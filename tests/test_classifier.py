"""Deterministic regime classifier — exercises each rule branch + driver selection.

These assert the RULES (docs/classifier/regime-classifier-v1.0.md), not the D10
fixtures (whose regime_today is a pre-set LLM-narration input, not a classifier target).
"""

from __future__ import annotations

from macro_pipeline.classifier import classify
from macro_pipeline.models import REGIME_LABELS, IndicatorReading


def _r(ind_id, value, *, delta_3m=None, delta=None, unit="%"):
    return IndicatorReading(id=ind_id, display_name=ind_id, value=value, unit=unit,
                            period_label="p", last_release_date="2026-06-11",
                            delta_3m=delta_3m, delta=delta)


def _set(core, gdp, unemp, *, unemp_d3m=None, t10=4.25, ff=4.5, umich=67.5, umich_d=None):
    return [
        _r("cpi_yoy", 2.9),
        _r("core_cpi_yoy", core),
        _r("unemployment", unemp, delta_3m=unemp_d3m),
        _r("real_gdp_yoy", gdp),
        _r("treasury_10y", t10),
        _r("fed_funds", ff),
        _r("umich_sentiment", umich, delta=umich_d, unit="index"),
    ]


def test_stagflation_risk():
    res = classify(_set(core=3.4, gdp=0.8, unemp=4.2))
    assert res.regime == "stagflation_risk"
    assert set(res.drivers) == {"core_cpi_yoy", "real_gdp_yoy"}


def test_slowdown():
    res = classify(_set(core=2.0, gdp=0.5, unemp=5.5))
    assert res.regime == "slowdown"
    assert set(res.drivers) == {"real_gdp_yoy", "unemployment"}


def test_recovery():
    res = classify(_set(core=2.4, gdp=2.0, unemp=4.0, unemp_d3m=-0.3))
    assert res.regime == "recovery"
    assert len(res.drivers) <= 2
    assert "unemployment" in res.drivers


def test_late_cycle_expansion():
    res = classify(_set(core=2.6, gdp=2.8, unemp=3.8))
    assert res.regime == "late_cycle_expansion"
    assert "real_gdp_yoy" in res.drivers


def test_soft_landing_explicit_rule():
    res = classify(_set(core=2.4, gdp=2.0, unemp=4.5))
    assert res.regime == "soft_landing"


def test_hot_inflation_default_is_late_cycle():
    # v1.1 Fix A: core hot (3.5), growth neither weak nor strong -> never soft_landing.
    res = classify(_set(core=3.5, gdp=2.0, unemp=4.5))
    assert res.regime == "late_cycle_expansion"
    assert "core_cpi_yoy" in res.drivers


def test_recovery_hysteresis_holds_through_small_wobble():
    # v1.1 Fix B: Δ3m back to flat -> stateless drops out, but recovery is sticky.
    s = _set(core=2.2, gdp=2.0, unemp=4.5, unemp_d3m=0.0)
    assert classify(s).regime != "recovery"
    assert classify(s, prev_regime="recovery").regime == "recovery"


def test_recovery_hysteresis_exits_when_unemployment_rises():
    s = _set(core=2.2, gdp=2.0, unemp=4.8, unemp_d3m=0.3)  # rises past +0.1
    assert classify(s, prev_regime="recovery").regime != "recovery"


def test_recovery_entry_requires_clear_fall():
    # Δ3m −0.2 is no longer enough to ENTER recovery (v1.1 needs ≤ −0.3) — kills chatter.
    s = _set(core=2.1, gdp=2.0, unemp=4.8, unemp_d3m=-0.2)
    assert classify(s, prev_regime="soft_landing").regime != "recovery"


def test_drivers_capped_at_two():
    res = classify(_set(core=2.4, gdp=2.0, unemp=4.0, unemp_d3m=-0.3, umich_d=5.0))
    assert len(res.drivers) <= 2


def test_missing_data_is_indeterminate_not_a_regime():
    """Was test_drivers_never_cite_missing_indicator, which asserted the classifier still picked a
    regime (and drivers) with unemployment absent. W3 makes that a precondition failure: a missing
    required input means we cannot call it, and the E1 spirit is satisfied more strongly than
    before — it cannot cite a missing indicator because it cites nothing at all."""
    res = classify(_set(core=2.4, gdp=2.0, unemp=None))
    assert res.regime == "indeterminate"
    assert res.rule == "missing_data"
    assert res.missing == ["unemployment"]
    assert res.drivers == [] and res.basis == []


# ── W1: the basis line (Bruce D+1, 2026-08-06) ───────────────────────────────────
# He asked "why are you calling it a soft landing when there is no reason to say that." There IS
# a reason — a three-clause tolerance band — but the card never showed it, so the label read as
# an opinion. `rule` names the branch; `basis` carries the clauses it tested.

def test_a_matched_rule_reports_the_clauses_it_tested():
    res = classify([_r("real_gdp_yoy", 2.10), _r("unemployment", 4.2, delta_3m=-0.1),
                    _r("core_cpi_yoy", 2.57)])
    assert res.regime == "soft_landing" and res.rule == "soft_landing"
    assert res.basis == [("real_gdp_yoy", 2.1, ">=", 1.0),
                         ("core_cpi_yoy", 2.57, "<", 3.0),
                         ("unemployment", 4.2, "<=", 5.0)]


def test_an_unmatched_economy_is_indeterminate_not_the_most_benign_label():
    """W3. Every one of these published "Soft landing" before — a 19% GDP contraction and a total
    data outage both rendered the most reassuring label in the set. The catch-all now declines."""
    depression = [_r("real_gdp_yoy", -19.0), _r("unemployment", 4.2, delta_3m=-0.1),
                  _r("core_cpi_yoy", 2.57)]
    res = classify(depression)
    assert res.regime == "indeterminate" and res.rule == "unmatched"
    assert res.basis == [] and res.drivers == []

    outage = [_r("real_gdp_yoy", None), _r("unemployment", None), _r("core_cpi_yoy", None)]
    res = classify(outage)
    assert res.regime == "indeterminate" and res.rule == "missing_data"
    assert sorted(res.missing) == ["core_cpi_yoy", "real_gdp_yoy", "unemployment"]


def test_basis_is_non_empty_exactly_when_a_named_rule_fired():
    """The property the D9 evidence rests on, over a grid rather than a handful of cases."""
    seen = set()
    for gdp in (-19.0, -2.0, 0.5, 1.5, 2.6, 12.4):
        for core in (1.4, 2.2, 2.57, 3.1, 3.8):
            for unemp in (3.6, 4.2, 5.1, 6.2, 14.8):
                for d3m in (-0.5, -0.1, 0.3):
                    res = classify([_r("real_gdp_yoy", gdp), _r("core_cpi_yoy", core),
                                    _r("unemployment", unemp, delta_3m=d3m)])
                    seen.add(res.rule)
                    declined = res.rule in ("unmatched", "missing_data")
                    assert bool(res.basis) == (not declined), f"{res.rule} basis={res.basis}"
                    assert res.regime in REGIME_LABELS or res.regime == "indeterminate"
                    if declined:
                        assert res.drivers == []   # never name a driver for a call we did not make
    assert "unmatched" in seen and "soft_landing" in seen   # the grid exercises both paths


def test_a_missing_value_is_dropped_from_the_basis_not_rendered_as_none():
    """A branch can fire on a disjunction while one input is None. Rendering 'None <= 5.0' on
    Bruce's card would be worse than omitting the clause."""
    res = classify([_r("real_gdp_yoy", 2.8), _r("unemployment", 3.7, delta_3m=0.0),
                    _r("core_cpi_yoy", None), _r("treasury_10y", 3.0), _r("fed_funds", 4.0)])
    assert all(b[1] is not None for b in res.basis)
