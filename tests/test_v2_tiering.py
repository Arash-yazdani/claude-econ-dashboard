"""V2 indicator tiering — the macro core feeds the briefing; drawer/market are display-only.

Locks the load-bearing invariants of docs/v2-indicator-expansion.md + ADR-003:
  - the briefing_request = the macro core 7 + a curated context handful (V3,
    config.BRIEFING_CONTEXT_IDS); the remaining drawer/market never enter it,
  - adding display indicators does NOT change the regime,
  - a display-only value out of range cannot blank the briefing (it drops to "—").
"""

from __future__ import annotations

from macro_pipeline import classifier
from macro_pipeline.config import BRIEFING_CONTEXT_IDS, INDICATORS
from macro_pipeline.fred_client import SeriesResult
from macro_pipeline.pipeline import run_pipeline

from conftest import AS_OF, FakeFredSource, obs

CORE_IDS = {"cpi_yoy", "core_cpi_yoy", "unemployment", "real_gdp_yoy",
            "treasury_10y", "fed_funds", "umich_sentiment"}


def test_config_tiers():
    core = [s for s in INDICATORS if s.tier == "core"]
    drawer = [s for s in INDICATORS if s.tier == "drawer"]
    market = [s for s in INDICATORS if s.tier == "market"]
    assert len(INDICATORS) == 26  # 7 core + 11 drawer + 8 market (V2.2: +gold_spot, ADR-006)
    assert {s.id for s in core} == CORE_IDS
    assert len(drawer) == 11
    assert {s.id for s in drawer} >= {"payrolls", "treasury_2y", "jobless_claims",
                                      "tips_10y_real", "capacity_util", "mfg_employment"}
    assert {s.id for s in market} == {"sp500", "nasdaq", "market_cap", "hy_spread",
                                      "vix", "usd_index", "brent_oil", "gold_spot"}


def test_briefing_request_is_core_plus_context(source):
    result = run_pipeline(source, as_of=AS_OF, cache_path="/dev/null", write_indicators_to=None)
    req = result.briefing_request()
    ids = {i["id"] for i in req["indicators"]}
    # V3: the briefing now sees the 7 core PLUS the curated context handful (oil/dollar/yield-curve).
    assert ids == CORE_IDS | set(BRIEFING_CONTEXT_IDS)
    assert {"yield_curve_2s10s", "brent_oil", "usd_index"} <= ids      # context handful is in
    # the OTHER drawer/market rows are still excluded from the LLM request.
    assert "payrolls" not in ids and "sp500" not in ids and "market_cap" not in ids
    # the data-half and the PWA feed differ: feed has all 29 (all tiers + derived), request has 10.
    assert len(result.indicators_json()["indicators"]) == 29
    assert len(req["indicators"]) == 10


def test_regime_unchanged_by_drawer_indicators(source):
    result = run_pipeline(source, as_of=AS_OF, cache_path="/dev/null", write_indicators_to=None)
    core_only = [r for r in result.readings if r.tier == "core"]
    full = classifier.classify(result.readings)
    just_core = classifier.classify(core_only)
    assert full.regime == just_core.regime == result.regime_today
    assert full.drivers == just_core.drivers


def test_drawer_implausible_does_not_gate_and_drops_to_none(base_series, tmp_path):
    # 2-year yield at 999 (> its 20% plausible max). A DISPLAY-only indicator must not blank
    # Bruce's briefing — the run stays ok and the bad value drops to None ("—") for the PWA.
    base_series["DGS2"] = SeriesResult(obs([("2026-06-18", 999.0), ("2026-06-17", 4.4)]), "2026-06-18")
    out = tmp_path / "indicators.json"
    result = run_pipeline(FakeFredSource(base_series), as_of=AS_OF,
                          cache_path=tmp_path / "c.json", write_indicators_to=out)
    assert result.ok is True
    t2 = next(r for r in result.readings if r.id == "treasury_2y")
    assert t2.value is None
    assert out.exists()  # the feed still publishes (unlike a core plausibility failure)


def test_any_stale_rolls_up_core_only():
    from datetime import date

    from macro_pipeline import staleness
    from macro_pipeline.models import IndicatorReading

    def rdg(id_, tier, last_release):
        return IndicatorReading(id=id_, display_name=id_, value=1.0, unit="%",
                                period_label="x", last_release_date=last_release, tier=tier)

    core = rdg("cpi_yoy", "core", "2026-06-15")                 # fresh (within 45d)
    drawer = rdg("federal_debt_gdp", "drawer", "2025-01-01")    # ~1.5y old, far past 120d
    any_stale = staleness.apply([core, drawer], date(2026, 6, 19))
    assert drawer.stale is True    # the row is flagged stale for the UI...
    assert any_stale is False      # ...but a stale display-only row never ambers the dashboard


def test_indicators_json_carries_tier(source, tmp_path):
    out = tmp_path / "indicators.json"
    run_pipeline(source, as_of=AS_OF, cache_path=tmp_path / "c.json", write_indicators_to=out)
    import json
    payload = json.loads(out.read_text())
    by_id = {i["id"]: i for i in payload["indicators"]}
    assert by_id["cpi_yoy"]["tier"] == "core"
    assert by_id["payrolls"]["tier"] == "drawer"
