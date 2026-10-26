"""B4 validator — O-checks (heavy on O5 grounding), refusal envelope, orchestration."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from macro_pipeline.llm_client import BriefingLLMError
from macro_pipeline.validator import (
    REFUSAL_ENVELOPE, _data_numbers, fallback_template, produce_briefing, validate,
)

ROOT = Path(__file__).resolve().parents[1]
FIX = json.loads((ROOT / "tests" / "fixtures" / "regression-cases-v1.0.json").read_text())
T1 = next(c for c in FIX["cases"] if c["id"] == "T1")["input"]


def _ind(ind_id, value, period="May 2026", unit="%", **kw):
    return {"id": ind_id, "display_name": ind_id, "value": value, "unit": unit,
            "period_label": period, "last_release_date": "2026-06-11", **kw}


def _req(indicators, releases=None, regime_today="soft_landing", regime_yesterday="soft_landing"):
    return {"as_of_date": "2026-06-19", "regime_today": regime_today,
            "regime_yesterday": regime_yesterday, "drivers": [],
            "indicators": indicators, "releases_last_24h": releases or []}


def _normal(headline="Soft landing holds, with a steady job market.", q=None,
            whats_new=None, regime_change=None):
    return {"headline": headline,
            "quotable_numbers": q or [
                {"indicator_id": "cpi_yoy", "sentence": "CPI 2.9 percent in May 2026."},
                {"indicator_id": "unemployment", "sentence": "Unemployment 4.2 percent in May 2026."}],
            "whats_new": whats_new, "regime_change": regime_change}


def _failed(result):
    return {cid for cid, _ in result.failed}


# --- O5 number grounding (the PRD-flagged risk area) -------------------------------

def test_o5_grounded_numbers_pass():
    assert validate(_normal(), _req([_ind("cpi_yoy", 2.9), _ind("unemployment", 4.2)])).status == "valid"


def test_o5_ungrounded_number_fails():
    out = _normal(q=[{"indicator_id": "cpi_yoy", "sentence": "CPI 3.5 percent in May 2026."},
                     {"indicator_id": "unemployment", "sentence": "Unemployment 4.2 percent in May 2026."}])
    assert "O5" in _failed(validate(out, _req([_ind("cpi_yoy", 2.9), _ind("unemployment", 4.2)])))


def test_o5_tolerance_boundary():
    req = _req([_ind("core_cpi_yoy", 2.82), _ind("unemployment", 4.2)])
    edge = _normal(q=[{"indicator_id": "core_cpi_yoy", "sentence": "Core CPI 2.87 percent in May 2026."},
                      {"indicator_id": "unemployment", "sentence": "Unemployment 4.2 percent in May 2026."}])
    over = _normal(q=[{"indicator_id": "core_cpi_yoy", "sentence": "Core CPI 2.88 percent in May 2026."},
                      {"indicator_id": "unemployment", "sentence": "Unemployment 4.2 percent in May 2026."}])
    assert validate(edge, req).status == "valid"          # 0.05 within tolerance
    assert "O5" in _failed(validate(over, req))            # 0.06 out


def test_o5_handles_negative_values():
    req = _req([_ind("real_gdp_yoy", -7.4, period="Q1 2026"), _ind("unemployment", 14.8)],
               regime_today="slowdown", regime_yesterday="soft_landing")
    out = _normal(headline="Slowing growth as output contracts sharply.",
                  q=[{"indicator_id": "real_gdp_yoy", "sentence": "Real GDP at -7.4 percent for Q1 2026."},
                     {"indicator_id": "unemployment", "sentence": "Unemployment 14.8 percent in May 2026."}],
                  regime_change="The call moved from Soft landing to Slowing growth as growth turned negative.")
    assert validate(out, req).status == "valid"


def test_o5_basis_points_conversion():
    req = _req([_ind("treasury_10y", 4.25, period="2026-06-18"), _ind("cpi_yoy", 2.9, delta=0.10)])
    out = _normal(headline="Soft landing holds as yields edge up.",
                  q=[{"indicator_id": "treasury_10y", "sentence": "The 10-year rose 10 basis points to 4.25% on 2026-06-18."},
                     {"indicator_id": "cpi_yoy", "sentence": "CPI 2.9 percent in May 2026."}])
    assert validate(out, req).status == "valid"  # 10 bps == 0.10 (the cpi delta)


def test_o5_year_and_quarter_not_treated_as_numbers():
    req = _req([_ind("real_gdp_yoy", 2.1, period="Q1 2026"), _ind("unemployment", 4.2)])
    out = _normal(q=[{"indicator_id": "real_gdp_yoy", "sentence": "Real GDP 2.1 percent for Q1 2026."},
                     {"indicator_id": "unemployment", "sentence": "Unemployment 4.2 percent in May 2026."}])
    assert validate(out, req).status == "valid"  # 2026 (year) and Q1 must not flag as ungrounded


# --- O5 review-found edge cases (year fabrication, thousands sep) -------------------

def test_o5_fabricated_year_shaped_value_is_caught():
    # Review HIGH: a fabricated number equal to the period's year must NOT slip past O5.
    req = _req([_ind("umich_sentiment", 99.0, unit="index"), _ind("unemployment", 4.2)])
    out = _normal(headline="Soft landing holds.",
                  q=[{"indicator_id": "umich_sentiment", "sentence": "Sentiment hit 2026 in May 2026."},
                     {"indicator_id": "unemployment", "sentence": "Unemployment 4.2 percent in May 2026."}])
    assert "O5" in _failed(validate(out, req))


def test_data_numbers_year_in_period_skipped_but_bare_year_grounded():
    assert _data_numbers("in May 2026") == []          # period year → not a data number
    assert (2026.0, False) in _data_numbers("near 2026")  # bare year → must be grounded


def test_data_numbers_strips_thousands_separator():
    assert _data_numbers("printed 71,000 today") == [(71000.0, False)]


def test_period_grounding_rejects_fabricated_month():
    out = _normal(q=[{"indicator_id": "cpi_yoy", "sentence": "CPI 2.9 percent in March 2026."},
                     {"indicator_id": "unemployment", "sentence": "Unemployment 4.2 percent in May 2026."}])
    assert "period" in _failed(validate(out, _req([_ind("cpi_yoy", 2.9), _ind("unemployment", 4.2)])))


def test_o2_sentence_over_200_chars_fails():
    out = _normal(q=[{"indicator_id": "cpi_yoy", "sentence": "CPI 2.9 percent in May 2026."},
                     {"indicator_id": "unemployment", "sentence": "Unemployment 4.2 percent " + "x" * 200}])
    assert "O2" in _failed(validate(out, _req([_ind("cpi_yoy", 2.9), _ind("unemployment", 4.2)])))


def test_o2_unknown_top_level_key_fails():
    out = {**_normal(), "extra_field": "nope"}
    assert "O2" in _failed(validate(out, _req([_ind("cpi_yoy", 2.9), _ind("unemployment", 4.2)])))


# --- O8 banned phrases -------------------------------------------------------------

def test_o8_no_false_positive_on_word_fragment():
    # "i think" must NOT match inside "multi think" (word-boundary anchoring).
    out = _normal(headline="Soft landing, and the multi think tank agrees.")
    assert "O8" not in _failed(validate(out, _req([_ind("cpi_yoy", 2.9), _ind("unemployment", 4.2)])))




@pytest.mark.parametrize("phrase", ["recommend", "should buy", "bullish on", "you should"])
def test_o8_banned_phrase_fails(phrase):
    out = _normal(headline=f"Soft landing, and analysts {phrase} caution.")
    assert "O8" in _failed(validate(out, _req([_ind("cpi_yoy", 2.9), _ind("unemployment", 4.2)])))


def test_o8_is_case_insensitive():
    out = _normal(headline="Soft landing. We Recommend nothing here.")
    assert "O8" in _failed(validate(out, _req([_ind("cpi_yoy", 2.9), _ind("unemployment", 4.2)])))


# --- O4 conditional emptiness ------------------------------------------------------

def test_o4_whats_new_without_release_fails():
    out = _normal(whats_new="Today: CPI hotter.")
    assert "O4" in _failed(validate(out, _req([_ind("cpi_yoy", 2.9), _ind("unemployment", 4.2)])))


def test_o4_regime_change_without_change_fails():
    out = _normal(regime_change="The call moved from X to Y.")
    assert "O4" in _failed(validate(out, _req([_ind("cpi_yoy", 2.9), _ind("unemployment", 4.2)])))


def test_o4_correct_conditionals_pass():
    req = _req([_ind("cpi_yoy", 3.4, period="Jun 2026"), _ind("unemployment", 4.2)],
               releases=[{"id": "cpi_yoy", "prior_value": 2.9, "new_value": 3.4}],
               regime_today="stagflation_risk", regime_yesterday="soft_landing")
    out = _normal(headline="Stagflation territory, driven by rising CPI.",
                  q=[{"indicator_id": "cpi_yoy", "sentence": "CPI 3.4 percent for Jun 2026."},
                     {"indicator_id": "unemployment", "sentence": "Unemployment 4.2 percent in May 2026."}],
                  whats_new="Today: CPI hotter, from 2.9 percent to 3.4 percent.",
                  regime_change="The call moved from Soft landing to Stagflation territory as CPI climbed.")
    assert validate(out, req).status == "valid"


# --- O3 / O2 / length --------------------------------------------------------------

def test_o3_unknown_indicator_id_fails():
    out = _normal(q=[{"indicator_id": "btc_usd", "sentence": "Bitcoin 2.9 percent in May 2026."},
                     {"indicator_id": "unemployment", "sentence": "Unemployment 4.2 percent in May 2026."}])
    assert "O3" in _failed(validate(out, _req([_ind("cpi_yoy", 2.9), _ind("unemployment", 4.2)])))


def test_o2_requires_exactly_two_quotable_numbers():
    out = {"headline": "Soft landing.", "whats_new": None, "regime_change": None,
           "quotable_numbers": [{"indicator_id": "cpi_yoy", "sentence": "CPI 2.9 percent in May 2026."}]}
    assert "O2" in _failed(validate(out, _req([_ind("cpi_yoy", 2.9), _ind("unemployment", 4.2)])))


def test_length_headline_over_22_words_fails():
    out = _normal(headline=" ".join(["Soft"] * 25))
    assert "length" in _failed(validate(out, _req([_ind("cpi_yoy", 2.9), _ind("unemployment", 4.2)])))


# --- O16/O17 refusal envelope ------------------------------------------------------

def test_exact_refusal_envelope_is_valid_refusal():
    res = validate(dict(REFUSAL_ENVELOPE), _req([_ind("unemployment", 42.0)]))
    assert res.status == "refusal" and res.is_refusal


def test_malformed_refusal_is_invalid():
    bad = {"headline": None, "quotable_numbers": [], "whats_new": None, "regime_change": None}  # no error
    res = validate(bad, _req([_ind("unemployment", 42.0)]))
    assert res.status == "invalid" and "O16" in _failed(res)


# --- fixture integration + fallback template ---------------------------------------

def test_t1_valid_output_passes():
    out = _normal(q=[{"indicator_id": "cpi_yoy", "sentence": "CPI 2.9 percent in May 2026."},
                     {"indicator_id": "unemployment", "sentence": "Unemployment 4.2 percent in May 2026."}])
    assert validate(out, T1).status == "valid"


def test_fallback_template_text():
    req = _req([_ind("cpi_yoy", 2.9), _ind("unemployment", 4.2)])
    assert fallback_template(req) == "Regime: Soft landing. CPI: 2.9%. Unemployment: 4.2%."


# --- produce_briefing orchestration (retry-once-then-template) ----------------------

class FakeLLM:
    def __init__(self, name, outputs):
        self.name = name
        self.outputs = list(outputs)
        self.calls = 0

    def generate(self, system, user):
        self.calls += 1
        item = self.outputs[min(self.calls - 1, len(self.outputs) - 1)]
        if item == "raise":
            raise BriefingLLMError(f"{self.name} down")
        return item


GOOD = {"headline": "Soft landing holds, with a steady job market.",
        "quotable_numbers": [{"indicator_id": "cpi_yoy", "sentence": "CPI 2.9 percent in May 2026."},
                             {"indicator_id": "unemployment", "sentence": "Unemployment 4.2 percent in May 2026."}],
        "whats_new": None, "regime_change": None}
BAD = {**GOOD, "quotable_numbers": [{"indicator_id": "cpi_yoy", "sentence": "CPI 9.9 percent in May 2026."},
                                    {"indicator_id": "unemployment", "sentence": "Unemployment 4.2 percent in May 2026."}]}
REQ = _req([_ind("cpi_yoy", 2.9), _ind("unemployment", 4.2)])


def test_produce_valid_first_try():
    fb = produce_briefing(REQ, primary=FakeLLM("opus", [GOOD]))
    assert fb.source == "llm" and fb.output == GOOD and fb.model == "opus"


def test_produce_retries_once_then_succeeds():
    primary = FakeLLM("opus", [BAD, GOOD])
    fb = produce_briefing(REQ, primary=primary)
    assert fb.source == "llm" and primary.calls == 2


def test_produce_invalid_twice_falls_back():
    primary = FakeLLM("opus", [BAD, BAD])
    fb = produce_briefing(REQ, primary=primary)
    assert fb.source == "fallback" and fb.fallback_text and primary.calls == 2


def test_produce_valid_refusal_renders_template_without_retry():
    primary = FakeLLM("opus", [dict(REFUSAL_ENVELOPE)])
    fb = produce_briefing(REQ, primary=primary)
    assert fb.source == "fallback" and primary.calls == 1  # no retry on a correct refusal


def test_produce_both_providers_down_falls_back():
    fb = produce_briefing(REQ, primary=FakeLLM("opus", ["raise"]))
    assert fb.source == "fallback" and fb.fallback_text


# ── daily-release date grounding (2026-08-25 / 2026-09-02 fallbacks) ────────────────
# A DAILY row's period_label is an ISO date, so it carried no month WORD and no day. When a
# daily series released, O4 forces a whats_new, the writer named the day in prose, and the pair
# (period rejecting "Sep", O5 rejecting the day "2") failed a TRUE sentence. All 3 release-run
# fallbacks in 167 runs were daily releases. A wrong date must still fail.

def _daily_release_request():
    return {
        "as_of_date": "2026-09-02", "regime_today": "soft_landing", "regime_yesterday": "soft_landing",
        "indicators": [
            {"id": "cpi_yoy", "value": 3.36483, "delta": -0.1, "period_label": "Jul 2026",
             "last_release_date": "2026-08-12", "unit": "%"},
            {"id": "core_cpi_yoy", "value": 2.47827, "delta": -0.02, "period_label": "Jul 2026",
             "last_release_date": "2026-08-12", "unit": "%"},
            {"id": "unemployment", "value": 4.1, "delta": -0.1, "period_label": "Jul 2026",
             "last_release_date": "2026-08-07", "unit": "%"},
            {"id": "brent_oil", "value": 96.02, "delta": 7.78, "period_label": "2026-09-02",
             "last_release_date": "2026-09-02", "unit": "USD"},
        ],
        "releases_last_24h": [{"id": "brent_oil", "prior_value": 88.24, "new_value": 96.02}],
    }


def _with_whats_new(text):
    return {
        "headline": "Soft landing holds.",
        "quotable_numbers": [
            {"indicator_id": "core_cpi_yoy", "sentence": "Core CPI at 2.48 percent for Jul 2026 is near target."},
            {"indicator_id": "unemployment", "sentence": "Unemployment at 4.1 percent for Jul 2026 stays low."},
        ],
        "whats_new": text, "regime_change": None,
    }


def test_true_daily_release_date_is_accepted():
    r = _daily_release_request()
    for text in ("Brent rose to 96.02 USD on Sep 2.", "Brent rose to 96.02 USD on September 2."):
        v = validate(_with_whats_new(text), r)
        assert v.status == "valid", (text, v.failed)


def test_wrong_daily_release_date_still_fails():
    r = _daily_release_request()
    for text in ("Brent rose to 96.02 USD on Sep 27.",   # wrong day
                 "Brent rose to 96.02 USD on Oct 2."):    # wrong month
        v = validate(_with_whats_new(text), r)
        assert v.status != "valid", text


def test_full_date_with_year_is_accepted_only_for_a_real_day():
    # Haiku 4.5 writes "Sep 2, 2026"; the year after the day failed O5 on T4 (2026-10-23, 10-26).
    r = _daily_release_request()
    for text in ("Brent rose to 96.02 USD on Sep 2, 2026.", "Brent rose to 96.02 USD on September 2 2026."):
        v = validate(_with_whats_new(text), r)
        assert v.status == "valid", (text, v.failed)
    v = validate(_with_whats_new("Brent rose to 96.02 USD on Sep 27, 2026."), r)
    assert v.status != "valid"


def test_fabricated_number_still_fails_alongside_a_true_date():
    r = _daily_release_request()
    v = validate(_with_whats_new("Brent rose to 111.11 USD on Sep 2."), r)
    assert v.status != "valid"
    assert any(c == "O5" for c, _ in v.failed)


def test_produce_fallback_keeps_the_rejected_drafts():
    """The audit row must carry what the model SAID, not just that it failed: the 2026-08-25
    and 09-02 fallbacks could only be diagnosed by reproducing the signature offline."""
    fb = produce_briefing(REQ, primary=FakeLLM("opus", [BAD, BAD]))
    assert fb.source == "fallback"
    assert fb.rejected == [BAD, BAD]                 # attempt order, both drafts


def test_produce_llm_and_refusal_carry_no_rejected_drafts():
    assert produce_briefing(REQ, primary=FakeLLM("opus", [GOOD])).rejected == []
    # a correct refusal is a correct output, not a rejected one
    assert produce_briefing(REQ, primary=FakeLLM("opus", [dict(REFUSAL_ENVELOPE)])).rejected == []
