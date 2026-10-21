# V4 — Going to the source: per-indicator origin review + free-API upgrade plan

**Date:** 2026-08-20 · **Trigger:** Bruce's "use the source whenever possible" (the CPI 3.3/3.4
incident, then the Brent spot-vs-futures question) · **Constraint:** free only — no card, no paid
tier, ever (SO9). · **Status:** PLAN — nothing here is built yet except what is marked SHIPPED.

Every claim below was live-probed on 2026-08-19/20; probe evidence is quoted inline.

## The principle

FRED is a *re-publisher* for most of our rows. Where the originating agency has a free machine
endpoint, we graft its same-day value onto the FRED backbone (the `TreasuryYieldSource`/`BlsSource`
pattern: FRED keeps the history, the origin supplies the freshest tip, any failure degrades to
FRED). We never *replace* FRED — never worse than FRED-only is the invariant.

## Where each indicator actually comes from

| Indicator | FRED series | True origin | Free origin API? | Verdict |
|---|---|---|---|---|
| cpi_yoy, core_cpi_yoy | CPIAUCNS/CPILFENS | **BLS** | api.bls.gov v2 | ✅ **SHIPPED 2026-08-18** (BlsSource) |
| unemployment, payrolls, participation, mfg/constr. employment | UNRATE/PAYEMS/… | **BLS** | api.bls.gov v2 | ✅ **SHIPPED 2026-08-18** (BlsSource) |
| treasury_10y, treasury_2y | DGS10/DGS2 | **U.S. Treasury** | treasury.gov XML | ✅ **SHIPPED** (TreasuryYieldSource) |
| gold_spot | (synthetic) | gold-api.com | keyless tick + keyed history | ✅ **SHIPPED** (ADR-006) |
| **fed_funds** ⚠ core+classifier | DFF | **NY Fed** (EFFR, 9:00 ET for prior day) | `markets.newyorkfed.org/api/rates/unsecured/effr/last/N.json` — **keyless**, probed: 2026-08-18 = 3.63, byte-identical to our DFF value | **P1 graft** |
| **federal_debt_gdp** | GFDEBTN (quarterly) | **Treasury FiscalData** ("Debt to the Penny", DAILY) | `api.fiscaldata.treasury.gov/...(od/debt_to_penny)` — **keyless**, probed: 2026-08-18 total = $40.047T vs our quarterly $39.07T | **P1 graft — biggest visible win** (~$1T fresher) |
| **vix** | VIXCLS (T+1) | **Cboe** | `cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv` — **keyless**, probed: has 08/18 close 15.84 while our row read 08/17's 15.19 | **P1 graft** (one day fresher) |
| **tips_10y_real** | DFII10 | **U.S. Treasury** (real yield curve) | same treasury.gov XML family, `data=daily_treasury_real_yield_curve` — **keyless**, probed OK | **P1 graft** (trivial: extend TreasuryYieldSource) |
| **brent_oil** | DCOILBRENTEU | **EIA** (Europe Brent spot FOB, RBRTE) | `api.eia.gov/v2/petroleum/pri/spt/...RBRTE` — **free key, no card** (gov registration); probed: route valid, key-gated | **P2 graft + relabel** (see the spot-vs-futures note) |
| **mortgage_30y** | MORTGAGE30US | **Freddie Mac** (PMMS, Thursdays) | `freddiemac.com/pmms/docs/PMMS_history.csv` — **keyless**, probed OK (weekly since 1971) | **P2 graft** (origin; freshness gain small) |
| **real_gdp_yoy** ⚠ core+classifier | GDPC1 | **BEA** (NIPA, 08:30 ET releases) | `apps.bea.gov/api` — free key, no card (gov) | **P3 graft** (release-morning freshness, ~monthly estimate cycle) |
| **housing_starts** | HOUST | **Census** (New Residential Construction) | `api.census.gov/data/timeseries/eits/resconst` — free key, no card (gov); probed: key-gated | **P3** (display-only, monthly; small gain) |
| **jobless_claims** | ICSA | **DOL/ETA** (Thursday 08:30) | `oui.doleta.gov/unemploy/csv/ar539.csv` is keyless but **state-level NSA** — not the national SA headline; DOL API v4 (free key) unverified for ICSA | **P3 investigate** — do NOT build from ar539 (wrong convention) |
| umich_sentiment | UMCSENT | **UM ISR** | tables only, no API | **Defer** (scraper = fragile; assessed 2026-08-18) |
| sp500, nasdaq | SP500/NASDAQCOM | S&P DJI / Nasdaq (licensed) | none free-and-licensed | **No change** — free scrapes (yahoo/stooq) are ToS-gray; not for a client product |
| hy_spread | BAMLH0A0HYM2 | ICE BofA (licensed) | none | **No change** |
| capacity_util, market_cap, usd_index | TCU/NCBEILQ027S/DTWEXBGS | **Federal Reserve Board** (G.17/Z.1/H.10) | FRED *is* the Fed's dissemination arm | **Already at source** |
| fed funds *target* / GDP deflator etc. | — | — | — | not in the registry |

## The Brent spot-vs-futures answer (why we differed from Google, 2026-08-19)

Our row is EIA's **spot** assessment (Aug 18 = 95.29, reconciliation `matched`); news sites quote
**futures** (a thin NYMEX contract showed 87.27). No free, licensed futures feed exists (ICE/CME
data is licensed), so within the $0 constraint the honest moves are: (a) graft EIA-direct so the
spot is as fresh as the origin allows, and (b) **relabel the row** so it self-explains:
display_name "Brent spot", long_name "EIA Europe Brent spot price FOB — publishes ~1 business day
behind; differs from the futures quote news sites show". Futures stays an evaluate-later item if a
licensed free source ever appears.

## Integration strategy (all four phases follow the shipped pattern)

One decorator per origin, composed in `sources.build_source()`; never-raise; graft only when
strictly newer; seasonal/unit convention matched to the FRED backbone; conftest fake + tests per
source; `record_source` degradation rows. Each graft adds ≤2 HTTP/run — no ceiling moves (FRED
stays the binding dependency at ~17% of its hard stop).

- **P1 — ✅ SHIPPED 2026-08-20 (all four, keyless):** `FiscalDataSource` (federal debt daily graft — decide
  delta semantics: day-over-day debt change is meaningful; note detail-sheet history stays FRED
  quarterly unless we page the API), extend `TreasuryYieldSource` with the real-yield feed
  (DFII10), `CboeSource` (VIX T+0 close), `NyFedSource` (EFFR tip). ⚠ fed_funds is core + a
  classifier input: EFFR is *the same number* as DFF (verified 3.63 == 3.63), so no re-backtest —
  but add a test asserting recent-history equality FRED-vs-NYFed before shipping, and get the
  standing (non-strict) gate green.
- **P2 — ✅ SHIPPED 2026-08-20:** `EiaSource` (RBRTE graft + the Brent relabel; EIA_API_KEY),
  `PmmsSource` (keyless CSV actually — no key needed).
- **P3 — ✅ SHIPPED 2026-08-20 (key-optional; pure no-op until keys exist):** `BeaSource`
  (NIPA T10106/A191RX -> GDPC1 YoY, same convention, newer-quarter-only — insurance for late
  FRED ingest mornings; needs free BEA_API_KEY) and `CensusSource` (EITS resconst ASTARTS/TOTAL
  SA -> HOUST; CENSUS_API_KEY registered; facet codes verified live 2026-08-20 — the SAAR
  series is ASTARTS, and the initial STARTS guess degraded visibly exactly as designed). **DOL claims: CLOSED, no
  build** — DOL's v4 API carries no claims dataset (probed 2026-08-20, 10 datasets, none
  relevant) and the keyless ar539 file is state-level NSA, the wrong convention for ICSA;
  jobless_claims stays on FRED, where the Thursday lag is already immaterial to a 10:45 run.
- **Each phase:** new env keys documented in `.env.example` + workflow env blocks + GH secrets;
  Bruce note only if a displayed number's *convention* changes (none planned — unlike CPI, these
  are all same-number-fresher grafts, except the federal-debt cadence change, which he should hear
  about: quarterly → daily is a visible behavior change).

## What this does NOT do

No publish-time change (10:30 ET floor stands). No classifier threshold changes. No paid or
ToS-gray sources. No replacement of FRED as backbone/history anywhere.
