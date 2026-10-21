"""Staleness flagging — resolves Phase-0 build-time carry-forward #2.

Decision (grounded in regression case E4 "Stale data"): a value that is *plausible but
old* is NOT rejected and does NOT short-circuit to the cached prior briefing. The
pipeline passes it through to the LLM with its real (stale) period_label — the prompt
must narrate the actual period, never fabricate a fresh date — and the pipeline emits a
`stale` flag per indicator + an `any_stale` rollup for the UI freshness banner (D7),
which is shown separately from the briefing text.

Only *implausible* values (plausibility.py / case A1) cause a refusal. Staleness is
purely advisory metadata.
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from ._dates import parse_iso
from .config import INDICATORS_BY_ID
from .models import IndicatorReading


def assess(reading: IndicatorReading, as_of: date) -> tuple[bool, Optional[int]]:
    """Return (is_stale, days_since_last_release) for one reading.

    A missing/unparseable last_release_date must never crash the daily run — treat it as
    not-stale with unknown age rather than letting ValueError propagate.
    """
    spec = INDICATORS_BY_ID[reading.id]
    try:
        days = (as_of - parse_iso(reading.last_release_date)).days
    except (ValueError, TypeError):
        return False, None
    return days > spec.stale_after_days, days


def apply(readings: list[IndicatorReading], as_of: date) -> bool:
    """Annotate readings in place with stale/staleness_days. Returns any_stale.

    V2: every reading still gets its own `stale` flag (for the per-row UI), but the dashboard-wide
    `any_stale` banner rolls up CORE-tier only — a naturally-slow display indicator (e.g. quarterly
    debt-to-GDP aging past its cadence) must NOT amber the whole dashboard when the core is fresh.
    """
    any_stale = False
    for r in readings:
        stale, days = assess(r, as_of)
        r.stale = stale
        r.staleness_days = days
        if r.tier == "core":
            any_stale = any_stale or stale
    return any_stale
