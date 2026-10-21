"""Daily 3-dimension quality checks (D1 brief / D2 regime / D3 indicators) — offline.

Every check must be a pure function of its inputs and must never raise: callers treat a check
error as a failed check, never as a crash (PRD §397).
"""

from __future__ import annotations

from macro_pipeline import quality
from macro_pipeline.models import IndicatorReading
from macro_pipeline.quality import QualityReport

# --- fixtures ----------------------------------------------------------------------

REQUEST = {
    "as_of_date": "2026-06-17",
    "regime_today": "soft_landing",
    "regime_yesterday": "soft_landing",
    "drivers": ["unemployment"],
    "indicators": [
        {"id": "unemployment", "value": 4.2, "delta": -0.1},
        {"id": "cpi_yoy", "value": 3.5, "delta": 0.7},
    ],
    "releases_last_24h": [],
}

GOOD_OUTPUT = {
    "headline": "Soft landing continues, driven by a steady job market.",
    "quotable_numbers": [
        {"indicator_id": "unemployment", "sentence": "Unemployment eased to 4.2 percent in Jun 2026."},
        {"indicator_id": "cpi_yoy", "sentence": "CPI rose to 3.5 percent in Jun 2026."},
    ],
    "whats_new": None,
    "regime_change": None,
}


def _readings(unemp=4.2, core=2.6, gdp=2.7, ten_y=4.6, ff=3.6, umich=44.8, unemp_d3m=-0.1):
    def r(i, v, d3=None):
        return IndicatorReading(id=i, display_name=i, value=v, unit="%", period_label="Jun 2026",
                                last_release_date="2026-06-17", delta_3m=d3)
    return [
        r("unemployment", unemp, unemp_d3m), r("core_cpi_yoy", core), r("real_gdp_yoy", gdp),
        r("treasury_10y", ten_y), r("fed_funds", ff), r("umich_sentiment", umich),
    ]


# --- D2 · R1 regime reproducibility -------------------------------------------------

def test_r1_passes_when_rules_reproduce_the_published_label():
    readings = _readings()
    from macro_pipeline import classifier
    expected = classifier.classify(readings, prev_regime=None).regime
    c = quality.check_regime_reproducible(readings, expected, None)
    assert c.passed and c.dimension == "D2"


def test_r1_fails_on_a_mismatch():
    c = quality.check_regime_reproducible(_readings(), "stagflation_risk", None)
    assert not c.passed
    assert "stagflation_risk" in c.detail


def test_r1_never_raises_on_garbage_readings():
    c = quality.check_regime_reproducible(["not a reading"], "soft_landing", None)
    assert not c.passed  # a check error is a failed check, not a crash


# --- D2 · R2 regime narration -------------------------------------------------------

def test_r2_passes_when_the_headline_narrates_its_own_regime():
    assert quality.check_regime_narration(GOOD_OUTPUT, "soft_landing").passed


def test_r2_fails_when_the_briefing_names_a_different_regime():
    bad = {**GOOD_OUTPUT, "headline": "Stagflation territory looms despite steady jobs."}
    c = quality.check_regime_narration(bad, "soft_landing")
    assert not c.passed
    assert "Stagflation territory" in c.detail


def test_r2_allows_a_refusal_envelope():
    refusal = {"headline": None, "quotable_numbers": [], "whats_new": None,
               "regime_change": None, "error": "data_unavailable"}
    assert quality.check_regime_narration(refusal, "soft_landing").passed


def test_r2_scans_regime_change_text_too():
    bad = {**GOOD_OUTPUT,
           "regime_change": "The call moved to Slowing growth as hiring cooled."}
    assert not quality.check_regime_narration(bad, "soft_landing").passed


# --- D3 · I4 direction framing ------------------------------------------------------

def test_i4_passes_when_direction_words_match_the_deltas():
    assert quality.check_direction_framing(GOOD_OUTPUT, REQUEST).passed


def test_i4_catches_a_reversed_direction_word():
    # unemployment delta is -0.1, but the sentence claims it rose — O5 would NOT catch this.
    bad = {**GOOD_OUTPUT, "quotable_numbers": [
        {"indicator_id": "unemployment", "sentence": "Unemployment rose to 4.2 percent in Jun 2026."},
        GOOD_OUTPUT["quotable_numbers"][1],
    ]}
    c = quality.check_direction_framing(bad, REQUEST)
    assert not c.passed
    assert "unemployment" in c.detail


def test_i4_ignores_indicators_with_no_delta():
    req = {**REQUEST, "indicators": [{"id": "unemployment", "value": 4.2}]}
    assert quality.check_direction_framing(GOOD_OUTPUT, req).passed


# --- D3 · I2 source reconciliation --------------------------------------------------

class _Spec:
    def __init__(self, id_, series, units="lin", live_tick=False):
        self.id, self.fred_series_id, self.units = id_, series, units
        self.live_tick = live_tick


class _FakeSource:
    """values: {series_id: [(date, value), ...]} newest-first, like FRED."""

    def __init__(self, values: dict):
        self.values = values

    def fetch_series(self, series_id, units, limit):
        from macro_pipeline.fred_client import SeriesResult
        from macro_pipeline.models import Observation
        if series_id not in self.values:
            raise RuntimeError("vendor down")
        rows = self.values[series_id]
        return SeriesResult([Observation(date=d, value=v) for d, v in rows], rows[0][0])


SPECS = [_Spec("unemployment", "UNRATE"), _Spec("cpi_yoy", "CPIAUCNS")]


def _pub(id_, value, date="2026-06-17"):
    return {"id": id_, "value": value, "history": [[date, value]]}


def test_i2_passes_when_published_matches_source():
    published = [_pub("unemployment", 4.2), _pub("cpi_yoy", 3.5)]
    src = _FakeSource({"UNRATE": [("2026-06-17", 4.2)], "CPIAUCNS": [("2026-06-17", 3.5)]})
    c = quality.reconcile_indicators(published, src, specs=SPECS)
    assert c.passed and "2 indicators verified" in c.detail


def test_i2_catches_a_revised_published_value():
    published = [_pub("unemployment", 4.2)]
    src = _FakeSource({"UNRATE": [("2026-06-17", 4.5)]})  # same date, different value
    c = quality.reconcile_indicators(published, src, specs=SPECS)
    assert not c.passed
    assert "published 4.2 vs source 4.5" in c.detail


def test_i2_ignores_newer_source_observations():
    """REGRESSION: the source routinely gains newer data between publish and check. Comparing
    against 'newest now' flagged every healthy run as a mismatch (caught on the first live run:
    treasury_10y published 4.69 @07-24 vs a fresh 4.65 @07-27, which was correct all along)."""
    published = [_pub("unemployment", 4.2, "2026-06-17")]
    src = _FakeSource({"UNRATE": [("2026-06-20", 4.9), ("2026-06-17", 4.2)]})
    c = quality.reconcile_indicators(published, src, specs=SPECS)
    assert c.passed, c.detail


_GOLD_SPECS = [_Spec("gold_spot", "XAU_SPOT_GOLDAPI", live_tick=True)]


def test_i2_tolerates_ordinary_drift_on_a_live_tick():
    """REGRESSION (first live PM run, 2026-07-28): gold published 4022.699951 and the source read
    4024.300049 minutes later — a 0.04% tick, not an accuracy failure. Exact comparison flagged it
    and would have failed D3 nearly every day, manufacturing exactly the alert fatigue logged as
    OQ9. A live tick's 'today' value keeps moving, so date-matching alone cannot save it."""
    published = [_pub("gold_spot", 4022.699951, "2026-07-28")]
    src = _FakeSource({"XAU_SPOT_GOLDAPI": [("2026-07-28", 4024.300049)]})
    c = quality.reconcile_indicators(published, src, specs=_GOLD_SPECS)
    assert c.passed, c.detail


def test_i2_still_catches_a_real_error_on_a_live_tick():
    """The band must not blind the check: a wrong series id / unit drift / stale cache misses by
    orders of magnitude, far outside the 2% band."""
    published = [_pub("gold_spot", 402.27, "2026-07-28")]  # 10x unit error
    src = _FakeSource({"XAU_SPOT_GOLDAPI": [("2026-07-28", 4024.30)]})
    c = quality.reconcile_indicators(published, src, specs=_GOLD_SPECS)
    assert not c.passed
    assert "live tick" in c.detail and ">2% drift" in c.detail


def test_i2_live_tick_band_is_relative_not_absolute():
    """A 1.5% move passes, 3% fails — the band scales with price, so it behaves the same whether
    gold is at $400 or $4,000."""
    src = _FakeSource({"XAU_SPOT_GOLDAPI": [("2026-07-28", 4000.0)]})
    ok = quality.reconcile_indicators([_pub("gold_spot", 4060.0, "2026-07-28")], src,
                                      specs=_GOLD_SPECS)
    bad = quality.reconcile_indicators([_pub("gold_spot", 4120.0, "2026-07-28")], src,
                                       specs=_GOLD_SPECS)
    assert ok.passed and not bad.passed


def test_i2_non_live_specs_stay_exact():
    """FRED prints are fixed once published — the band must NOT leak onto them, or a real
    revision would slip through."""
    published = [_pub("unemployment", 4.2)]
    src = _FakeSource({"UNRATE": [("2026-06-17", 4.24)]})  # <2% but a genuine revision
    c = quality.reconcile_indicators(published, src, specs=SPECS)
    assert not c.passed


def test_i2_treats_a_vendor_outage_as_unverified_not_wrong():
    # A source being down is a health problem (alerted separately), not an accuracy failure.
    published = [_pub("unemployment", 4.2)]
    c = quality.reconcile_indicators(published, _FakeSource({}), specs=SPECS)
    assert c.passed
    assert "unverified" in c.detail


def test_i2_unverified_when_the_source_dropped_that_date():
    published = [_pub("unemployment", 4.2, "2026-06-17")]
    src = _FakeSource({"UNRATE": [("2026-06-20", 4.9)]})
    c = quality.reconcile_indicators(published, src, specs=SPECS)
    assert c.passed and "no value for 2026-06-17" in c.detail


def test_i2_skips_blanked_rows():
    published = [_pub("unemployment", None)]
    c = quality.reconcile_indicators(published, _FakeSource({}), specs=SPECS)
    assert c.passed


def test_published_observation_date_prefers_history_over_prose_label():
    assert quality.published_observation_date(
        {"history": [["2026-06-17", 1]], "period_label": "Jun 2026"}) == "2026-06-17"
    assert quality.published_observation_date({"period_label": "Jun 2026"}) is None
    assert quality.published_observation_date({"period_label": "2026-06-17"}) == "2026-06-17"


# --- D1 · judge verdict → checks ----------------------------------------------------

VERDICT_GOOD = {"c1_quotability": 5, "c3_tone": 5, "c4_conditionals": 5, "c5_length_format": 4}
VERDICT_WEAK = {"c1_quotability": 2, "c3_tone": 4, "c4_conditionals": 5, "c5_length_format": 4}


def test_d1_checks_pass_on_a_good_verdict():
    checks = quality.checks_from_verdict(VERDICT_GOOD)
    assert all(c.passed for c in checks)
    assert {c.dimension for c in checks} == {"D1"}


def test_d1_flags_a_criterion_below_threshold_and_below_3():
    checks = quality.checks_from_verdict(VERDICT_WEAK)
    failed = {c.id for c in checks if not c.passed}
    assert "D1_c1_quotability" in failed
    assert "D1_no_below_3" in failed


def test_d1_ignores_null_criteria_on_a_refusal():
    verdict = {"c1_quotability": None, "c3_tone": None, "c4_conditionals": None,
               "c5_length_format": 5}
    checks = quality.checks_from_verdict(verdict)
    assert {c.id for c in checks} == {"D1_c5_length_format", "D1_no_below_3"}
    assert all(c.passed for c in checks)


def test_recomputed_flag_below_3_does_not_trust_the_model():
    # The judge self-reports flag_below_3; nothing cross-checked it before.
    lying = {**VERDICT_WEAK, "flag_below_3": False}
    assert quality.recomputed_flag_below_3(lying) is True
    assert quality.recomputed_flag_below_3(VERDICT_GOOD) is False


# --- report roll-up -----------------------------------------------------------------

def test_report_scores_per_dimension():
    rep = QualityReport()
    for c in quality.checks_from_verdict(VERDICT_GOOD):
        rep.add(c)
    rep.add(quality.check_regime_narration(GOOD_OUTPUT, "soft_landing"))
    rep.add(quality.check_direction_framing(GOOD_OUTPUT, REQUEST))
    assert rep.dimension_score("D1") == 1.0
    assert rep.dimension_score("D2") == 1.0
    assert rep.dimension_score("D3") == 1.0
    assert rep.failures() == []
    assert set(rep.to_json()["scores"]) == {"D1", "D2", "D3"}


def test_report_dimension_score_is_none_when_empty():
    assert QualityReport().dimension_score("D2") is None
