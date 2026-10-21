"""Deterministic regime classifier (B2) — LOCKED v1.2.

Rules-only, fully explainable, no model. The LLM later *narrates* this label; it never
re-classifies. Consumes the B1 `IndicatorReading`s (needs `value` + `delta_3m`) and
emits a regime label + up to 2 driver indicator ids.

v1.2 (D9 sign-off 2026-08-07, Bruce, via the three-item sign-off item 3 —
docs/feedback/2026-08-08-bruce-signoff-request.md) adds W3 on top of v1.1, thresholds unchanged:
- missing core/gdp/unemp -> `indeterminate` (rule="missing_data") instead of reaching the else;
- an unmatched reading set -> `indeterminate` (rule="unmatched") instead of soft_landing.
Back-test evidence: 13 of 144 months (25% of soft_landing) had been the silent default; every
remaining soft_landing month is a genuine rule match. His words, quoted at sign-off: "Why are
you calling it a soft landing when there is no reason to say that."

v1.1 (signed off via D9 re-sign-off, 2026-06-17) supersedes the v1.0 thresholds after the
carry-forward #1 back-test, with two changes vs v1.0:
- Fix A: hot inflation (core ≥ 3.0) never falls through to soft_landing → late_cycle_expansion.
- Fix B: recovery uses hysteresis (enter Δ3m unemployment ≤ −0.3, stay until Δ3m > +0.1),
  which needs `prev_regime` (the pipeline passes yesterday's cached label).
See docs/classifier/regime-classifier-v1.1-proposal.md for the evidence + before/after.

NOTE: the D10 regression fixtures carry a pre-set `regime_today` as a *narration* input
for the LLM/validator — they are not the classifier's assertion targets.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .models import IndicatorReading

# Thresholds (v1.1 — D9 re-sign-off; still tunable via O3 quarterly recal).
INFLATION_HOT = 3.0
INFLATION_NEAR_TARGET = 2.5
GROWTH_STRONG = 2.5
GROWTH_MODERATE_MIN = 1.0
LABOR_TIGHT = 4.0
LABOR_LOOSE_LEVEL = 5.0
LABOR_LOOSE_TREND = 0.3      # Δ3m unemployment ≥ +0.3 == loosening
RECOVERY_ENTER_TREND = -0.3  # Fix B: need a clear fall to ENTER recovery
RECOVERY_STAY_TREND = 0.1    # Fix B: once in recovery, STAY until Δ3m rises past +0.1


@dataclass
class ClassifierResult:
    regime: str
    drivers: list[str] = field(default_factory=list)  # ≤2 indicator ids
    # W3: which required inputs were absent, when rule == "missing_data". Empty otherwise.
    missing: list[str] = field(default_factory=list)
    # W1 (Bruce D+1, 2026-08-06). He asked "why are you calling it a soft landing when there is
    # no reason to say that." There IS a reason — `gdp >= 1.0 AND core < 3.0 AND unemp <= 5.0` —
    # but the card never showed it, so the label read as an editorial opinion rather than a
    # tolerance band that a rule reported. These two fields carry the answer to the surface.
    #
    # PURELY ADDITIVE: no branch condition changes, so no reading set maps to a different label
    # than it did before. `rule` names the branch that fired; "fallthrough" is the catch-all.
    rule: str = ""
    # (indicator_id, observed, comparator, threshold) for each clause the branch tested.
    basis: list[tuple[str, float, str, float]] = field(default_factory=list)


@dataclass
class _Signals:
    core: Optional[float]
    gdp: Optional[float]
    unemp: Optional[float]
    unemp_d3m: Optional[float]
    yield_curve: Optional[float]
    umich_delta: Optional[float]
    values: dict[str, Optional[float]]


@dataclass
class _Flags:
    inflation_hot: bool
    core_below_hot: bool
    core_ge_near: bool
    growth_strong: bool
    growth_moderate: bool
    growth_weak: bool
    growth_ge_1: bool
    labor_tight: bool
    labor_loosening: bool
    curve_inverted: bool
    unemp_le_5: bool


def _signals(readings: list[IndicatorReading]) -> _Signals:
    by_id = {r.id: r for r in readings}

    def val(i: str) -> Optional[float]:
        r = by_id.get(i)
        return r.value if r else None

    t10, ff = val("treasury_10y"), val("fed_funds")
    yield_curve = (t10 - ff) if (t10 is not None and ff is not None) else None
    umich = by_id.get("umich_sentiment")
    return _Signals(
        core=val("core_cpi_yoy"),
        gdp=val("real_gdp_yoy"),
        unemp=val("unemployment"),
        unemp_d3m=by_id["unemployment"].delta_3m if "unemployment" in by_id else None,
        yield_curve=yield_curve,
        umich_delta=umich.delta if umich else None,
        values={r.id: r.value for r in readings},
    )


def _flags(s: _Signals) -> _Flags:
    return _Flags(
        inflation_hot=s.core is not None and s.core >= INFLATION_HOT,
        core_below_hot=s.core is not None and s.core < INFLATION_HOT,
        core_ge_near=s.core is not None and s.core >= INFLATION_NEAR_TARGET,
        growth_strong=s.gdp is not None and s.gdp >= GROWTH_STRONG,
        growth_moderate=s.gdp is not None and GROWTH_MODERATE_MIN <= s.gdp < GROWTH_STRONG,
        growth_weak=s.gdp is not None and s.gdp < GROWTH_MODERATE_MIN,
        growth_ge_1=s.gdp is not None and s.gdp >= GROWTH_MODERATE_MIN,
        labor_tight=s.unemp is not None and s.unemp <= LABOR_TIGHT,
        labor_loosening=(s.unemp_d3m is not None and s.unemp_d3m >= LABOR_LOOSE_TREND)
        or (s.unemp is not None and s.unemp > LABOR_LOOSE_LEVEL),
        curve_inverted=s.yield_curve is not None and s.yield_curve < 0,
        unemp_le_5=s.unemp is not None and s.unemp <= LABOR_LOOSE_LEVEL,
    )


def _c(indicator_id: str, observed: Optional[float], op: str,
       threshold: float) -> Optional[tuple[str, float, str, float]]:
    """One clause of the basis, or None when the value is missing.

    A branch can fire on flags that are individually satisfiable while one input is None (the
    recovery/late-cycle disjunctions), so a clause with nothing observed is dropped rather than
    rendered as `None <= 5.0`. Filtering here keeps the display layer from needing to know that.
    """
    return None if observed is None else (indicator_id, round(observed, 4), op, threshold)


def classify(readings: list[IndicatorReading], prev_regime: Optional[str] = None) -> ClassifierResult:
    """Classify the macro regime (locked v1.2, first match wins).

    `prev_regime` is yesterday's label (from the cache); it powers the recovery
    hysteresis (Fix B). Omit it for a stateless single-shot classification.
    """
    s = _signals(readings)
    f = _flags(s)

    # W3 (Bruce D+1, D9). MISSING DATA IS A PRECONDITION FAILURE, not a regime. Every branch below
    # tests values that may be None, and plausibility.py returns True for None by design, so an
    # all-None reading set used to sail through to the `else` and publish "Soft landing" — the most
    # reassuring label in the set, on no data at all.
    if s.core is None or s.gdp is None or s.unemp is None:
        missing = [n for n, v in (("core_cpi_yoy", s.core), ("real_gdp_yoy", s.gdp),
                                  ("unemployment", s.unemp)) if v is None]
        return ClassifierResult(regime="indeterminate", drivers=[], rule="missing_data",
                                basis=[], missing=missing)

    recovery_enter = (
        f.growth_moderate and f.core_below_hot
        and s.unemp_d3m is not None and s.unemp_d3m <= RECOVERY_ENTER_TREND
    )
    recovery_stay = (
        prev_regime == "recovery" and f.growth_moderate and f.core_below_hot
        and s.unemp_d3m is not None and s.unemp_d3m <= RECOVERY_STAY_TREND
    )

    if f.inflation_hot and f.growth_weak:
        regime, cands, rule = "stagflation_risk", _stagflation_candidates(s), "stagflation_risk"
        basis = [_c("core_cpi_yoy", s.core, ">=", INFLATION_HOT),
                 _c("real_gdp_yoy", s.gdp, "<", GROWTH_MODERATE_MIN)]
    elif f.growth_weak and f.labor_loosening and f.core_below_hot:
        regime, cands, rule = "slowdown", _slowdown_candidates(s), "slowdown"
        basis = [_c("real_gdp_yoy", s.gdp, "<", GROWTH_MODERATE_MIN),
                 _c("unemployment", s.unemp_d3m, ">=", LABOR_LOOSE_TREND),
                 _c("core_cpi_yoy", s.core, "<", INFLATION_HOT)]
    elif recovery_enter or recovery_stay:
        regime, cands, rule = "recovery", _recovery_candidates(s), (
            "recovery_enter" if recovery_enter else "recovery_stay")
        basis = [_c("unemployment", s.unemp_d3m, "<=",
                    RECOVERY_ENTER_TREND if recovery_enter else RECOVERY_STAY_TREND),
                 _c("core_cpi_yoy", s.core, "<", INFLATION_HOT)]
    elif f.growth_strong and f.labor_tight and (f.core_ge_near or f.curve_inverted):
        regime, cands, rule = "late_cycle_expansion", _late_cycle_candidates(s, f), "late_cycle"
        basis = [_c("real_gdp_yoy", s.gdp, ">=", GROWTH_STRONG),
                 _c("unemployment", s.unemp, "<=", LABOR_TIGHT)]
        basis.append(_c("core_cpi_yoy", s.core, ">=", INFLATION_NEAR_TARGET) if f.core_ge_near
                     else _c("yield_curve_2s10s", s.yield_curve, "<", 0.0))
    elif f.growth_ge_1 and f.core_below_hot and f.unemp_le_5:
        regime, cands, rule = "soft_landing", _soft_landing_candidates(s), "soft_landing"
        basis = [_c("real_gdp_yoy", s.gdp, ">=", GROWTH_MODERATE_MIN),
                 _c("core_cpi_yoy", s.core, "<", INFLATION_HOT),
                 _c("unemployment", s.unemp, "<=", LABOR_LOOSE_LEVEL)]
    elif f.inflation_hot:  # Fix A — hot inflation is never a "soft landing".
        regime, cands, rule = "late_cycle_expansion", _hot_default_candidates(s), "hot_default"
        basis = [_c("core_cpi_yoy", s.core, ">=", INFLATION_HOT)]
    else:
        # W3: a genuinely unmatched reading set is `indeterminate`, not the most benign of five.
        # "The rules do not describe this economy" is true and defensible; picking soft_landing
        # was neither — GDP -19% published "Soft landing" for as long as this branch existed, and
        # 13 of 53 soft_landing months over 12 years came through here.
        #
        # `indeterminate` is a STATUS, not a regime: it says the classifier declined, which keeps
        # it out of the O3 threshold conversation entirely.
        regime, cands, rule = "indeterminate", [], "unmatched"
        basis = []

    return ClassifierResult(regime=regime, drivers=_rank_drivers(cands, s.values),
                            rule=rule, basis=[b for b in basis if b is not None])


# --- driver candidate lists (id, distance-past-threshold) -------------------------

def _stagflation_candidates(s: _Signals) -> list[tuple[str, float]]:
    return [("core_cpi_yoy", _past(s.core, INFLATION_HOT, "above")),
            ("real_gdp_yoy", _past(s.gdp, GROWTH_MODERATE_MIN, "below"))]


def _slowdown_candidates(s: _Signals) -> list[tuple[str, float]]:
    return [("real_gdp_yoy", _past(s.gdp, GROWTH_MODERATE_MIN, "below")),
            ("unemployment", _labor_loose_distance(s))]


def _recovery_candidates(s: _Signals) -> list[tuple[str, float]]:
    # Falling unemployment is recovery's defining signal — keep it the primary driver
    # (a +1.0 floor so a "staying" recovery with small positive Δ3m can't drop it).
    return [("unemployment", abs(s.unemp_d3m) + 1.0 if s.unemp_d3m is not None else 1.0),
            ("real_gdp_yoy", _past(s.gdp, GROWTH_MODERATE_MIN, "above")),
            ("umich_sentiment", abs(s.umich_delta) if s.umich_delta is not None else 0.0)]


def _late_cycle_candidates(s: _Signals, f: _Flags) -> list[tuple[str, float]]:
    return [("real_gdp_yoy", _past(s.gdp, GROWTH_STRONG, "above")),
            ("treasury_10y", -s.yield_curve if f.curve_inverted else 0.0),
            ("core_cpi_yoy", _past(s.core, INFLATION_NEAR_TARGET, "above") if f.core_ge_near else 0.0)]


def _hot_default_candidates(s: _Signals) -> list[tuple[str, float]]:
    return [("core_cpi_yoy", _past(s.core, INFLATION_HOT, "above")),
            ("real_gdp_yoy", _past(s.gdp, GROWTH_STRONG, "below"))]


def _soft_landing_candidates(s: _Signals) -> list[tuple[str, float]]:
    return [("core_cpi_yoy", _past(s.core, INFLATION_HOT, "below")),
            ("unemployment", _past(s.unemp, LABOR_LOOSE_LEVEL, "below"))]


def _past(value: Optional[float], threshold: float, direction: str) -> float:
    """Magnitude a value sits past a threshold (0 if on the wrong side / missing)."""
    if value is None:
        return 0.0
    diff = (value - threshold) if direction == "above" else (threshold - value)
    return max(diff, 0.0)


def _labor_loose_distance(s: _Signals) -> float:
    by_level = (s.unemp - LABOR_LOOSE_LEVEL) if s.unemp is not None else None
    by_trend = (s.unemp_d3m - LABOR_LOOSE_TREND) if s.unemp_d3m is not None else None
    return max((d for d in (by_level, by_trend) if d is not None), default=0.0)


def _rank_drivers(candidates: list[tuple[str, float]], values: dict[str, Optional[float]]) -> list[str]:
    """Rank binding indicators by distance past threshold; drop any with a null value
    (never cite a missing indicator — E1); cap at 2."""
    ranked = sorted(
        (c for c in candidates if values.get(c[0]) is not None),
        key=lambda c: c[1],
        reverse=True,
    )
    return [ind_id for ind_id, _ in ranked[:2]]
