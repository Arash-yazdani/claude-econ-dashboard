"""B8 — file-based observability instrumentation (PRD §339 audit + §397 monitoring).

The run-logging half of the all-free monitoring stack: one structured JSONL line per
briefing (input hash, output source, validator results, model id, prompt version,
latency, timestamp — indefinite retention, PRD §339) plus thin, opt-in shims for the
two free services (healthchecks.io cron ping, Slack/Gmail alert).

NON-NEGOTIABLE: monitoring must NEVER break a run (PRD §397). Every side-effecting call
here swallows its own failures and returns False rather than raising. When the relevant
env var is unset the network shims are pure no-ops — they do not even construct a request.

Env is read at CALL time via os.environ.get (real env wins — _env.py / ADR-001), so tests
can monkeypatch.

Sentry was removed 2026-08-06 (never installed, never configured, superseded). Every
dominant failure here is an ABSENCE — the cron did not fire, it self-skipped, a source
silently degraded — and an exception tracker is structurally blind to absence. The rule
above guarantees it: code that never raises gives an exception tracker nothing to see.
healthchecks.io is the right instrument for this system.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import requests

RUN_LOG_PATH = "logs/runs.jsonl"
HEALTHCHECK_URL_ENV = "HEALTHCHECKS_URL"
ALERT_WEBHOOK_ENV = "ALERT_SLACK_WEBHOOK"



@dataclass
class RunRecord:
    """One audited briefing run (PRD §339). Field order is the schema/contract order;
    keep schemas/run-record.schema.json in lockstep."""

    timestamp: str            # ISO-8601 seconds precision, e.g. "2026-06-17T10:15:00-04:00"
    run_label: str            # "AM" | "PM" | "manual"
    as_of_date: Optional[str]
    input_hash: str           # sha256 hex of the canonical BriefingRequest (see input_hash())
    source: str               # "llm" | "fallback" | "refusal"
    model: Optional[str]      # production model id; None on fallback
    prompt_version: str
    validator_status: str     # "valid" | "refusal" | "invalid" | "skipped"
    validator_failed: list[str] = field(default_factory=list)  # failed check ids; [] when none
    regime_today: Optional[str] = None
    regime_yesterday: Optional[str] = None
    drivers: list[str] = field(default_factory=list)
    any_stale: bool = False
    latency_ms: Optional[int] = None
    llm_calls: int = 0        # provider calls this run (quota accounting)
    error: Optional[str] = None  # short error string when the run errored, else None
    output: Any = None        # §339 "output": the rendered briefing (validated dict) or fallback text
    # Per-source health for this run: {name: {calls, failures, degraded, error}}. Without this a
    # degraded vendor is INVISIBLE — Treasury down silently reverts to FRED's day-old tip, gold-api
    # down silently renders "—". Populated via record_source() at each swallow point.
    sources: dict[str, Any] = field(default_factory=dict)
    # Display-tier indicator ids blanked by the plausibility guard (pipeline.py). Also silent today.
    indicators_blanked: list[str] = field(default_factory=list)
    # Ids of every reading past its own stale_after_days, ANY tier. Wider on purpose than
    # any_stale, which rolls up core only for the UI banner (staleness.py:42): this is the
    # alerting predicate, and a market row aging out because its vendor is down is exactly the
    # thing that must still page once a transient fetch failure no longer does.
    stale_indicators: list[str] = field(default_factory=list)
    # The releases the LLM was actually shown. Stored because the audit row keeps only the input
    # HASH, so anything rebuilding the request later cannot recover them — and the C4 criterion
    # ("whats_new present IFF a release fired") is unjudgeable without them. Omitting it made the
    # daily eval score a CORRECT whats_new as a C4 failure on every release day.
    releases_last_24h: list[Any] = field(default_factory=list)
    # The LLM drafts the validator REJECTED on a fallback run (attempt order). `output` holds
    # what the reader SAW (the card text); this holds what the model SAID and lost — the one
    # thing the 2026-08-25 / 09-02 fallback diagnoses did not have. [] on llm/refusal/error rows.
    # Appended LAST: field order is the run-record schema contract. Optional in the schema, never
    # required, so every row written before 2026-09 stays valid.
    rejected_outputs: list[Any] = field(default_factory=list)
    # One entry per Claude call this run (attempt order, retries and failover included):
    # {model, input_tokens, output_tokens, latency_ms, stop_reason} from response.usage.
    # Cost accounting for ADR-007. Optional in the schema, so rows from the Gemini era stay valid.
    llm_usage: list[Any] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        """Plain, JSON-serializable dict of all fields (the JSONL line / audit row)."""
        return {
            "timestamp": self.timestamp,
            "run_label": self.run_label,
            "as_of_date": self.as_of_date,
            "input_hash": self.input_hash,
            "source": self.source,
            "model": self.model,
            "prompt_version": self.prompt_version,
            "validator_status": self.validator_status,
            "validator_failed": list(self.validator_failed),
            "regime_today": self.regime_today,
            "regime_yesterday": self.regime_yesterday,
            "drivers": list(self.drivers),
            "any_stale": self.any_stale,
            "latency_ms": self.latency_ms,
            "llm_calls": self.llm_calls,
            "error": self.error,
            "output": self.output,
            "sources": dict(self.sources),
            "indicators_blanked": list(self.indicators_blanked),
            "stale_indicators": list(self.stale_indicators),
            "releases_last_24h": list(self.releases_last_24h),
            "rejected_outputs": list(self.rejected_outputs),
            "llm_usage": list(self.llm_usage),
        }

    def degraded_sources(self) -> list[str]:
        """Names of sources that failed or degraded this run — the alerting predicate."""
        return sorted(
            name for name, s in self.sources.items()
            if s.get("degraded") or s.get("failures", 0) > 0
        )


# ── per-run signal collector ────────────────────────────────────────────────────
# A run is one single-threaded process, so a module-level collector is the least invasive
# way to get degradation signals out of the deep swallow points (treasury/gold/plausibility)
# and into the audit row. record_* never raises; drain_run_signals() resets for the next run.
_RUN_SIGNALS: dict[str, Any] = {"sources": {}, "indicators_blanked": [], "llm_usage": []}


def record_source(name: str, *, ok: bool, degraded: bool = False,
                  error: Optional[BaseException | str] = None) -> None:
    """Record one external-source call outcome for this run. Accumulates: `calls` counts every
    call, `failures` counts non-ok ones, `degraded` is sticky (we did not get the good data),
    `error` keeps the FIRST error string. Never raises — this is monitoring (PRD §397)."""
    try:
        s = _RUN_SIGNALS["sources"].setdefault(
            name, {"calls": 0, "failures": 0, "degraded": False, "error": None}
        )
        s["calls"] += 1
        if not ok:
            s["failures"] += 1
        if degraded:
            s["degraded"] = True
        if error is not None and not s["error"]:
            s["error"] = str(error)[:140]
    except Exception:  # noqa: BLE001
        pass


def record_blanked(indicator_id: str) -> None:
    """Record a display-tier indicator blanked by the plausibility guard. Never raises."""
    try:
        blanked = _RUN_SIGNALS["indicators_blanked"]
        if indicator_id not in blanked:
            blanked.append(indicator_id)
    except Exception:  # noqa: BLE001
        pass


def record_llm_usage(*, model: str, input_tokens: Optional[int], output_tokens: Optional[int],
                     latency_ms: Optional[int], stop_reason: Optional[str]) -> None:
    """Record one LLM call's usage (from response.usage) for this run. Never raises."""
    try:
        _RUN_SIGNALS["llm_usage"].append({
            "model": model, "input_tokens": input_tokens, "output_tokens": output_tokens,
            "latency_ms": latency_ms, "stop_reason": stop_reason,
        })
    except Exception:  # noqa: BLE001
        pass


def drain_run_signals() -> dict[str, Any]:
    """Return this run's collected signals and reset the collector for the next run."""
    snapshot = {
        "sources": {k: dict(v) for k, v in _RUN_SIGNALS["sources"].items()},
        "indicators_blanked": list(_RUN_SIGNALS["indicators_blanked"]),
        "llm_usage": [dict(u) for u in _RUN_SIGNALS["llm_usage"]],
    }
    reset_run_signals()
    return snapshot


def reset_run_signals() -> None:
    """Clear the collector (call at run start; tests use it for isolation)."""
    _RUN_SIGNALS["sources"] = {}
    _RUN_SIGNALS["indicators_blanked"] = []
    _RUN_SIGNALS["llm_usage"] = []


def input_hash(request: dict) -> str:
    """Stable sha256 hex digest of a BriefingRequest, independent of dict key order
    (sort_keys=True). The §339 audit input hash — full 64-char digest."""
    canonical = json.dumps(request, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def record_from(
    request: dict, final, *, run_label: str, prompt_version: str,
    latency_ms: Optional[int], llm_calls: int = 0,
    timestamp: str, error: Optional[str] = None, any_stale: Optional[bool] = None,
    signals: Optional[dict[str, Any]] = None,
    stale_indicators: Optional[list[str]] = None,
) -> RunRecord:
    """Build a RunRecord from a BriefingRequest dict + a validator.FinalBriefing.

    A correct refusal (final.validation.status == "refusal", which the validator returns
    with final.source == "fallback") is surfaced as source == "refusal" so a benign
    data-bail reads differently from a quality fallback in the audit log.

    `any_stale` is passed EXPLICITLY by the caller: it is a per-run audit signal, not part of
    the LLM-facing BriefingRequest, so PipelineResult.briefing_request() omits it by design.
    We fall back to request.get("any_stale") only if a caller injected it into the dict.
    """
    validation = getattr(final, "validation", None)
    if validation is not None and getattr(validation, "status", None) == "refusal":
        source = "refusal"
    else:
        source = final.source
    validator_status = validation.status if validation is not None else "skipped"
    validator_failed = [cid for cid, _ in validation.failed] if validation is not None else []
    # §339 "output": the rendered briefing the user actually saw — the validated dict on an LLM
    # run, else the deterministic fallback/refusal card text. Without it the audit log cannot
    # show which number was quoted (PRD §386 P1 triage: "wrong number quoted by Bruce").
    output = getattr(final, "output", None)
    if output is None:
        output = getattr(final, "fallback_text", None)
    stale = any_stale if any_stale is not None else request.get("any_stale", False)
    # Drain the per-run collector unless the caller supplied signals explicitly (tests do).
    sig = signals if signals is not None else drain_run_signals()
    return RunRecord(
        sources=sig.get("sources", {}),
        indicators_blanked=sig.get("indicators_blanked", []),
        stale_indicators=list(stale_indicators or []),
        releases_last_24h=list(request.get("releases_last_24h", []) or []),
        rejected_outputs=list(getattr(final, "rejected", None) or []),
        llm_usage=list(sig.get("llm_usage", [])),
        timestamp=timestamp,
        run_label=run_label,
        as_of_date=request.get("as_of_date"),
        input_hash=input_hash(request),
        source=source,
        model=getattr(final, "model", None),
        prompt_version=prompt_version,
        validator_status=validator_status,
        validator_failed=validator_failed,
        regime_today=request.get("regime_today"),
        regime_yesterday=request.get("regime_yesterday"),
        drivers=list(request.get("drivers", [])),
        any_stale=stale,
        latency_ms=latency_ms,
        llm_calls=llm_calls,
        error=error,
        output=output,
    )


def error_record(*, run_label: str, prompt_version: str, timestamp: str, error: str,
                 as_of_date: Optional[str] = None,
                 signals: Optional[dict[str, Any]] = None) -> RunRecord:
    """A minimal audit row for a run that crashed BEFORE a briefing was produced.

    PRD §339 wants EVERY run logged, including failures — without this the failure path leaves
    no audit trail and RunRecord.error is dead. source/validator_status are "error" and
    input_hash is empty (no request to hash). The cron's crash handler logs one of these.
    """
    sig = signals if signals is not None else drain_run_signals()
    return RunRecord(
        timestamp=timestamp,
        run_label=run_label,
        as_of_date=as_of_date,
        input_hash="",
        source="error",
        model=None,
        prompt_version=prompt_version,
        validator_status="error",
        error=error,
        sources=sig.get("sources", {}),
        indicators_blanked=sig.get("indicators_blanked", []),
    )


def log_run(record: RunRecord, path: str | Path = RUN_LOG_PATH) -> bool:
    """Append exactly one JSON line to the run log (mkdir parents). Returns True on success.

    Monitoring never breaks the run: on ANY exception, warn to stderr
    and return False (PRD §397).
    """
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record.to_json(), ensure_ascii=False) + "\n")
        return True
    except Exception as exc:  # noqa: BLE001 — monitoring must not raise into the run
        print(f"[observability] log_run failed: {exc}", file=sys.stderr)
        return False


def ping_healthcheck(state: str = "success", *, url: Optional[str] = None, timeout: float = 5.0) -> bool:
    """Ping healthchecks.io (cron liveness). url defaults to $HEALTHCHECKS_URL; if unset/empty
    this is a pure no-op (returns False WITHOUT touching requests). state maps to the URL
    suffix: start -> "/start", success -> "", fail -> "/fail". Swallows all errors -> False."""
    url = url if url is not None else os.environ.get(HEALTHCHECK_URL_ENV)
    if not url:
        return False
    suffix = {"start": "/start", "success": "", "fail": "/fail"}.get(state, "")
    try:
        resp = requests.get(url + suffix, timeout=timeout)
        return 200 <= resp.status_code < 300
    except Exception:  # noqa: BLE001 — a missed ping must never break the run
        return False


def healthcheck_url(name: str = "") -> Optional[str]:
    """Resolve one check's ping URL. "" -> $HEALTHCHECKS_URL (the AM/PM dead-man switch);
    a name -> $HEALTHCHECKS_URL_<NAME>, e.g. "sources" -> $HEALTHCHECKS_URL_SOURCES."""
    env = HEALTHCHECK_URL_ENV if not name else f"{HEALTHCHECK_URL_ENV}_{name.upper()}"
    return os.environ.get(env) or None


def ping_check(name: str, state: str = "success", *, timeout: float = 5.0) -> bool:
    """Ping a NAMED healthchecks.io check. Unlike ping_healthcheck this never falls back to
    $HEALTHCHECKS_URL — a missing named URL is a silent no-op, so a misconfigured quality check
    can never /fail the AM/PM dead-man switch and raise a false outage alarm."""
    url = healthcheck_url(name)
    if not url:
        return False
    return ping_healthcheck(state, url=url, timeout=timeout)


PERSISTENT_DEGRADED_RUNS = 4  # two full sessions of the same source failing = not a blip


def persistently_degraded(record: "RunRecord", runlog: str = RUN_LOG_PATH,
                          k: int = PERSISTENT_DEGRADED_RUNS) -> list[str]:
    """Sources degraded on THIS run and on each of the previous k-1 logged runs.

    The alerting predicate pages on the OUTCOME, so a degraded-but-delivered source only ever
    reaches the Slack body (d5243e4) — which is right for a one-run DNS blip and wrong for a
    dead origin: the Treasury graft failed 22 consecutive runs (2026-08-24 -> 09-03), the 10Y/2Y/
    TIPS rows served FRED's day-late values for ten days, and nothing distinguished run 22 from
    run 1. Persistence is the signal a blip cannot fake. Never raises; fails CLOSED to [] (no
    page) on any read error, since this is an alert, not a publish gate.
    """
    try:
        current = set(record.degraded_sources())
        if not current or k <= 1:
            return sorted(current) if k <= 1 else []
        lines = [ln for ln in Path(runlog).read_text(encoding="utf-8").splitlines() if ln.strip()]
        prior = [json.loads(ln) for ln in lines[-(k - 1):]]
        if len(prior) < k - 1:
            return []
        for row in prior:
            src = row.get("sources") or {}
            current &= {n for n, v in src.items() if v.get("degraded") or v.get("failures", 0) > 0}
            if not current:
                return []
        return sorted(current)
    except Exception:  # noqa: BLE001 — monitoring must never break a run
        return []


def report_run_health(record: "RunRecord") -> list[str]:
    """Fail the quality/source checks for anything that is a SILENT success today.

    Two failure classes the dead-man switch structurally cannot see, because the run exits 0
    and pings success either way:
      * a fallback/refusal was published instead of a real briefing (LLM outage);
      * a data source degraded (Treasury tip missing, gold-api down, a row blanked, core stale).
    Returns the human-readable reasons (empty == all clear). Never raises.
    """
    reasons: list[str] = []
    try:
        if record.source not in ("llm",):
            reasons.append(f"published source={record.source} (not a live LLM briefing)")
            ping_check("quality", "fail")
        else:
            ping_check("quality", "success")

        # Page on the OUTCOME, not on the attempt. A source that retried and still delivered
        # cost the reader nothing: gold-api's DNS resolution failed on 08-07, 08-12 and 08-14
        # and gold_spot published correct and current every time, because the second endpoint
        # answered. Four of the six degraded-source events ever logged are that one transient,
        # and an alert channel that pages on a vendor hiccup is one its reader learns to skip
        # (2026-08-04 incident, lesson 2). Degradation stays in the alert BODY for visibility.
        source_problems = record.degraded_sources()
        if source_problems:
            reasons.append(f"degraded sources: {', '.join(source_problems)}")
        if record.indicators_blanked:
            reasons.append(f"indicators blanked: {', '.join(record.indicators_blanked)}")
        # stale_rows is ANY tier, deliberately wider than record.any_stale, which rolls up
        # CORE only for the UI banner (staleness.py:42). Without it, dropping source_problems
        # from the predicate would trade today's noise for a blind spot: gold-api down for
        # days would age gold_spot on the card with nothing left to page.
        stale_rows = sorted(record.stale_indicators or ())
        if stale_rows:
            reasons.append(f"stale rows: {', '.join(stale_rows)}")
        # A source dead for PERSISTENT_DEGRADED_RUNS straight runs is an outcome, not an attempt:
        # the reader has been on the fallback tier for two sessions. Page it.
        dead = persistently_degraded(record)
        if dead:
            reasons.append(f"sources degraded {PERSISTENT_DEGRADED_RUNS}+ runs running: {', '.join(dead)}")
        if record.indicators_blanked or stale_rows or dead:
            ping_check("sources", "fail")
        else:
            ping_check("sources", "success")

        if reasons:
            send_alert(f"Daily briefing {record.run_label} degraded — " + "; ".join(reasons))
    except Exception as exc:  # noqa: BLE001 — monitoring must never break a run
        print(f"[observability] report_run_health failed: {exc}", file=sys.stderr)
    return reasons


def send_alert(message: str, *, webhook: Optional[str] = None, timeout: float = 5.0) -> bool:
    """Post an alert to the Slack incoming webhook. webhook defaults to $ALERT_SLACK_WEBHOOK;
    if unset/empty this is a pure no-op (returns False WITHOUT touching requests). Posts
    {"text": message}; swallows all errors -> False; True on 2xx."""
    webhook = webhook if webhook is not None else os.environ.get(ALERT_WEBHOOK_ENV)
    if not webhook:
        return False
    try:
        resp = requests.post(webhook, json={"text": message}, timeout=timeout)
        return 200 <= resp.status_code < 300
    except Exception:  # noqa: BLE001 — a failed alert must never break the run
        return False
