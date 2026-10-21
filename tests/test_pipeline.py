"""End-to-end B1 pipeline against the in-memory FRED source (no network)."""

from __future__ import annotations

import json
from datetime import date

from macro_pipeline.cache import Cache
from macro_pipeline.fred_client import SeriesResult
from macro_pipeline.pipeline import run_pipeline

from conftest import AS_OF, FakeFredSource, obs


def _by_id(result, ind_id):
    return next(r for r in result.readings if r.id == ind_id)


def test_happy_path(source, tmp_path):
    result = run_pipeline(
        source, as_of=AS_OF,
        cache_path=tmp_path / "cache.json",
        write_indicators_to=tmp_path / "indicators.json",
    )

    assert result.ok is True
    assert len(result.readings) == 29  # 7 core + 11 drawer + 8 market + 3 derived spread rows
    assert sum(1 for r in result.readings if r.tier == "core") == 7
    # 26 indicator fetches + 4 extra "lin" level fetches for the rate/change series (V2 chip raw mode).
    # The 3 derived spreads add ZERO fetches (computed from existing readings).
    assert len(source.calls) == 30
    # V3 derived spread: 2s10s = treasury_10y − treasury_2y, display-only, never fetched.
    assert _by_id(result, "yield_curve_2s10s").value == round(4.25 - 4.42, 4)
    assert _by_id(result, "yield_curve_2s10s").tier == "drawer"

    # V2 chip raw-level: a rate indicator carries the underlying level (CPI index, not the YoY %),
    # while a "lin" indicator's level is just its value.
    assert _by_id(result, "cpi_yoy").level == 322.0
    assert _by_id(result, "treasury_10y").level == _by_id(result, "treasury_10y").value == 4.25

    # Daily series with a trailing weekend null -> freshest real value is used.
    t10 = _by_id(result, "treasury_10y")
    assert t10.value == 4.25
    assert t10.observation_date == "2026-06-18"
    assert t10.period_label == "2026-06-18"

    cpi = _by_id(result, "cpi_yoy")
    assert cpi.value == 2.9
    assert cpi.period_label == "May 2026"
    assert cpi.delta == 0.0
    assert cpi.delta_3m == 0.2

    assert _by_id(result, "real_gdp_yoy").period_label == "Q1 2026"
    assert _by_id(result, "umich_sentiment").period_label == "Jun 2026"
    assert result.any_stale is False

    # B2 classifier ran: regime labelled, no prior label on a cold cache.
    assert result.regime_today == "soft_landing"
    assert result.regime_yesterday is None
    assert result.drivers  # non-empty


def test_indicators_json_includes_spark_history(source, tmp_path):
    out = tmp_path / "indicators.json"
    run_pipeline(source, as_of=AS_OF, cache_path=tmp_path / "c.json", write_indicators_to=out)
    payload = json.loads(out.read_text())
    cpi = next(i for i in payload["indicators"] if i["id"] == "cpi_yoy")
    # oldest→newest, from the conftest CPIAUCNS fixture (newest-first 2.9/2.9/2.8/2.7)
    assert cpi["spark"] == [2.7, 2.8, 2.9, 2.9]
    assert all(isinstance(i["spark"], list) for i in payload["indicators"])


def test_indicators_json_carries_history_and_links(source, tmp_path):
    out = tmp_path / "indicators.json"
    result = run_pipeline(source, as_of=AS_OF, cache_path=tmp_path / "c.json", write_indicators_to=out)
    payload = json.loads(out.read_text())
    cpi = next(i for i in payload["indicators"] if i["id"] == "cpi_yoy")
    # BLS-origin row: "View source" points at the BLS series page (the primary publisher), not FRED.
    assert cpi["source_url"] == "https://data.bls.gov/timeseries/CUUR0000SA0"
    # A Treasury-grafted row links to its origin (V4); a plain FRED row still links to FRED.
    trez = next(i for i in payload["indicators"] if i["id"] == "treasury_10y")
    assert trez["source_url"].startswith("https://home.treasury.gov/")
    sp = next(i for i in payload["indicators"] if i["id"] == "sp500")
    assert sp["source_url"] == "https://fred.stlouisfed.org/series/SP500"
    assert cpi["frequency"] == "monthly"
    # history is [[iso_date, value], …] oldest→newest; small fixtures pass through un-thinned.
    assert cpi["history"][0] == ["2026-02-01", 2.7]
    assert cpi["history"][-1] == ["2026-05-01", 2.9]
    # detail-view fields are PWA-only; they never enter the LLM contract.
    llm_ind = result.briefing_request()["indicators"][0]
    assert {"history", "source_url", "frequency", "level"}.isdisjoint(llm_ind)


def test_downsample_history_bounds_and_keeps_recent_full():
    from macro_pipeline.models import Observation
    from macro_pipeline.pipeline import (
        HISTORY_POINTS, HISTORY_RECENT_FULL, _downsample_history,
    )
    # FRED returns newest-first; encode value = chronological index so reversed() is ascending.
    n = 1000
    newest_first = [Observation(date=f"2020-{i:04d}", value=float(n - 1 - i)) for i in range(n)]
    h = _downsample_history(newest_first)
    assert len(h) <= HISTORY_POINTS + 1
    vals = [v for _, v in h]
    assert vals == sorted(vals)         # oldest→newest
    assert vals[-1] == float(n - 1)     # freshest obs preserved
    # the freshest HISTORY_RECENT_FULL points are kept at full (consecutive) resolution
    assert vals[-HISTORY_RECENT_FULL:] == [float(n - HISTORY_RECENT_FULL + k) for k in range(HISTORY_RECENT_FULL)]


def test_regime_yesterday_carries_across_runs(source, tmp_path):
    cache_path = tmp_path / "cache.json"
    first = run_pipeline(source, as_of=AS_OF, cache_path=cache_path, write_indicators_to=None)
    second = run_pipeline(source, as_of=AS_OF, cache_path=cache_path, write_indicators_to=None)
    assert second.regime_yesterday == first.regime_today


def test_indicators_json_written_and_schema_clean(source, tmp_path):
    out = tmp_path / "indicators.json"
    result = run_pipeline(source, as_of=AS_OF, cache_path=tmp_path / "c.json", write_indicators_to=out)

    payload = json.loads(out.read_text())
    assert payload["as_of_date"] == "2026-06-19"
    assert payload["any_stale"] is False
    assert len(payload["indicators"]) == 29  # all tiers + derived + V3/V2.2 expansion in the PWA feed
    assert {"stale", "staleness_days", "tier", "level"} <= payload["indicators"][0].keys()
    assert any(i["tier"] == "drawer" for i in payload["indicators"])

    # The BriefingRequest data-half is core + V3 context and must NOT leak internal-only fields.
    data = result.briefing_request_data()
    assert set(data.keys()) == {"as_of_date", "indicators", "releases_last_24h"}
    assert len(data["indicators"]) == 10  # 7 core + 3 V3 context (oil/dollar/yield-curve)
    for ind in data["indicators"]:
        assert "delta_3m" not in ind
        assert "stale" not in ind
        assert "tier" not in ind  # tier is internal; not part of the LLM contract


def test_plausibility_fallback_skips_write(base_series, tmp_path):
    base_series["UNRATE"] = SeriesResult(obs([("2026-05-01", 42.0), ("2026-04-01", 4.2)]), "2026-06-05")
    out = tmp_path / "indicators.json"
    result = run_pipeline(
        FakeFredSource(base_series), as_of=AS_OF,
        cache_path=tmp_path / "c.json", write_indicators_to=out,
    )
    assert result.ok is False
    assert any(f["id"] == "unemployment" for f in result.rejected)
    assert not out.exists()  # LLM skipped → no fresh feed published


def test_release_detected_against_prior_cache(source, tmp_path):
    cache_path = tmp_path / "cache.json"
    seed = Cache({"snapshot": {
        "cpi_yoy": {"value": 2.8, "observation_date": "2026-04-01"},
        "core_cpi_yoy": {"value": 3.1, "observation_date": "2026-05-01"},
        "unemployment": {"value": 4.2, "observation_date": "2026-05-01"},
        "real_gdp_yoy": {"value": 2.1, "observation_date": "2026-01-01"},
        "treasury_10y": {"value": 4.25, "observation_date": "2026-06-18"},
        "fed_funds": {"value": 4.5, "observation_date": "2026-06-18"},
        "umich_sentiment": {"value": 67.5, "observation_date": "2026-06-01"},
    }})
    seed.save(cache_path)

    result = run_pipeline(source, as_of=AS_OF, cache_path=cache_path, write_indicators_to=None)
    assert [r.id for r in result.releases] == ["cpi_yoy"]
    assert result.releases[0].prior_value == 2.8
    assert result.releases[0].new_value == 2.9


def test_stale_data_passes_through(source, tmp_path):
    # Carry-forward #2: old-but-plausible data is flagged, NOT rejected.
    result = run_pipeline(source, as_of=date(2026, 9, 1),
                          cache_path=tmp_path / "c.json", write_indicators_to=None)
    assert result.ok is True
    assert result.any_stale is True
    assert _by_id(result, "cpi_yoy").stale is True
