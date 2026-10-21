"""Pre-LLM plausibility gate (mirrors the ranges in docs/schemas/briefing-io-v1.0.md)."""

from __future__ import annotations

import pytest

from macro_pipeline.models import IndicatorReading
from macro_pipeline.plausibility import is_plausible, validate


def _reading(ind_id: str, value: float | None) -> IndicatorReading:
    return IndicatorReading(id=ind_id, display_name=ind_id, value=value, unit="%",
                            period_label="May 2026", last_release_date="2026-06-11")


@pytest.mark.parametrize("ind_id, value, expected", [
    ("unemployment", 4.2, True),
    ("unemployment", 42.0, False),   # case A1: out of 0–30
    ("unemployment", 30.0, True),    # inclusive upper bound
    ("cpi_yoy", 2.9, True),
    ("cpi_yoy", 10.01, False),
    ("treasury_10y", -0.1, False),   # below 0
    ("real_gdp_yoy", -10.0, True),   # inclusive lower bound
    ("real_gdp_yoy", -10.1, False),
    ("umich_sentiment", 67.5, True),
    ("fed_funds", 4.5, True),
])
def test_is_plausible_ranges(ind_id, value, expected):
    assert is_plausible(value, ind_id) is expected


def test_null_value_is_plausible():
    # Missing data (E1) is a valid state, not a plausibility failure.
    assert is_plausible(None, "umich_sentiment") is True


def test_validate_ok_when_all_in_range():
    readings = [_reading("cpi_yoy", 2.9), _reading("unemployment", 4.2)]
    result = validate(readings)
    assert result.ok is True
    assert result.failures == []


def test_validate_flags_implausible_value():
    readings = [_reading("cpi_yoy", 2.9), _reading("unemployment", 42.0)]
    result = validate(readings)
    assert result.ok is False
    assert len(result.failures) == 1
    assert result.failures[0]["id"] == "unemployment"
    assert result.failures[0]["valid_range"] == [0.0, 30.0]
