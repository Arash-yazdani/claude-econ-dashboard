"""B8 observability — run-log audit row + opt-in service shims (offline, deterministic).

Covers: input-hash stability, record_from mapping (llm / fallback / refusal), JSONL
append + the never-break-the-run guarantee, and the env-gated no-op rule for the
healthcheck/alert shims (requests must NOT be touched when the env var is unset).
"""

from __future__ import annotations

import json

import pytest

from macro_pipeline import observability
from macro_pipeline.observability import (
    ALERT_WEBHOOK_ENV,
    HEALTHCHECK_URL_ENV,
    RunRecord,
    input_hash,
    log_run,
    ping_healthcheck,
    record_from,
    send_alert,
)
from macro_pipeline.validator import FinalBriefing, ValidationResult

# --- fixtures: a BriefingRequest + the three FinalBriefing shapes -------------------

REQUEST = {
    "as_of_date": "2026-06-17",
    "regime_today": "soft_landing",
    "regime_yesterday": "slowdown",
    "drivers": ["cpi_yoy", "unemployment"],
    "indicators": [{"id": "cpi_yoy", "value": 2.9}],
    "releases_last_24h": [],
}

NORMAL_OUTPUT = {"headline": "Soft landing holds.", "quotable_numbers": [],
                 "whats_new": None, "regime_change": None}


def _valid_final() -> FinalBriefing:
    return FinalBriefing("llm", ValidationResult("valid"), output=NORMAL_OUTPUT,
                         model="gemini-2.5-flash")


def _fallback_final() -> FinalBriefing:
    # two invalid checks → quality fallback, NOT a refusal
    val = ValidationResult("invalid", [("O5", "ungrounded"), ("O8", "banned phrase")])
    return FinalBriefing("fallback", val, fallback_text="Regime: Soft landing.")


def _refusal_final() -> FinalBriefing:
    # validator returns source "fallback" with status "refusal" for a correct data-bail
    return FinalBriefing("fallback", ValidationResult("refusal", is_refusal=True),
                         fallback_text="Regime: Soft landing.")


# --- input_hash --------------------------------------------------------------------

def test_input_hash_is_64_hex_chars():
    h = input_hash(REQUEST)
    assert len(h) == 64
    assert all(c in "0123456789abcdef" for c in h)


def test_input_hash_independent_of_key_order():
    a = {"x": 1, "y": [1, 2], "z": {"b": 2, "a": 1}}
    b = {"z": {"a": 1, "b": 2}, "y": [1, 2], "x": 1}  # same content, shuffled keys
    assert input_hash(a) == input_hash(b)


def test_input_hash_changes_with_content():
    assert input_hash(REQUEST) != input_hash({**REQUEST, "as_of_date": "2026-06-18"})


# --- record_from -------------------------------------------------------------------

def test_record_from_valid_llm():
    rec = record_from(REQUEST, _valid_final(), run_label="manual", prompt_version="1.3.1",
                      latency_ms=1234, llm_calls=1, timestamp="2026-06-17T10:15:00-04:00")
    assert rec.source == "llm"
    assert rec.model == "gemini-2.5-flash"
    assert rec.validator_status == "valid"
    assert rec.validator_failed == []
    assert rec.input_hash == input_hash(REQUEST)
    assert rec.as_of_date == "2026-06-17"
    assert rec.regime_today == "soft_landing"
    assert rec.regime_yesterday == "slowdown"
    assert rec.drivers == ["cpi_yoy", "unemployment"]
    assert rec.latency_ms == 1234
    assert rec.llm_calls == 1
    assert rec.any_stale is False  # not present in request → default
    assert rec.error is None


def test_record_from_fallback_extracts_failed_checks():
    rec = record_from(REQUEST, _fallback_final(), run_label="AM", prompt_version="1.3.1",
                      latency_ms=None, timestamp="2026-06-17T10:15:00-04:00")
    assert rec.source == "fallback"
    assert rec.model is None
    assert rec.validator_status == "invalid"
    assert rec.validator_failed == ["O5", "O8"]  # ids only, in order
    assert rec.latency_ms is None


def test_record_from_refusal_is_source_refusal():
    rec = record_from(REQUEST, _refusal_final(), run_label="PM", prompt_version="1.3.1",
                      latency_ms=900, timestamp="2026-06-17T17:30:00-04:00")
    # validator gave source "fallback" + status "refusal" → record source is "refusal"
    assert rec.source == "refusal"
    assert rec.validator_status == "refusal"
    assert rec.validator_failed == []


def test_record_from_any_stale_injected_by_caller():
    rec = record_from({**REQUEST, "any_stale": True}, _valid_final(), run_label="manual",
                      prompt_version="1.3.1", latency_ms=10, timestamp="2026-06-17T10:15:00-04:00")
    assert rec.any_stale is True


def test_record_from_skipped_when_no_validation():
    final = FinalBriefing("fallback", None, fallback_text="Regime: Soft landing.")
    rec = record_from(REQUEST, final, run_label="manual", prompt_version="1.3.1",
                      latency_ms=None, timestamp="2026-06-17T10:15:00-04:00")
    assert rec.validator_status == "skipped"
    assert rec.validator_failed == []


def test_to_json_is_serializable_and_complete():
    rec = record_from(REQUEST, _valid_final(), run_label="manual", prompt_version="1.3.1",
                      latency_ms=5, timestamp="2026-06-17T10:15:00-04:00")
    d = rec.to_json()
    json.dumps(d)  # must not raise
    expected_keys = {
        "timestamp", "run_label", "as_of_date", "input_hash", "source", "model",
        "prompt_version", "validator_status", "validator_failed", "regime_today",
        "regime_yesterday", "drivers", "any_stale", "latency_ms", "llm_calls", "error",
        "output", "sources", "indicators_blanked", "stale_indicators", "releases_last_24h",
        "rejected_outputs", "llm_usage",
    }
    assert set(d) == expected_keys


def test_record_from_captures_releases_for_c4_judging():
    """REGRESSION: the row stores only the input HASH, so a later rebuild cannot recover the
    releases — and C4 ("whats_new fires IFF a release did") is unjudgeable without them. While
    this was missing, the daily eval scored a CORRECT whats_new as a C4 failure (judge gave 1/5)
    on every release day, because the rebuilt request always claimed no release had fired."""
    req = {**REQUEST, "releases_last_24h": [
        {"id": "cpi_yoy", "prior_value": 2.9, "new_value": 3.4},
    ]}
    rec = record_from(req, _valid_final(), run_label="manual", prompt_version="1.3.1",
                      latency_ms=5, timestamp="2026-06-17T10:15:00-04:00")
    assert rec.releases_last_24h == [{"id": "cpi_yoy", "prior_value": 2.9, "new_value": 3.4}]
    assert rec.to_json()["releases_last_24h"] == rec.releases_last_24h


def test_record_from_defaults_releases_to_empty():
    rec = record_from(REQUEST, _valid_final(), run_label="manual", prompt_version="1.3.1",
                      latency_ms=5, timestamp="2026-06-17T10:15:00-04:00")
    assert rec.releases_last_24h == []


def test_record_from_captures_output_for_llm():
    # §339 "output": the validated briefing dict the user saw must be in the audit row.
    rec = record_from(REQUEST, _valid_final(), run_label="manual", prompt_version="1.3.1",
                      latency_ms=5, timestamp="2026-06-17T10:15:00-04:00")
    assert rec.output == NORMAL_OUTPUT


def test_record_from_captures_fallback_text_as_output():
    rec = record_from(REQUEST, _fallback_final(), run_label="manual", prompt_version="1.3.1",
                      latency_ms=5, timestamp="2026-06-17T10:15:00-04:00")
    assert rec.output == "Regime: Soft landing."  # the rendered fallback card text


def test_record_from_any_stale_explicit_param_wins():
    # Production path: briefing_request() omits any_stale, so run_briefing passes it explicitly,
    # even though REQUEST itself carries no any_stale key.
    rec = record_from(REQUEST, _valid_final(), run_label="manual", prompt_version="1.3.1",
                      latency_ms=5, timestamp="2026-06-17T10:15:00-04:00", any_stale=True)
    assert rec.any_stale is True


def test_error_record_is_a_loggable_audit_row(tmp_path):
    # PRD §339: a crash before a briefing exists still leaves an audit trail; error field is used.
    rec = observability.error_record(
        run_label="manual", prompt_version="1.3.1",
        timestamp="2026-06-17T10:15:00-04:00", error="FredError: 503", as_of_date="2026-06-17")
    assert rec.source == "error" and rec.validator_status == "error"
    assert rec.error == "FredError: 503" and rec.input_hash == ""
    path = tmp_path / "runs.jsonl"
    assert log_run(rec, path) is True
    parsed = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert parsed["source"] == "error" and parsed["error"] == "FredError: 503"


def test_to_json_conforms_to_run_record_schema():
    # Keep the dataclass and schemas/run-record.schema.json in lockstep for llm / refusal / error.
    from pathlib import Path

    from jsonschema import Draft202012Validator

    schema = json.loads(
        (Path(__file__).resolve().parents[1] / "schemas" / "run-record.schema.json").read_text()
    )
    Draft202012Validator.check_schema(schema)
    v = Draft202012Validator(schema)
    records = [
        record_from(REQUEST, _valid_final(), run_label="manual", prompt_version="1.3.1",
                    latency_ms=5, timestamp="2026-06-17T10:15:00-04:00"),
        record_from(REQUEST, _refusal_final(), run_label="PM", prompt_version="1.3.1",
                    latency_ms=None, timestamp="2026-06-17T17:30:00-04:00"),
        observability.error_record(run_label="manual", prompt_version="1.3.1",
                                   timestamp="2026-06-17T10:15:00-04:00", error="X: y"),
    ]
    for rec in records:
        assert list(v.iter_errors(rec.to_json())) == []


# --- log_run -----------------------------------------------------------------------

def _record() -> RunRecord:
    return record_from(REQUEST, _valid_final(), run_label="manual", prompt_version="1.3.1",
                       latency_ms=5, timestamp="2026-06-17T10:15:00-04:00")


def test_log_run_appends_parseable_jsonl(tmp_path):
    path = tmp_path / "nested" / "runs.jsonl"  # parent dir does not exist yet
    assert log_run(_record(), path) is True
    assert log_run(_record(), path) is True  # appends, not overwrites
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    for line in lines:
        parsed = json.loads(line)  # each line is valid JSON
        assert parsed["source"] == "llm"
        assert parsed["input_hash"] == input_hash(REQUEST)


def test_log_run_returns_false_when_path_unwritable(tmp_path):
    # point the log at a directory → open("a") raises IsADirectoryError; must be swallowed
    bad = tmp_path / "a_dir"
    bad.mkdir()
    assert log_run(_record(), bad) is False  # returns False, does NOT raise


# --- ping_healthcheck / send_alert no-op rule --------------------------------------

class _Boom:
    """Monkeypatch target: fails the test if requests.get/post is ever called."""

    def __call__(self, *a, **k):  # pragma: no cover - only hit on a bug
        raise AssertionError("requests must NOT be called when the env var is unset")


class _Resp:
    def __init__(self, status_code: int):
        self.status_code = status_code


def test_ping_healthcheck_noop_when_env_unset(monkeypatch):
    monkeypatch.delenv(HEALTHCHECK_URL_ENV, raising=False)
    monkeypatch.setattr(observability.requests, "get", _Boom())
    assert ping_healthcheck("success") is False  # no request constructed


def test_ping_healthcheck_builds_suffix_and_returns_true(monkeypatch):
    monkeypatch.setenv(HEALTHCHECK_URL_ENV, "https://hc.example/abc")
    calls: list[tuple] = []

    def fake_get(url, timeout):
        calls.append((url, timeout))
        return _Resp(200)

    monkeypatch.setattr(observability.requests, "get", fake_get)
    assert ping_healthcheck("start") is True
    assert ping_healthcheck("success") is True
    assert ping_healthcheck("fail") is True
    assert [c[0] for c in calls] == [
        "https://hc.example/abc/start",
        "https://hc.example/abc",
        "https://hc.example/abc/fail",
    ]


def test_ping_healthcheck_swallows_exception(monkeypatch):
    monkeypatch.setenv(HEALTHCHECK_URL_ENV, "https://hc.example/abc")

    def boom(url, timeout):
        raise RuntimeError("network down")

    monkeypatch.setattr(observability.requests, "get", boom)
    assert ping_healthcheck("success") is False  # swallowed → False, no raise


def test_ping_healthcheck_non_2xx_is_false(monkeypatch):
    monkeypatch.setenv(HEALTHCHECK_URL_ENV, "https://hc.example/abc")
    monkeypatch.setattr(observability.requests, "get", lambda url, timeout: _Resp(500))
    assert ping_healthcheck("success") is False


def test_send_alert_noop_when_env_unset(monkeypatch):
    monkeypatch.delenv(ALERT_WEBHOOK_ENV, raising=False)
    monkeypatch.setattr(observability.requests, "post", _Boom())
    assert send_alert("RED — cron paused") is False  # no request constructed


def test_send_alert_posts_text_payload_and_returns_true(monkeypatch):
    monkeypatch.setenv(ALERT_WEBHOOK_ENV, "https://hooks.slack.test/xyz")
    captured: dict = {}

    def fake_post(url, json, timeout):
        captured["url"] = url
        captured["json"] = json
        return _Resp(200)

    monkeypatch.setattr(observability.requests, "post", fake_post)
    assert send_alert("RED — cron paused") is True
    assert captured["url"] == "https://hooks.slack.test/xyz"
    assert captured["json"] == {"text": "RED — cron paused"}


def test_send_alert_swallows_exception(monkeypatch):
    monkeypatch.setenv(ALERT_WEBHOOK_ENV, "https://hooks.slack.test/xyz")

    def boom(url, json, timeout):
        raise RuntimeError("network down")

    monkeypatch.setattr(observability.requests, "post", boom)
    assert send_alert("x") is False


def test_explicit_url_arg_overrides_unset_env(monkeypatch):
    # passing url/webhook explicitly must still fire even with env unset
    monkeypatch.delenv(HEALTHCHECK_URL_ENV, raising=False)
    monkeypatch.setattr(observability.requests, "get", lambda url, timeout: _Resp(200))
    assert ping_healthcheck("success", url="https://hc.example/abc") is True


# --- per-run signal collector (Phase 0: degraded sources must leave a trace) --------

@pytest.fixture(autouse=True)
def _clean_signals():
    """Every test starts with an empty collector — it is module-level state."""
    observability.reset_run_signals()
    yield
    observability.reset_run_signals()


def test_record_source_accumulates_calls_and_failures():
    observability.record_source("fred", ok=True)
    observability.record_source("fred", ok=True)
    observability.record_source("fred", ok=False, error=ValueError("429"))
    sig = observability.drain_run_signals()
    assert sig["sources"]["fred"] == {
        "calls": 3, "failures": 1, "degraded": False, "error": "429",
    }


def test_record_source_degraded_is_sticky_and_keeps_first_error():
    observability.record_source("treasury", ok=False, degraded=True, error="timeout")
    observability.record_source("treasury", ok=True)  # a later success must not clear it
    s = observability.drain_run_signals()["sources"]["treasury"]
    assert s["degraded"] is True
    assert s["error"] == "timeout"  # FIRST error is kept


def test_drain_resets_so_the_next_run_starts_clean():
    observability.record_source("gold_api_price", ok=False, degraded=True)
    observability.record_blanked("vix")
    first = observability.drain_run_signals()
    assert first["sources"] and first["indicators_blanked"] == ["vix"]
    assert observability.drain_run_signals() == {"sources": {}, "indicators_blanked": [], "llm_usage": []}


def test_record_blanked_dedupes():
    observability.record_blanked("vix")
    observability.record_blanked("vix")
    observability.record_blanked("gold_spot")
    assert observability.drain_run_signals()["indicators_blanked"] == ["vix", "gold_spot"]


def test_record_source_never_raises_on_bad_input():
    # Monitoring must never break a run (PRD §397) — even given nonsense.
    observability.record_source(None, ok=True)          # type: ignore[arg-type]
    observability.record_blanked(None)                  # type: ignore[arg-type]


def test_run_record_reports_degraded_sources():
    observability.record_source("fred", ok=True)
    observability.record_source("treasury", ok=True, degraded=True)   # empty tip
    observability.record_source("gold_api_price", ok=False, error="boom")
    rec = record_from(REQUEST, _valid_final(), run_label="manual", prompt_version="1.3.1",
                      latency_ms=5, timestamp="2026-06-17T10:15:00-04:00")
    assert rec.degraded_sources() == ["gold_api_price", "treasury"]  # fred is healthy
    assert rec.sources["fred"]["calls"] == 1


def test_record_from_drains_signals_into_the_audit_row():
    observability.record_source("treasury", ok=False, degraded=True, error="dns")
    observability.record_blanked("nasdaq")
    rec = record_from(REQUEST, _valid_final(), run_label="manual", prompt_version="1.3.1",
                      latency_ms=5, timestamp="2026-06-17T10:15:00-04:00")
    assert rec.sources["treasury"]["failures"] == 1
    assert rec.indicators_blanked == ["nasdaq"]
    # and the collector is now empty for the next run
    assert observability.drain_run_signals() == {"sources": {}, "indicators_blanked": [], "llm_usage": []}


def test_llm_usage_lands_in_the_audit_row():
    # ADR-007 cost accounting: every Claude call's response.usage reaches logs/runs.jsonl.
    observability.record_llm_usage(model="anthropic.claude-opus-5-5", input_tokens=1200,
                                   output_tokens=300, latency_ms=900, stop_reason="max_tokens")
    observability.record_llm_usage(model="anthropic.claude-haiku-5-5", input_tokens=1200,
                                   output_tokens=150, latency_ms=400, stop_reason="end_turn")
    rec = record_from(REQUEST, _valid_final(), run_label="manual", prompt_version="1.5",
                      latency_ms=5, timestamp="2026-06-17T10:15:00-04:00")
    assert [u["model"] for u in rec.llm_usage] == ["anthropic.claude-opus-5-5",
                                                   "anthropic.claude-haiku-5-5"]
    assert rec.to_json()["llm_usage"][0]["output_tokens"] == 300
    assert observability.drain_run_signals()["llm_usage"] == []


def test_error_record_also_carries_signals():
    # The crash path is exactly where source health matters most.
    observability.record_source("fred", ok=False, degraded=True, error="HTTP 500")
    rec = observability.error_record(run_label="AM", prompt_version="1.4.2",
                                     timestamp="2026-06-17T10:15:00-04:00", error="boom")
    assert rec.source == "error"
    assert rec.degraded_sources() == ["fred"]


# --- named checks + degradation alerting (Phase 1) ---------------------------------

def test_ping_check_is_noop_when_named_url_unset(monkeypatch):
    # CRITICAL: a missing named URL must NOT fall back to $HEALTHCHECKS_URL, or a quality
    # failure would /fail the AM/PM dead-man switch and raise a false outage alarm.
    monkeypatch.setenv(HEALTHCHECK_URL_ENV, "https://hc.example/main")
    monkeypatch.delenv(f"{HEALTHCHECK_URL_ENV}_SOURCES", raising=False)
    called = []
    monkeypatch.setattr(observability.requests, "get", lambda *a, **k: called.append(a))
    assert observability.ping_check("sources", "fail") is False
    assert called == []  # requests was never touched


def test_ping_check_uses_the_named_url(monkeypatch):
    monkeypatch.setenv(f"{HEALTHCHECK_URL_ENV}_SOURCES", "https://hc.example/sources")
    seen = {}

    class _Resp:
        status_code = 200

    def _get(url, **kw):
        seen["url"] = url
        return _Resp()

    monkeypatch.setattr(observability.requests, "get", _get)
    assert observability.ping_check("sources", "fail") is True
    assert seen["url"] == "https://hc.example/sources/fail"


def _healthy_record() -> RunRecord:
    observability.reset_run_signals()
    observability.record_source("fred", ok=True)
    return record_from(REQUEST, _valid_final(), run_label="AM", prompt_version="1.4.2",
                       latency_ms=5, timestamp="2026-06-17T10:15:00-04:00")


def test_report_run_health_clean_run_has_no_reasons(monkeypatch):
    monkeypatch.setattr(observability, "ping_check", lambda *a, **k: True)
    monkeypatch.setattr(observability, "send_alert", lambda *a, **k: True)
    assert observability.report_run_health(_healthy_record()) == []


def test_report_run_health_flags_a_fallback_publish(monkeypatch):
    # The silent-success case: exit 0, dead-man pings green, but Bruce got a template card.
    pings: list[tuple] = []
    monkeypatch.setattr(observability, "ping_check", lambda n, s="success", **k: pings.append((n, s)))
    monkeypatch.setattr(observability, "send_alert", lambda *a, **k: True)
    observability.reset_run_signals()
    rec = record_from(REQUEST, _fallback_final(), run_label="AM", prompt_version="1.4.2",
                      latency_ms=5, timestamp="2026-06-17T10:15:00-04:00")
    reasons = observability.report_run_health(rec)
    assert any("source=fallback" in r for r in reasons)
    assert ("quality", "fail") in pings


def test_report_run_health_flags_degraded_sources_and_blanked_rows(monkeypatch):
    pings: list[tuple] = []
    alerts: list[str] = []
    monkeypatch.setattr(observability, "ping_check", lambda n, s="success", **k: pings.append((n, s)))
    monkeypatch.setattr(observability, "send_alert", lambda m, **k: alerts.append(m))
    observability.reset_run_signals()
    observability.record_source("treasury", ok=False, degraded=True, error="timeout")
    observability.record_blanked("gold_spot")
    rec = record_from(REQUEST, _valid_final(), run_label="PM", prompt_version="1.4.2",
                      latency_ms=5, timestamp="2026-06-17T17:30:00-04:00")
    reasons = observability.report_run_health(rec)
    assert any("treasury" in r for r in reasons)
    assert any("gold_spot" in r for r in reasons)
    assert ("sources", "fail") in pings
    assert ("quality", "success") in pings   # the briefing itself was fine
    assert len(alerts) == 1


def test_report_run_health_never_raises_on_a_broken_record(monkeypatch):
    monkeypatch.setattr(observability, "ping_check", lambda *a, **k: True)

    class Broken:
        run_label = "AM"
        source = "llm"

        def degraded_sources(self):
            raise RuntimeError("boom")

    assert observability.report_run_health(Broken()) == []  # swallowed, run survives


# --- 2026-08-14: page on the OUTCOME, not the fetch attempt ----------------------------------

def _health(monkeypatch, *, sources=(), blanked=(), stale=()):
    pings: list[tuple] = []
    alerts: list[str] = []
    monkeypatch.setattr(observability, "ping_check", lambda n, s="success", **k: pings.append((n, s)))
    monkeypatch.setattr(observability, "send_alert", lambda m, **k: alerts.append(m))
    observability.reset_run_signals()
    for name in sources:
        observability.record_source(name, ok=False, degraded=True, error="NameResolutionError")
    for b in blanked:
        observability.record_blanked(b)
    rec = record_from(REQUEST, _valid_final(), run_label="AM", prompt_version="1.5",
                      latency_ms=5, timestamp="2026-08-14T10:45:48-04:00",
                      stale_indicators=list(stale))
    return observability.report_run_health(rec), pings, alerts


def test_a_source_that_retried_and_still_delivered_does_not_page(monkeypatch):
    """THE 2026-08-14 INCIDENT, verbatim. gold-api's price endpoint failed DNS resolution; the
    history endpoint answered, so gold_spot published correct and current. Nothing was blanked
    and nothing aged out, yet 'Data Sources' went DOWN and paged hello@modeo.io. Four of the six
    degraded-source events ever logged are that same transient (08-07, 08-12, 08-14).
    The degradation must still be VISIBLE in the alert body — it just must not page."""
    reasons, pings, alerts = _health(monkeypatch, sources=["gold_api_price"])
    assert ("sources", "success") in pings          # <- the fix
    assert ("sources", "fail") not in pings
    assert any("gold_api_price" in r for r in reasons)  # still reported, not hidden


def test_a_row_that_actually_aged_out_still_pages(monkeypatch):
    """The blind spot the fix would otherwise open. Dropping degraded-sources from the predicate
    means a vendor down for DAYS must surface some other way: as its rows going stale. This uses
    ANY tier deliberately — record.any_stale rolls up CORE only (staleness.py:42), so a market
    row like gold_spot aging out would page nobody if we leaned on any_stale."""
    reasons, pings, alerts = _health(monkeypatch, sources=["gold_api_price"], stale=["gold_spot"])
    assert ("sources", "fail") in pings
    assert any("gold_spot" in r for r in reasons)
    assert len(alerts) == 1


def test_a_blanked_row_still_pages(monkeypatch):
    """Unchanged behaviour: a value the plausibility guard dropped is a published hole."""
    _, pings, _ = _health(monkeypatch, blanked=["sp500"])
    assert ("sources", "fail") in pings


def test_a_clean_run_pings_success_and_sends_no_alert(monkeypatch):
    reasons, pings, alerts = _health(monkeypatch)
    assert reasons == [] and alerts == []
    assert ("sources", "success") in pings and ("quality", "success") in pings


# --- rejected drafts survive into the audit row -----------------------------------------

def test_fallback_record_carries_the_rejected_drafts_and_still_validates():
    """`output` is what the reader saw; rejected_outputs is what the model said and lost.
    Optional in the schema (never required) so every pre-2026-09 row stays valid; an llm row
    carries []."""
    from pathlib import Path

    from jsonschema import Draft202012Validator

    from macro_pipeline.validator import FinalBriefing, ValidationResult
    bad = {"headline": "Brent rose to 96.02 USD on Sep 2.", "quotable_numbers": [],
           "whats_new": None, "regime_change": None}
    final = FinalBriefing("fallback", ValidationResult("invalid", [("O5", "x"), ("period", "y")]),
                          fallback_text="Regime: Soft landing.", rejected=[bad, bad])
    rec = record_from(REQUEST, final, run_label="PM", prompt_version="1.5",
                      latency_ms=3170, timestamp="2026-09-02T14:04:11-04:00")
    assert rec.rejected_outputs == [bad, bad]
    d = rec.to_json()
    assert d["rejected_outputs"] == [bad, bad] and d["output"] == "Regime: Soft landing."
    schema = json.loads(
        (Path(__file__).resolve().parents[1] / "schemas" / "run-record.schema.json").read_text())
    assert list(Draft202012Validator(schema).iter_errors(d)) == []
    # and a row WITHOUT the field (every row written before this change) is still valid
    old_row = {k: v for k, v in d.items() if k != "rejected_outputs"}
    assert list(Draft202012Validator(schema).iter_errors(old_row)) == []
    # llm runs carry nothing
    assert record_from(REQUEST, _valid_final(), run_label="AM", prompt_version="1.5",
                       latency_ms=5, timestamp="2026-09-02T10:46:00-04:00").rejected_outputs == []


# --- persistent degradation pages; a blip does not ------------------------------------

def _deg_row(names):
    return json.dumps({"timestamp": "t", "run_label": "AM", "source": "llm",
                       "sources": {n: {"calls": 1, "failures": 1, "degraded": True, "error": "x"} for n in names}})


def _deg_record(names):
    rec = _record()
    rec.sources = {n: {"calls": 1, "failures": 1, "degraded": True, "error": "x"} for n in names}
    return rec


def test_persistently_degraded_needs_k_consecutive_runs(tmp_path):
    from macro_pipeline.observability import persistently_degraded
    log = tmp_path / "runs.jsonl"
    # 3 prior runs with treasury dead + this run -> 4 straight -> pages
    log.write_text("\n".join(_deg_row(["treasury"]) for _ in range(3)) + "\n")
    assert persistently_degraded(_deg_record(["treasury"]), str(log), k=4) == ["treasury"]
    # a source that was fine on one of the prior runs is a blip, not dead
    log.write_text("\n".join([_deg_row(["treasury"]), _deg_row([]), _deg_row(["treasury"])]) + "\n")
    assert persistently_degraded(_deg_record(["treasury"]), str(log), k=4) == []
    # not enough history yet -> no page
    log.write_text(_deg_row(["treasury"]) + "\n")
    assert persistently_degraded(_deg_record(["treasury"]), str(log), k=4) == []


def test_persistently_degraded_fails_closed_on_unreadable_log(tmp_path):
    from macro_pipeline.observability import persistently_degraded
    assert persistently_degraded(_deg_record(["treasury"]), str(tmp_path / "nope.jsonl"), k=4) == []
