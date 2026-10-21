"""Staleness flagging — carry-forward #2 (regression case E4)."""

from __future__ import annotations

from datetime import date

from macro_pipeline.models import IndicatorReading
from macro_pipeline.staleness import apply, assess


def _reading(ind_id: str, last_release: str) -> IndicatorReading:
    return IndicatorReading(id=ind_id, display_name=ind_id, value=2.6, unit="%",
                            period_label="Feb 2026", last_release_date=last_release)


def test_fresh_value_not_stale():
    stale, days = assess(_reading("cpi_yoy", "2026-06-11"), date(2026, 6, 19))
    assert stale is False
    assert days == 8


def test_old_but_plausible_value_is_flagged_stale():
    # E4: CPI last released 2026-03-12, as_of 2026-06-19 -> 99 days (> 45) -> stale.
    stale, days = assess(_reading("cpi_yoy", "2026-03-12"), date(2026, 6, 19))
    assert stale is True
    assert days == 99


def test_apply_sets_flags_and_rolls_up():
    readings = [_reading("cpi_yoy", "2026-03-12"), _reading("core_cpi_yoy", "2026-06-11")]
    any_stale = apply(readings, date(2026, 6, 19))
    assert any_stale is True
    assert readings[0].stale is True and readings[0].staleness_days == 99
    assert readings[1].stale is False
