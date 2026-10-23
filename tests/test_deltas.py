"""Delta + Δ3m + release detection."""

from __future__ import annotations

from macro_pipeline.cache import Cache
from macro_pipeline.deltas import compute_delta, compute_delta_3m, detect_releases
from macro_pipeline.models import IndicatorReading

from conftest import obs


def test_compute_delta_latest_minus_previous():
    # newest-first: 3.4 then 2.9
    assert compute_delta(obs([("2026-06-01", 3.4), ("2026-05-01", 2.9)])) == 0.5


def test_compute_delta_skips_nulls():
    o = obs([("2026-06-03", None), ("2026-06-02", 4.25), ("2026-06-01", 4.20)])
    assert compute_delta(o) == 0.05


def test_compute_delta_none_when_insufficient():
    assert compute_delta(obs([("2026-06-01", 2.9)])) is None
    assert compute_delta(obs([("2026-06-01", None), ("2026-05-01", None)])) is None


def test_compute_delta_3m_monthly():
    # latest May 2026 = 4.5; ~3 months back is Feb 2026 = 4.0 -> +0.5
    o = obs([("2026-05-01", 4.5), ("2026-04-01", 4.3),
             ("2026-03-01", 4.1), ("2026-02-01", 4.0)])
    assert compute_delta_3m(o) == 0.5


def test_detect_releases_emits_on_new_print():
    reading = IndicatorReading(
        id="cpi_yoy", display_name="CPI", value=3.4, unit="%",
        period_label="Jun 2026", last_release_date="2026-06-12",
        observation_date="2026-06-01",
    )
    cache = Cache({"snapshot": {"cpi_yoy": {"value": 2.9, "observation_date": "2026-05-01"}}})
    releases = detect_releases([reading], cache)
    assert len(releases) == 1
    assert releases[0].id == "cpi_yoy"
    assert releases[0].prior_value == 2.9
    assert releases[0].new_value == 3.4


def test_detect_releases_cold_cache_is_silent():
    reading = IndicatorReading(
        id="cpi_yoy", display_name="CPI", value=3.4, unit="%",
        period_label="Jun 2026", last_release_date="2026-06-12",
        observation_date="2026-06-01",
    )
    assert detect_releases([reading], Cache()) == []


def test_detect_releases_no_release_when_period_unchanged():
    reading = IndicatorReading(
        id="cpi_yoy", display_name="CPI", value=2.9, unit="%",
        period_label="May 2026", last_release_date="2026-06-11",
        observation_date="2026-05-01",
    )
    cache = Cache({"snapshot": {"cpi_yoy": {"value": 2.9, "observation_date": "2026-05-01"}}})
    assert detect_releases([reading], cache) == []


def test_detect_releases_skips_unchanged_daily_print():
    # 2026-10-23 incident: DFF reprinted 3.88 -> 3.88, forced whats_new, run fell back.
    reading = IndicatorReading(
        id="fed_funds", display_name="Fed funds", value=3.88, unit="%",
        period_label="2026-10-22", last_release_date="2026-10-23",
        observation_date="2026-10-22",
    )
    cache = Cache({"snapshot": {"fed_funds": {"value": 3.88, "observation_date": "2026-10-21"}}})
    assert detect_releases([reading], cache) == []


def test_detect_releases_keeps_unchanged_monthly_print():
    reading = IndicatorReading(
        id="unemployment", display_name="Unemployment", value=4.2, unit="%",
        period_label="Sep 2026", last_release_date="2026-10-03",
        observation_date="2026-09-01",
    )
    cache = Cache({"snapshot": {"unemployment": {"value": 4.2, "observation_date": "2026-08-01"}}})
    assert len(detect_releases([reading], cache)) == 1
