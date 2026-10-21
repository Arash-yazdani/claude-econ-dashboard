"""Canonical indicator registry: the 7 V1 indicators → FRED series + metadata.

The indicator *ids* (cpi_yoy, core_cpi_yoy, ...) are the contract locked in
docs/schemas/briefing-io-v1.0.md and the regression fixtures. The FRED series_ids
and `units` transforms below were NOT pinned in Phase 0 — they are defined here as
part of B1 (the "FRED ingest" deliverable). Thresholds/ranges trace to D8/D9.

`units` uses FRED's built-in transform so the API returns the value we actually
brief on, avoiding hand-rolled YoY math:
  - "lin"  → level, as published (UNRATE, DGS10, DFF, UMCSENT)
  - "pc1"  → percent change from a year ago (CPIAUCNS, CPILFENS, GDPC1) = YoY
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class Frequency(str, Enum):
    DAILY = "daily"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"


@dataclass(frozen=True)
class IndicatorSpec:
    id: str
    fred_series_id: str
    units: str  # FRED transform: "lin" | "pc1"
    frequency: Frequency
    display_name: str
    unit: str  # output contract unit: "%" | "index" | "USD"
    # Pre-LLM plausibility gate (docs/schemas/briefing-io-v1.0.md). A value outside
    # [min, max] => reject, skip the LLM, render the deterministic fallback.
    plausible_min: float
    plausible_max: float
    # Days after which a still-plausible value is considered STALE (surfaced for the
    # UI freshness banner — NOT a refusal; see staleness.py / regression case E4).
    stale_after_days: int
    # V2 tiering (docs/v2-indicator-expansion.md): "core" feeds the LLM briefing + regime and
    # hard-gates plausibility; "drawer" + "market" are DISPLAY-ONLY (PWA rows), never enter the
    # briefing_request, and an implausible value drops to "—" without failing the run. "market"
    # additionally carries the ADR-003 no-securities-advice posture.
    tier: str = "core"
    # Override for a non-FRED source (e.g. gold_spot's gold-api.com) — the PWA's "View source"
    # link and build_reading() both fall back to the FRED series page when this is None.
    source_url: Optional[str] = None
    # Full official series title (e.g. FRED's own name for the series) — shown in the PWA detail
    # sheet only; display_name stays short so the compact row list doesn't wrap. None -> the
    # detail sheet just falls back to display_name.
    long_name: Optional[str] = None
    # True when the source is a CONTINUOUSLY MOVING live tick rather than a fixed daily print.
    # FRED publishes a value for a date and it stays put, so I2 source reconciliation can compare
    # it exactly. A live tick's value for "today" keeps changing all day, so an exact compare
    # flags normal market movement as an accuracy failure. Reconciliation uses a relative band
    # for these instead — still wide enough to catch the errors I2 exists for (wrong series id,
    # unit drift, stale cache all miss by orders of magnitude, not fractions of a percent).
    live_tick: bool = False

    @property
    def is_yoy(self) -> bool:
        return self.units == "pc1"


# Order here is the canonical dashboard/briefing order.
INDICATORS: tuple[IndicatorSpec, ...] = (
    # CPI uses the NSA series (CPIAUCNS / CPILFENS), not the seasonally-adjusted CPIAUCSL/CPILFESL:
    # BLS's headline over-the-year change — the number the news quotes — is the NSA figure. The SA
    # series read 3.30% for Jul 2026 while the published headline was 3.4% (NSA 3.36%), which Bruce
    # flagged ("use the source"). BlsSource grafts the matching NSA tip (CUUR...) for 08:30 freshness.
    # NOTE: core_cpi_yoy is a classifier input (INFLATION_HOT/NEAR_TARGET) — this NSA switch is gated
    # on a fresh backtest_thresholds.py run + D9 sign-off before it may reach main.
    IndicatorSpec("cpi_yoy", "CPIAUCNS", "pc1", Frequency.MONTHLY,
                  "CPI", "%", 0.0, 10.0, stale_after_days=45),
    IndicatorSpec("core_cpi_yoy", "CPILFENS", "pc1", Frequency.MONTHLY,
                  "Core CPI", "%", 0.0, 10.0, stale_after_days=45),
    IndicatorSpec("unemployment", "UNRATE", "lin", Frequency.MONTHLY,
                  "Unemployment", "%", 0.0, 30.0, stale_after_days=45),
    IndicatorSpec("real_gdp_yoy", "GDPC1", "pc1", Frequency.QUARTERLY,
                  "Real GDP", "%", -10.0, 10.0, stale_after_days=120,
                  # V4 P3: BeaSource grafts the release-morning print; link goes to the origin.
                  source_url="https://www.bea.gov/data/gdp/gross-domestic-product"),
    # V4: yield/rate rows grafted from their origin link "View source" there, not to FRED's mirror.
    IndicatorSpec("treasury_10y", "DGS10", "lin", Frequency.DAILY,
                  "10-Year Treasury", "%", 0.0, 20.0, stale_after_days=7,
                  source_url="https://home.treasury.gov/policy-issues/financing-the-government/interest-rate-statistics"),
    IndicatorSpec("fed_funds", "DFF", "lin", Frequency.DAILY,
                  "Fed Funds", "%", 0.0, 25.0, stale_after_days=7,
                  source_url="https://www.newyorkfed.org/markets/reference-rates/effr"),
    IndicatorSpec("umich_sentiment", "UMCSENT", "lin", Frequency.MONTHLY,
                  "U-Mich Sentiment", "index", 0.0, 150.0, stale_after_days=45),
    # V2 Tier 2 — macro drawer (display-only; not in the briefing/regime). All free FRED.
    IndicatorSpec("payrolls", "PAYEMS", "chg", Frequency.MONTHLY,
                  "Payrolls (net)", "K", -25000.0, 25000.0, stale_after_days=45, tier="drawer",
                  long_name="Payrolls (net, in thousands)"),
    IndicatorSpec("treasury_2y", "DGS2", "lin", Frequency.DAILY,
                  "2-Year Treasury", "%", 0.0, 20.0, stale_after_days=7, tier="drawer",
                  source_url="https://home.treasury.gov/policy-issues/financing-the-government/interest-rate-statistics"),
    IndicatorSpec("jobless_claims", "ICSA", "lin", Frequency.DAILY,
                  "Jobless claims", "count", 100_000.0, 10_000_000.0, stale_after_days=14, tier="drawer"),
    IndicatorSpec("labor_participation", "CIVPART", "lin", Frequency.MONTHLY,
                  "Labor participation", "%", 55.0, 70.0, stale_after_days=45, tier="drawer"),
    IndicatorSpec("mortgage_30y", "MORTGAGE30US", "lin", Frequency.DAILY,
                  "30-Yr mortgage", "%", 0.0, 20.0, stale_after_days=14, tier="drawer",
                  # V4 P2: PmmsSource grafts Freddie's weekly print; link goes to the origin.
                  source_url="https://www.freddiemac.com/pmms",
                  long_name="30-Year Fixed Rate Mortgage Average (Freddie Mac PMMS, weekly)"),
    IndicatorSpec("housing_starts", "HOUST", "lin", Frequency.MONTHLY,
                  "Housing starts", "K", 200.0, 4000.0, stale_after_days=45, tier="drawer",
                  # V4 P3: CensusSource grafts the origin print; link goes to Census NRC.
                  source_url="https://www.census.gov/construction/nrc/index.html"),
    # GFDEBTN is the raw dollar level (Millions of Dollars, NSA, quarterly) — user decision
    # 2026-07-22, replacing the earlier %-of-GDP series (GFDEGDQ188S). "usd_tn" matches
    # US-market-cap's convention: raw value already in millions, fmtValue divides by 1e6 -> $T.
    IndicatorSpec("federal_debt_gdp", "GFDEBTN", "lin", Frequency.QUARTERLY,
                  "Federal Debt", "usd_tn", 10_000_000.0, 200_000_000.0,
                  stale_after_days=120, tier="drawer",
                  # V4 P1: FiscalDataSource grafts the DAILY "Debt to the Penny" figure onto the
                  # quarterly FRED backbone; the link goes to the origin dataset.
                  source_url="https://fiscaldata.treasury.gov/datasets/debt-to-the-penny/",
                  long_name="Federal Debt: Total Public Debt"),
    # V3 — sector + rates drawer rows (all free FRED, all `lin`, probed live 2026-06-19).
    IndicatorSpec("tips_10y_real", "DFII10", "lin", Frequency.DAILY,
                  "10Y TIPS yield", "%", -5.0, 10.0, stale_after_days=7, tier="drawer",
                  source_url="https://home.treasury.gov/policy-issues/financing-the-government/interest-rate-statistics"),
    IndicatorSpec("capacity_util", "TCU", "lin", Frequency.MONTHLY,
                  "Capacity utilization", "%", 50.0, 95.0, stale_after_days=45, tier="drawer"),
    IndicatorSpec("mfg_employment", "MANEMP", "lin", Frequency.MONTHLY,
                  "Mfg. employment", "K", 5_000.0, 25_000.0, stale_after_days=45, tier="drawer"),
    IndicatorSpec("construction_employment", "USCONS", "lin", Frequency.MONTHLY,
                  "Construction emp.", "K", 3_000.0, 12_000.0, stale_after_days=45, tier="drawer"),
    # V2 Tier 3 — markets strip (DISPLAY-ONLY; excluded from the LLM briefing per ADR-003 / SO7′).
    IndicatorSpec("sp500", "SP500", "lin", Frequency.DAILY,
                  "S&P 500", "index", 0.0, 100_000.0, stale_after_days=7, tier="market"),
    IndicatorSpec("nasdaq", "NASDAQCOM", "lin", Frequency.DAILY,
                  "Nasdaq", "index", 0.0, 100_000.0, stale_after_days=7, tier="market"),
    # NCBEILQ027S = market value of US corporate equities (the "Buffett indicator" numerator),
    # in $millions, QUARTERLY. Wilshire's daily total-market index was discontinued on FRED, so
    # this macro aggregate is the total-market-cap proxy (user decision 2026-06-18). "usd_tn" → $T.
    IndicatorSpec("market_cap", "NCBEILQ027S", "lin", Frequency.QUARTERLY,
                  "US market cap", "usd_tn", 1_000_000.0, 500_000_000.0, stale_after_days=120, tier="market"),
    IndicatorSpec("hy_spread", "BAMLH0A0HYM2", "lin", Frequency.DAILY,
                  "HY credit spread", "%", 0.0, 50.0, stale_after_days=7, tier="market"),
    # V2.1 — CBOE volatility index (display-only, Charter Rev 3 / SCRUM-77).
    IndicatorSpec("vix", "VIXCLS", "lin", Frequency.DAILY,
                  "VIX volatility", "index", 5.0, 150.0, stale_after_days=7, tier="market",
                  source_url="https://www.cboe.com/tradable-products/vix"),
    # V3 — international + commodities (display-only markets strip; probed live 2026-06-19). Gold was
    # dropped — FRED's LBMA $/oz series (GOLDAMGBD228NLBM) is discontinued and the only fallback is an
    # ambiguous index, so no clean gold price was available.
    IndicatorSpec("usd_index", "DTWEXBGS", "lin", Frequency.DAILY,
                  "US Dollar index", "index", 80.0, 160.0, stale_after_days=7, tier="market"),
    # V2.1 — swapped the prior domestic crude benchmark for Brent (CEO-approved, Charter Rev 3 /
    # SCRUM-77): Brent is the more widely quoted international benchmark.
    # V4 P2 — relabeled "Brent spot" + linked to the EIA origin: our number is EIA's Europe Brent
    # SPOT assessment (grafted same-day by EiaSource when EIA_API_KEY is set), which is NOT the
    # futures quote news sites show — the label + long_name make that difference self-explaining
    # (the 2026-08-19 spot-95/futures-87 question). Prompt metadata (_CONTEXT_META/_PHRASING)
    # deliberately keeps "Brent crude oil": renaming there is a prompt change and re-opens SO8.
    IndicatorSpec("brent_oil", "DCOILBRENTEU", "lin", Frequency.DAILY,
                  "Brent spot", "USD", 0.0, 400.0, stale_after_days=7, tier="market",
                  source_url="https://www.eia.gov/dnav/pet/hist/rbrted.htm",
                  long_name="Europe Brent Spot Price FOB (EIA) — the physical cargo assessment, "
                            "published ~1 business day behind; differs from the futures quote "
                            "shown by news sites"),
    # V2.2 — real gold spot price via gold-api.com (ADR-006, CEO-approved 2026-07-20). Not on FRED
    # (its gold series is discontinued — see the note by usd_index above). fred_series_id is a
    # SYNTHETIC id (gold_client.GOLD_SERIES_ID) matched by GoldSpotSource, never sent to FRED.
    # Current-value-only (no history endpoint on the free tier) — see gold_client.py. Never fed to
    # the writer (not in BRIEFING_CONTEXT_IDS): a commodity spot price, kept display-only like oil.
    IndicatorSpec("gold_spot", "XAU_SPOT_GOLDAPI", "lin", Frequency.DAILY,
                  "Gold (spot)", "USD", 200.0, 20_000.0, stale_after_days=3, tier="market",
                  source_url="https://gold-api.com", live_tick=True),
)

INDICATORS_BY_ID: dict[str, IndicatorSpec] = {spec.id: spec for spec in INDICATORS}

# V3 — a curated handful of display-tier indicators the LLM may reference as CONTEXT in the
# narrative (commodity/FX/rate), in addition to the 7 core. These are NOT promoted to core display
# rows; they only widen the writer's evidence base. yield_curve_2s10s is a DERIVED row (built in
# derived.py, not an INDICATORS spec), included here by id. Pending Bruce sign-off — this deviates
# from ADR-003/SO7′'s blanket "market tier excluded from the LLM", so the no-securities-advice
# validator (O8 banned phrases) still hard-blocks any buy/sell/recommend language.
BRIEFING_CONTEXT_IDS: tuple[str, ...] = ("yield_curve_2s10s", "brent_oil", "usd_index")

# How many observations to retain per series in the cache. Δ3m needs a ~3-month
# lookback; for daily series that is ~65 business days. 70 keeps history bounded
# (total cache stays well under the 5MB cap) while covering every cadence.
CACHE_HISTORY_LEN = 70

# V2 detail view: how many observations to pull for the pre-baked chart history. ~2600 covers
# ~10y of a daily series (252 trading days/yr); shorter-cadence series return all they have. The
# response is downsampled to ~220 points before publishing (models.IndicatorReading.history), so
# this only widens the FRED response — it never adds a FRED *call*.
HISTORY_LEN = 2600

# FRED REST API + public series page (the detail view's "View source" link-out).
FRED_BASE_URL = "https://api.stlouisfed.org/fred"
FRED_SERIES_PAGE = "https://fred.stlouisfed.org/series"
FRED_API_KEY_ENV = "FRED_API_KEY"

# The SEVEN BLS-origin indicators: FRED backbone series id -> BLS series id. Single source of truth,
# used two ways so they can never drift apart: bls_client grafts BLS's same-day tip through this map
# (seasonal adjustment matched per series), and pipeline.build_reading points each row's "View
# source" link at the BLS series page (data.bls.gov) instead of FRED's mirror — "use the source"
# (Bruce 2026-08-18). CPI/core use the NSA series the headline quotes; the jobs numbers are SA.
BLS_SERIES: dict[str, str] = {
    "CPIAUCNS": "CUUR0000SA0",      # CPI-U all items, NSA (the headline over-the-year number)
    "CPILFENS": "CUUR0000SA0L1E",   # CPI-U less food & energy, NSA
    "UNRATE":   "LNS14000000",      # unemployment rate, SA
    "PAYEMS":   "CES0000000001",    # total nonfarm employment, SA (thousands)
    "CIVPART":  "LNS11300000",      # labor force participation rate, SA
    "MANEMP":   "CES3000000001",    # manufacturing employment, SA (thousands)
    "USCONS":   "CES2000000001",    # construction employment, SA (thousands)
}
# BLS's public series viewer: data.bls.gov/timeseries/<seriesID> opens the chart + data table.
BLS_SERIES_PAGE = "https://data.bls.gov/timeseries"
