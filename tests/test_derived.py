"""V3 derived spread rows — display-only indicator pairings computed from existing readings."""

from __future__ import annotations

from macro_pipeline.derived import build_derived
from macro_pipeline.models import IndicatorReading


def _r(id_, value, delta=None, freq="daily", history=None, source_url=""):
    return IndicatorReading(id=id_, display_name=id_, value=value, unit="%",
                            period_label="2026-06-18", last_release_date="2026-06-18",
                            delta=delta, frequency=freq, history=history or [], source_url=source_url)


def test_2s10s_value_delta_and_metadata():
    out = {x.id: x for x in build_derived([
        _r("treasury_10y", 4.25, delta=-0.05, source_url="https://fred.stlouisfed.org/series/DGS10"),
        _r("treasury_2y", 4.42, delta=0.03),
    ])}
    yc = out["yield_curve_2s10s"]
    assert yc.value == round(4.25 - 4.42, 4)
    assert yc.delta == round(-0.05 - 0.03, 4)
    assert yc.tier == "drawer"          # display-only, never core
    assert yc.unit == "%"
    assert yc.level == yc.value          # a spread IS its own level
    # 2s10s has a native series page (FRED T10Y2Y) -> "View source" goes there, not to a leg
    assert yc.source_url == "https://fred.stlouisfed.org/series/T10Y2Y"


def test_spread_without_native_page_inherits_the_primary_leg():
    out = {x.id: x for x in build_derived([
        _r("fed_funds", 3.63, source_url="https://www.newyorkfed.org/markets/reference-rates/effr"),
        _r("cpi_yoy", 3.36, freq="monthly"),
    ])}
    assert out["real_fed_funds"].source_url == "https://www.newyorkfed.org/markets/reference-rates/effr"


def test_2s10s_history_joins_by_exact_date():
    out = {x.id: x for x in build_derived([
        _r("treasury_10y", 4.25, freq="daily",
           history=[["2026-06-16", 4.30], ["2026-06-17", 4.27], ["2026-06-18", 4.25]]),
        _r("treasury_2y", 4.42, freq="daily",   # 06-17 missing here, 06-19 is extra
           history=[["2026-06-16", 4.40], ["2026-06-18", 4.42], ["2026-06-19", 9.9]]),
    ])}
    yc = out["yield_curve_2s10s"]
    # only dates present in BOTH (06-16, 06-18) produce a spread point
    assert yc.history == [["2026-06-16", round(4.30 - 4.40, 4)], ["2026-06-18", round(4.25 - 4.42, 4)]]
    assert yc.spark == [round(4.30 - 4.40, 4), round(4.25 - 4.42, 4)]


def test_mixed_cadence_forward_fills_the_monthly_print():
    out = {x.id: x for x in build_derived([
        _r("fed_funds", 4.5, delta=0.0, freq="daily", history=[
            ["2026-04-30", 4.6], ["2026-05-01", 4.6], ["2026-05-15", 4.55], ["2026-06-18", 4.5]]),
        _r("cpi_yoy", 2.7, delta=0.1, freq="monthly", history=[
            ["2026-05-01", 2.9], ["2026-06-01", 2.7]]),
    ])}
    rf = out["real_fed_funds"]
    assert rf.value == round(4.5 - 2.7, 4)
    # 04-30 predates the first monthly print -> dropped (never guess backwards). 05-01 and 05-15
    # use May's 2.9; 06-18 uses June's 2.7 — each day minus the latest print at or before it.
    assert rf.history == [["2026-05-01", round(4.6 - 2.9, 4)],
                          ["2026-05-15", round(4.55 - 2.9, 4)],
                          ["2026-06-18", round(4.5 - 2.7, 4)]]
    assert rf.spark == [v for _, v in rf.history]
    assert rf.frequency == "daily"   # updates with the daily leg now that it charts


def test_mixed_cadence_without_monthly_history_stays_value_only():
    out = {x.id: x for x in build_derived([
        _r("fed_funds", 4.5, delta=0.0, freq="daily", history=[["2026-06-18", 4.5]]),
        _r("cpi_yoy", 2.9, delta=0.1, freq="monthly", history=[]),
    ])}
    rf = out["real_fed_funds"]
    assert rf.value == round(4.5 - 2.9, 4)
    assert rf.history == [] and rf.spark == []
    assert rf.frequency == "monthly"  # no chart -> sheet stays closed, old behavior preserved


def test_null_parent_value_is_none():
    out = {x.id: x for x in build_derived([_r("treasury_10y", None), _r("treasury_2y", 4.42)])}
    assert out["yield_curve_2s10s"].value is None


def test_missing_parent_skips_the_row():
    ids = {x.id for x in build_derived([_r("treasury_10y", 4.25)])}  # no treasury_2y present
    assert "yield_curve_2s10s" not in ids
