"""Pre-LLM plausibility gate (docs/schemas/briefing-io-v1.0.md).

A NON-NULL value outside its spec range => reject the whole run, skip the LLM, render
the deterministic fallback (regression case A1). A NULL value is *not* a failure —
"data unavailable" is a valid state the briefing narrates around (regression case E1).

This is distinct from staleness: an old-but-in-range value is plausible (E4) and passes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .config import INDICATORS_BY_ID
from .models import IndicatorReading


@dataclass
class PlausibilityResult:
    ok: bool
    failures: list[dict[str, Any]] = field(default_factory=list)


def is_plausible(value: float | None, indicator_id: str) -> bool:
    """True if value is missing (null) or within the indicator's plausible range."""
    if value is None:
        return True
    spec = INDICATORS_BY_ID[indicator_id]
    return spec.plausible_min <= value <= spec.plausible_max


def validate(readings: list[IndicatorReading]) -> PlausibilityResult:
    """Hard-gate the run on CORE-tier plausibility only. Drawer/market (display-only, V2) are
    excluded from the gate — an out-of-range display value is dropped to "—" in the pipeline,
    it does not blank Bruce's briefing (which depends only on the core tier)."""
    failures: list[dict[str, Any]] = []
    for r in readings:
        spec = INDICATORS_BY_ID[r.id]
        if spec.tier != "core":
            continue
        if not is_plausible(r.value, r.id):
            failures.append(
                {
                    "id": r.id,
                    "value": r.value,
                    "valid_range": [spec.plausible_min, spec.plausible_max],
                }
            )
    return PlausibilityResult(ok=not failures, failures=failures)
