"""V3: derived display-only "spread" rows computed from existing readings — NO new FRED calls.

These are indicator PAIRINGS (e.g. the 2s10s yield curve = 10Y − 2Y; the real fed-funds rate =
fed funds − CPI YoY). `pipeline.run_pipeline` appends them to the readings AFTER the config-keyed
passes — `plausibility.validate` and `staleness.apply` index `INDICATORS_BY_ID[r.id]` and would
KeyError on these ids — and after `cache.update_snapshot` (so they stay out of the cache snapshot
and release detection). They are non-core, so `briefing_request()` (core-only) never sees them;
`models.indicators_json()` serializes every reading, so they publish to the PWA automatically.

Cadence note: a spread of two DAILY series (2s10s) gets a real joined-by-date history + sparkline.
A spread mixing daily and monthly cadence (the real-rate rows) ships CURRENT-VALUE-ONLY
(`history=[]` → the PWA detail sheet won't open for it), avoiding date-alignment guesswork; a
forward-filled history is a deliberate later enhancement.
"""

from __future__ import annotations

from .models import IndicatorReading

# (id, display_name, minuend_id, subtrahend_id, tier[, source_url]). value = minuend − subtrahend.
# source_url (optional): the spread's own canonical page when one exists; else the row inherits the
# PRIMARY leg's link. 2s10s -> FRED's native T10Y2Y (evaluated 2026-08-21: its value matches ours
# exactly — 0.46 on 08-19 — but OUR row is equal-or-fresher because the legs carry the Treasury
# same-day grafts, so we keep computing and only link there). The real-rate rows deliberately have
# no native series: real_10y here is nominal-minus-CPI, a different concept from DFII10 (its own row).
DERIVED: tuple[tuple, ...] = (
    ("yield_curve_2s10s", "10Y–2Y spread", "treasury_10y", "treasury_2y", "drawer",
     "https://fred.stlouisfed.org/series/T10Y2Y"),
    ("real_fed_funds", "Real fed funds", "fed_funds", "cpi_yoy", "drawer"),
    ("real_10y", "Real 10Y yield", "treasury_10y", "cpi_yoy", "drawer"),
)


def _ffill_history(a: IndicatorReading, b: IndicatorReading) -> list[list]:
    """Join a DAILY minuend against a MONTHLY subtrahend by forward-fill → [[iso, a−b], …].

    Each daily date uses the most recent monthly value ON OR BEFORE it — which is exactly what
    "the real rate today" means: today's nominal rate minus the latest known inflation print.
    Days before the first monthly print produce no point (no guessing backwards). Both histories
    arrive oldest→newest, so one forward pass suffices.
    """
    bh = [(d, v) for d, v in (b.history or []) if v is not None]
    out: list[list] = []
    bi = -1  # index of the latest monthly print at or before the current daily date
    for d, av in (a.history or []):
        if av is None:
            continue
        while bi + 1 < len(bh) and bh[bi + 1][0] <= d:
            bi += 1
        if bi < 0:
            continue
        out.append([d, round(av - bh[bi][1], 4)])
    return out


def _spread_history(a: IndicatorReading, b: IndicatorReading) -> list[list]:
    """Join two oldest→newest [[iso, value], …] histories by EXACT date → [[iso, a−b], …].

    Only dates present in both (and non-null) produce a point, so it's robust to the two series'
    downsample grids drifting apart on the older tail; the freshest full-res points align.
    """
    bv = {d: v for d, v in (b.history or []) if v is not None}
    out: list[list] = []
    for d, av in (a.history or []):
        if av is None:
            continue
        bvv = bv.get(d)
        if bvv is not None:
            out.append([d, round(av - bvv, 4)])
    return out


def build_derived(readings: list[IndicatorReading]) -> list[IndicatorReading]:
    by_id = {r.id: r for r in readings}
    out: list[IndicatorReading] = []
    for did, name, a_id, b_id, tier, *rest in DERIVED:
        a, b = by_id.get(a_id), by_id.get(b_id)
        if a is None or b is None:
            continue
        value = None if (a.value is None or b.value is None) else round(a.value - b.value, 4)
        delta = None if (a.delta is None or b.delta is None) else round(a.delta - b.delta, 4)
        both_daily = a.frequency == "daily" and b.frequency == "daily"
        daily_vs_monthly = a.frequency == "daily" and b.frequency == "monthly"
        # daily−daily joins by exact date; daily−monthly forward-fills the monthly print (the
        # real-rate rows) so they chart and open a detail sheet like every other row.
        history = _spread_history(a, b) if both_daily else \
                  _ffill_history(a, b) if daily_vs_monthly else []
        spark = [v for _, v in history[-12:]] if history else []
        out.append(IndicatorReading(
            id=did, display_name=name, value=value, unit="%",
            period_label=a.period_label,
            last_release_date=max(a.last_release_date, b.last_release_date),
            observation_date=a.observation_date, delta=delta, delta_3m=None,
            stale=False, staleness_days=None, spark=spark, tier=tier,
            # "View source": the spread's own canonical page when DERIVED carries one (2s10s ->
            # T10Y2Y), else the PRIMARY leg's link (the minuend inherits its FRED/BLS/NYFed page).
            level=value, history=history, source_url=(rest[0] if rest else a.source_url),
            # a forward-filled real-rate row updates with its DAILY leg, so it reads as daily
            frequency=a.frequency if (both_daily or (daily_vs_monthly and history)) else "monthly",
        ))
    return out
