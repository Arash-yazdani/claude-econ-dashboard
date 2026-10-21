"""B6 — the cron orchestrator (src/macro_pipeline/daily.py), fully OFFLINE.

The whole flow runs against conftest's FakeFredSource + small stub LLM clients that satisfy
the validator's LLMClient seam (name + generate(system, user) -> dict). No network, no keys,
deterministic. The live FRED/LLM run on a real device is B9, not B6.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from jsonschema import Draft202012Validator

from macro_pipeline import daily, observability
from macro_pipeline.daily import build_card, data_through, orchestrate, resolve_run_label
from macro_pipeline.fred_client import FredError
from macro_pipeline.models import IndicatorReading, PipelineResult
from macro_pipeline.validator import FinalBriefing, ValidationResult

from conftest import AS_OF

ET = ZoneInfo("America/New_York")
ROOT = Path(__file__).resolve().parents[1]
CARD_SCHEMA = json.loads((ROOT / "schemas" / "briefing-card.schema.json").read_text())


# --- stub LLM clients (satisfy llm_client.LLMClient: name + generate) ----------------------

# A valid, grounded briefing for the steady-state fixture (soft_landing, no release, no prior
# regime). Numbers (4.2 / 2.8) and the "May 2026" period are all present in the request, so it
# passes the B4 validator end-to-end.
GROUNDED_OUTPUT = {
    "headline": "Soft landing holds as the job market stays steady.",
    "quotable_numbers": [
        {"indicator_id": "unemployment", "sentence": "Unemployment held at 4.2 percent in May 2026."},
        {"indicator_id": "core_cpi_yoy", "sentence": "Core CPI was 2.8 percent in May 2026."},
    ],
    "whats_new": None,
    "regime_change": None,
}


class StubLLM:
    """Returns a fixed parsed BriefingOutput; records that it was called."""

    def __init__(self, name: str, output: dict):
        self.name = name
        self._output = output
        self.calls = 0

    def generate(self, system: str, user: str) -> dict:
        self.calls += 1
        return dict(self._output)


class RaisingLLM:
    """Raises on generate to force failover/fallback."""

    name = "raising-stub"

    def __init__(self) -> None:
        self.calls = 0

    def generate(self, system: str, user: str) -> dict:
        self.calls += 1
        raise RuntimeError("provider down")


@pytest.fixture(autouse=True)
def _isolate_logs(tmp_path, monkeypatch):
    """chdir into tmp so the default logs/runs.jsonl audit row lands under tmp, never the repo.

    (orchestrate calls observability.log_run with no path, so the default RUN_LOG_PATH —
    bound at def-time — applies; chdir is the clean redirect.)
    """
    monkeypatch.chdir(tmp_path)


def _log_path(tmp_path) -> Path:
    return tmp_path / observability.RUN_LOG_PATH


def _reading(id_, value, obs_date, period_label):
    return IndicatorReading(
        id=id_, display_name=id_, value=value, unit="percent",
        period_label=period_label, last_release_date="2026-06-11",
        observation_date=obs_date,
    )


# --- resolve_run_label (session window — robust to GitHub's late/dropped cron) -------------

@pytest.mark.parametrize("hour,minute,expected", [
    # AM session = ET 10:30 .. 13:59 — the 10:45 cron + late/buffer firings
    (10, 30, "AM"), (10, 45, "AM"), (11, 15, "AM"), (12, 15, "AM"), (13, 59, "AM"),
    # Before FRED has ingested the 08:30 ET releases (~10:10) -> skip. Raised from 09:30 on
    # 2026-08-12: a 09:45 run read June CPI on July CPI's release morning. 10:15 must skip too,
    # it sits inside FRED's ingest gap and would claim the session with pre-release data.
    (8, 45, None), (9, 0, None), (9, 30, None), (9, 45, None), (10, 15, None), (10, 29, None),
    # PM session = ET 14:00–22:59 (tolerates a late firing of the 17:30 cron — incl. the
    # real-world 19:16 / 20:09 ET firings that the old exact-hour guard skipped)
    (14, 0, "PM"), (17, 30, "PM"), (19, 16, "PM"), (20, 9, "PM"), (22, 59, "PM"),
    # overnight gap -> skip (don't publish at 3am)
    (23, 0, None), (0, 0, None), (3, 0, None), (6, 0, None),
])
def test_resolve_run_label(hour, minute, expected):
    now = datetime(2026, 6, 17, hour, minute, tzinfo=ET)
    assert resolve_run_label(now) == expected


# --- already_published (idempotent de-dupe so a session publishes exactly once) ------------

def _runlog(tmp_path, records):
    p = tmp_path / "runs.jsonl"
    p.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    return p

def test_already_published_non_string_timestamp_fails_open(tmp_path):
    # A corrupted runs.jsonl line with a non-string timestamp must not crash (PR #1: fromisoformat
    # raises TypeError on non-str, and this runs OUTSIDE main()'s catch-all) — it falls open.
    log = _runlog(tmp_path, [
        {"timestamp": 1234567890, "run_label": "AM", "source": "llm"},
    ])
    now = datetime(2026, 6, 17, 11, 15, tzinfo=ET)
    assert daily.already_published("AM", now, runlog=log) is False  # fail open -> allow publish

def test_already_published_true_for_same_day_label(tmp_path):
    log = _runlog(tmp_path, [
        {"timestamp": "2026-06-17T10:18:00-04:00", "run_label": "AM", "source": "llm"},
    ])
    now = datetime(2026, 6, 17, 11, 15, tzinfo=ET)          # a later AM-session firing same day
    assert daily.already_published("AM", now, runlog=log) is True

def test_already_published_false_for_other_session_or_day(tmp_path):
    log = _runlog(tmp_path, [
        {"timestamp": "2026-06-17T10:18:00-04:00", "run_label": "AM", "source": "llm"},
    ])
    pm = datetime(2026, 6, 17, 17, 30, tzinfo=ET)
    nextday = datetime(2026, 6, 18, 10, 15, tzinfo=ET)
    assert daily.already_published("PM", pm, runlog=log) is False       # different session
    assert daily.already_published("AM", nextday, runlog=log) is False  # different ET day

def test_already_published_ignores_failed_run_so_retry_can_happen(tmp_path):
    log = _runlog(tmp_path, [
        {"timestamp": "2026-06-17T10:18:00-04:00", "run_label": "AM", "source": "error",
         "error": "FRED down"},
    ])
    now = datetime(2026, 6, 17, 11, 15, tzinfo=ET)
    assert daily.already_published("AM", now, runlog=log) is False      # a failure never claims it

def test_already_published_missing_log_fails_open(tmp_path):
    assert daily.already_published("AM", datetime(2026, 6, 17, 10, 15, tzinfo=ET),
                                   runlog=tmp_path / "nope.jsonl") is False


# --- data_through --------------------------------------------------------------------------

def test_data_through_picks_freshest_non_null_observation():
    readings = [
        _reading("cpi_yoy", 2.9, "2026-05-01", "May 2026"),
        _reading("treasury_10y", 4.25, "2026-06-18", "2026-06-18"),
        _reading("fed_funds", 4.5, "2026-06-17", "2026-06-17"),
    ]
    assert data_through(readings) == "2026-06-18"


def test_data_through_empty_when_no_observation_dates():
    readings = [_reading("x", 1.0, None, "—")]
    assert data_through(readings) == ""


# --- build_card ----------------------------------------------------------------------------

def _result(regime="soft_landing", drivers=("unemployment", "core_cpi_yoy")):
    readings = [
        _reading("cpi_yoy", 2.9, "2026-05-01", "May 2026"),
        _reading("treasury_10y", 4.25, "2026-06-18", "2026-06-18"),
    ]
    return PipelineResult(ok=True, as_of_date="2026-06-19", readings=readings,
                          regime_today=regime, drivers=list(drivers), any_stale=False)


def test_build_card_llm_source_is_default_state():
    final = FinalBriefing("llm", ValidationResult("valid"), output=GROUNDED_OUTPUT, model="m")
    now = datetime(2026, 6, 17, 10, 15, tzinfo=ET)
    card = build_card(_result(), final, run_label="AM", now_et=now)

    assert card["state"] == "default"
    assert card["briefing"] == GROUNDED_OUTPUT
    assert card["fallback_text"] is None
    assert card["run_label"] == "AM"
    assert card["regime"] == "soft_landing"
    assert card["regime_label"] == "Soft landing"
    assert card["drivers"] == ["unemployment", "core_cpi_yoy"]  # display-only driver badges
    assert card["data_through"] == "2026-06-18"
    # generated_at carries the DST-correct ET offset (-04:00 in June).
    assert card["generated_at"] == "2026-06-17T10:15:00-04:00"


def test_build_card_fallback_source_is_fallback_state():
    final = FinalBriefing("fallback", None, fallback_text="Regime: Soft landing. CPI: 2.9%. Unemployment: 4.2%.")
    now = datetime(2026, 6, 17, 17, 30, tzinfo=ET)
    card = build_card(_result(), final, run_label="PM", now_et=now)

    assert card["state"] == "fallback"
    assert card["briefing"] is None
    assert card["fallback_text"].startswith("Regime: Soft landing.")
    assert card["run_label"] == "PM"
    assert card["drivers"] == ["unemployment", "core_cpi_yoy"]  # drivers ride both states


def test_build_card_unknown_regime_uses_empty_label():
    final = FinalBriefing("fallback", None, fallback_text="Regime: —. CPI: n/a. Unemployment: n/a.")
    now = datetime(2026, 6, 17, 10, 15, tzinfo=ET)
    card = build_card(_result(regime=None), final, run_label="AM", now_et=now)
    assert card["regime"] is None
    assert card["regime_label"] == ""


# --- orchestrate: end-to-end LLM path ------------------------------------------------------

def test_orchestrate_llm_end_to_end(source, tmp_path):
    primary = StubLLM("claude-stub", GROUNDED_OUTPUT)
    now = datetime(2026, 6, 17, 10, 15, tzinfo=ET)
    out_dir = tmp_path / "public"

    outcome = orchestrate(
        source=source, primary=primary, failover=None, as_of=AS_OF,
        run_label="AM", now_et=now, out_dir=out_dir, cache_path=str(tmp_path / "cache.json"),
    )

    assert outcome.published is True
    assert outcome.source == "llm"
    assert primary.calls == 1

    # Both feeds were written.
    briefing_path = out_dir / "briefing.json"
    indicators_path = out_dir / "indicators.json"
    assert briefing_path.exists() and indicators_path.exists()

    # The card on disk validates against the B5/B6 PWA card schema.
    card = json.loads(briefing_path.read_text())
    Draft202012Validator.check_schema(CARD_SCHEMA)
    assert list(Draft202012Validator(CARD_SCHEMA).iter_errors(card)) == []
    assert card["state"] == "default"
    assert card["briefing"] == GROUNDED_OUTPUT
    assert card["regime_label"] == "Soft landing"

    # A RunRecord was logged.
    log_path = _log_path(tmp_path)
    assert log_path.exists()
    rows = [json.loads(line) for line in log_path.read_text().splitlines() if line.strip()]
    assert len(rows) == 1
    assert rows[0]["source"] == "llm"
    assert rows[0]["run_label"] == "AM"
    assert outcome.record is not None and outcome.record.source == "llm"


def test_orchestrate_failover_to_fallback(source, tmp_path):
    # Primary raises and there is no failover -> both providers down -> template fallback card.
    primary = RaisingLLM()
    now = datetime(2026, 6, 17, 17, 30, tzinfo=ET)
    out_dir = tmp_path / "public"

    outcome = orchestrate(
        source=source, primary=primary, failover=None, as_of=AS_OF,
        run_label="PM", now_et=now, out_dir=out_dir, cache_path=str(tmp_path / "cache.json"),
    )

    assert outcome.published is True
    assert outcome.source == "fallback"
    card = json.loads((out_dir / "briefing.json").read_text())
    assert card["state"] == "fallback"
    assert card["briefing"] is None
    assert isinstance(card["fallback_text"], str)
    assert list(Draft202012Validator(CARD_SCHEMA).iter_errors(card)) == []


# --- orchestrate: a correct refusal renders a fallback-shaped card -------------------------

def test_orchestrate_refusal_is_fallback_card_but_audits_as_refusal(source, tmp_path):
    # The LLM returns the refusal envelope (a correct data-bail). The validator marks it
    # "refusal", so the published CARD is fallback-shaped (no briefing object) while the audit
    # row + DailyOutcome.source surface "refusal". Locks the docs/cron-b6.md card-states contract.
    from macro_pipeline.validator import REFUSAL_ENVELOPE

    primary = StubLLM("claude-stub", REFUSAL_ENVELOPE)
    now = datetime(2026, 6, 17, 10, 15, tzinfo=ET)
    out_dir = tmp_path / "public"

    outcome = orchestrate(
        source=source, primary=primary, failover=None, as_of=AS_OF,
        run_label="AM", now_et=now, out_dir=out_dir, cache_path=str(tmp_path / "cache.json"),
    )

    assert outcome.published is True
    assert outcome.source == "refusal"             # internal / audit only
    card = json.loads((out_dir / "briefing.json").read_text())
    assert card["state"] == "fallback"             # the ON-DISK card is fallback-shaped
    assert card["briefing"] is None
    assert isinstance(card["fallback_text"], str)
    assert list(Draft202012Validator(CARD_SCHEMA).iter_errors(card)) == []
    rows = [json.loads(line) for line in _log_path(tmp_path).read_text().splitlines() if line.strip()]
    assert rows[0]["source"] == "refusal" and rows[0]["validator_status"] == "refusal"


# --- orchestrate: deterministic fallback (both clients None) -------------------------------

def test_orchestrate_no_clients_renders_fallback(source, tmp_path):
    now = datetime(2026, 6, 17, 10, 15, tzinfo=ET)
    out_dir = tmp_path / "public"

    outcome = orchestrate(
        source=source, primary=None, failover=None, as_of=AS_OF,
        run_label="AM", now_et=now, out_dir=out_dir, cache_path=str(tmp_path / "cache.json"),
    )

    assert outcome.published is True
    assert outcome.source == "fallback"
    card = json.loads((out_dir / "briefing.json").read_text())
    assert card["state"] == "fallback"
    assert card["briefing"] is None
    assert card["fallback_text"].startswith("Regime: Soft landing.")
    assert list(Draft202012Validator(CARD_SCHEMA).iter_errors(card)) == []
    # No LLM was called -> llm_calls 0 in the audit row.
    assert outcome.record.llm_calls == 0


# --- orchestrate: FRED down preserves last-good --------------------------------------------

class FredDownSource:
    def fetch_series(self, series_id, units, limit):
        raise FredError("FRED 503")


def test_orchestrate_fred_down_preserves_last_good(tmp_path):
    out_dir = tmp_path / "public"
    out_dir.mkdir(parents=True)
    # A pre-existing last-good briefing.json that must NOT be overwritten.
    last_good = {"schema_version": 1, "state": "default", "sentinel": True}
    (out_dir / "briefing.json").write_text(json.dumps(last_good))

    now = datetime(2026, 6, 17, 10, 15, tzinfo=ET)
    outcome = orchestrate(
        source=FredDownSource(), primary=StubLLM("g", GROUNDED_OUTPUT), failover=None,
        as_of=AS_OF, run_label="AM", now_et=now, out_dir=out_dir,
        cache_path=str(tmp_path / "cache.json"),
    )

    assert outcome.published is False
    assert outcome.source == "error"
    assert "FRED" in outcome.reason
    # briefing.json untouched (last-good preserved).
    assert json.loads((out_dir / "briefing.json").read_text()) == last_good


# ── W3 end-to-end: an indeterminate day must degrade LEGIBLY ─────────────────────
# The failure this guards is silent: validator.REGIME_PLAIN has no entry for a new status ->
# build_card yields regime_label="" -> app.js hides the ENTIRE regime block, taking W1's basis
# line down with it. The card looks fine; it just quietly stops saying anything.

def test_indeterminate_card_shows_the_plain_label_not_the_raw_id():
    from macro_pipeline.models import PipelineResult
    from macro_pipeline.validator import FinalBriefing

    result = PipelineResult(ok=True, as_of_date="2026-08-06", readings=[],
                            regime_today="indeterminate", regime_yesterday="soft_landing",
                            drivers=[], rule="missing_data", basis=[])
    card = build_card(result, FinalBriefing(source="fallback", output=None,
                                            fallback_text="Regime: No call today. CPI: 3.5%."),
                      run_label="AM", now_et=datetime(2026, 8, 6, 9, 45, tzinfo=ZoneInfo("America/New_York")))

    assert card["regime"] == "indeterminate"
    assert card["regime_label"] == "No call today"      # NOT "" and NOT "indeterminate"
    assert card["regime_label"] not in ("", "indeterminate")
    assert card["state"] == "fallback"
    assert card["basis"] == []                          # nothing was tested; claim nothing
    assert card["drivers"] == []                        # no drivers for a call we did not make


def test_indeterminate_card_still_validates_against_the_card_schema():
    """The card schema must accept the status, or an indeterminate day fails to publish at all."""
    from macro_pipeline.models import PipelineResult
    from macro_pipeline.validator import FinalBriefing
    schema = json.loads((Path(__file__).resolve().parents[1]
                         / "schemas/briefing-card.schema.json").read_text())
    result = PipelineResult(ok=True, as_of_date="2026-08-06", readings=[],
                            regime_today="indeterminate", drivers=[], rule="unmatched", basis=[])
    card = build_card(result, FinalBriefing(source="fallback", output=None, fallback_text="x"),
                      run_label="PM", now_et=datetime(2026, 8, 6, 17, 30, tzinfo=ZoneInfo("America/New_York")))
    Draft202012Validator(schema).validate(card)
