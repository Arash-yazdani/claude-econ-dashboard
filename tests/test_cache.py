"""Local JSON cache — round-trip, cold-start resilience, 5MB cap."""

from __future__ import annotations

import pytest

from macro_pipeline.cache import Cache, CacheTooLargeError
from macro_pipeline.models import IndicatorReading


def test_round_trip(tmp_path):
    cache = Cache()
    reading = IndicatorReading(
        id="cpi_yoy", display_name="CPI", value=2.9, unit="%",
        period_label="May 2026", last_release_date="2026-06-11",
        observation_date="2026-05-01",
    )
    cache.update_snapshot([reading], regime_today="soft_landing")
    path = tmp_path / "cache.json"
    cache.save(path)

    reloaded = Cache.load(path)
    assert reloaded.regime_yesterday == "soft_landing"
    prev = reloaded.previous("cpi_yoy")
    assert prev["value"] == 2.9
    assert prev["observation_date"] == "2026-05-01"


def test_missing_file_is_cold_start(tmp_path):
    cache = Cache.load(tmp_path / "does-not-exist.json")
    assert cache.previous("cpi_yoy") is None
    assert cache.regime_yesterday is None


def test_corrupt_file_is_cold_start(tmp_path):
    path = tmp_path / "cache.json"
    path.write_text("{not valid json", encoding="utf-8")
    cache = Cache.load(path)  # must not raise
    assert cache.previous("cpi_yoy") is None


def test_size_cap_enforced(tmp_path):
    cache = Cache()
    cache.snapshot = {"blob": {"value": "x" * (6 * 1024 * 1024)}}
    with pytest.raises(CacheTooLargeError):
        cache.save(tmp_path / "cache.json")
