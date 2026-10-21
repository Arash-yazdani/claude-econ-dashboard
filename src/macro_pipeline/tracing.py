"""LangSmith tracing — an OPTIONAL observability layer over the LLM seam.

Design constraints, in priority order:

1. **It must never break a run** (PRD §397, same rule as observability.py). Every call here
   swallows its own failures. If LangSmith is down, un-installed, or unconfigured, the briefing
   still publishes — tracing degrades to a no-op and nothing upstream notices.
2. **It must never cost money** (SO9 — no payment method, ever). We deliberately do NOT use
   LangSmith's *online evaluators*: they auto-upgrade every trace they touch to extended (400-day)
   retention, which is billable. Instead the judge runs in our own CI (Claude on Bedrock) and
   pushes its scores here as **feedback**, which is free. With no card on file the account is
   rate-limited at 5k traces/month rather than billed; we use ~150-200.
3. **It must be an extra, not a dependency.** `langsmith` is imported lazily inside functions,
   so `pip install -e .` and the whole test suite
   work without it.

The seam: `TracedClient` wraps anything satisfying llm_client.LLMClient (`generate(system, user)
-> dict`). Because judge.make_judge() returns a BedrockClaudeClient — the same shape — wrapping the
protocol captures production briefings, retries, provider failover AND judge calls in one place,
with zero changes to any caller.
"""

from __future__ import annotations

import os
import sys
from typing import Any, Optional

LANGSMITH_API_KEY_ENV = "LANGSMITH_API_KEY"
LANGSMITH_PROJECT_ENV = "LANGSMITH_PROJECT"
LANGSMITH_TRACING_ENV = "LANGSMITH_TRACING"  # the SDK's own master switch — @traceable itself
# checks this (or the legacy LANGCHAIN_TRACING_V2) before it will emit a span, so our gate must
# agree with it or "the key is set" and "tracing actually happens" quietly diverge.
DEFAULT_PROJECT = "macro-daily-briefing"

_WARNED = False


def _tracing_flag_set() -> bool:
    """LANGSMITH_TRACING as a boolean. Unset defaults to True (back-compat with the key-only gate
    this module shipped with first); an EXPLICIT false/0/no disables tracing even with a valid key
    present — e.g. to pause tracing temporarily without touching the secret."""
    val = os.environ.get(LANGSMITH_TRACING_ENV)
    if val is None:
        return True
    return val.strip().lower() not in ("false", "0", "no", "")


def enabled() -> bool:
    """True when tracing is configured (key + not explicitly disabled) AND the SDK is
    importable. Never raises."""
    if not os.environ.get(LANGSMITH_API_KEY_ENV):
        return False
    if not _tracing_flag_set():
        return False
    try:
        import langsmith  # noqa: F401 — lazy: optional [langsmith] extra
        return True
    except Exception:  # noqa: BLE001 — missing extra must not break the run
        _warn_once("langsmith SDK not installed; tracing disabled (pip install -e '.[langsmith]')")
        return False


def project() -> str:
    return os.environ.get(LANGSMITH_PROJECT_ENV) or DEFAULT_PROJECT


def _warn_once(msg: str) -> None:
    global _WARNED
    if not _WARNED:
        print(f"[tracing] {msg}", file=sys.stderr)
        _WARNED = True


def _client():
    """A langsmith.Client, or None. Never raises."""
    try:
        from langsmith import Client
        return Client()
    except Exception as exc:  # noqa: BLE001
        _warn_once(f"could not create LangSmith client: {exc}")
        return None


class TracedClient:
    """Passthrough LLMClient that mirrors each call into LangSmith as a child run.

    Satisfies the llm_client.LLMClient protocol (`name`, `generate`). On ANY tracing failure the
    underlying call still happens and its result is returned unchanged — tracing is observation,
    never control flow.
    """

    def __init__(self, inner: Any, *, run_type: str = "llm", tags: Optional[list[str]] = None):
        self.inner = inner
        self.name = getattr(inner, "name", "llm")
        self._run_type = run_type
        self._tags = list(tags or [])

    def generate(self, system: str, user: str) -> dict[str, Any]:
        if not enabled():
            return self.inner.generate(system, user)
        try:
            from langsmith import traceable
        except Exception:  # noqa: BLE001
            return self.inner.generate(system, user)

        @traceable(run_type=self._run_type, name=f"llm:{self.name}",
                   project_name=project(), tags=self._tags)
        def _call(system: str, user: str) -> dict[str, Any]:
            return self.inner.generate(system, user)

        try:
            return _call(system, user)
        except Exception:
            # A real provider error must propagate unchanged (generate_briefing needs it to fail
            # over). Only tracing-internal problems are swallowed, and @traceable re-raises the
            # inner exception as-is, so this is the provider's error.
            raise


def trace_run(name: str, *, inputs: dict[str, Any], outputs: dict[str, Any],
              metadata: Optional[dict[str, Any]] = None,
              tags: Optional[list[str]] = None) -> Optional[str]:
    """Emit one completed root run (the whole daily briefing) and return its run id.

    Called AFTER orchestrate() finishes, so a tracing hiccup cannot delay or break publishing.
    Returns None when tracing is off or anything fails.
    """
    if not enabled():
        return None
    client = _client()
    if client is None:
        return None
    try:
        import uuid
        from datetime import datetime, timezone

        run_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        client.create_run(
            id=run_id,
            name=name,
            run_type="chain",
            inputs=inputs,
            outputs=outputs,
            extra={"metadata": metadata or {}},
            tags=list(tags or []),
            project_name=project(),
            start_time=now,
            end_time=now,
        )
        return run_id
    except Exception as exc:  # noqa: BLE001
        _warn_once(f"trace_run failed: {exc}")
        return None


def log_feedback(run_id: Optional[str], key: str, *, score: Optional[float] = None,
                 value: Any = None, comment: Optional[str] = None) -> bool:
    """Attach one evaluator/human score to a run. This is how judge results reach LangSmith
    WITHOUT paying for online evaluators. Returns True on success; never raises."""
    if not run_id or not enabled():
        return False
    client = _client()
    if client is None:
        return False
    try:
        client.create_feedback(run_id, key=key, score=score, value=value, comment=comment)
        return True
    except Exception as exc:  # noqa: BLE001
        _warn_once(f"log_feedback({key}) failed: {exc}")
        return False


def upsert_example(dataset_name: str, *, inputs: dict[str, Any], outputs: dict[str, Any],
                   metadata: Optional[dict[str, Any]] = None) -> bool:
    """Promote one published briefing into a LangSmith **Dataset**.

    Why this exists: the free tier keeps *traces* for only 14 days, which would make the D+30
    annotation review impossible. Dataset examples are persistent objects and are not subject to
    trace retention, so the 1-day / 1-week / 1-month reviews all annotate examples, not traces.
    Never raises.
    """
    if not enabled():
        return False
    client = _client()
    if client is None:
        return False
    try:
        try:
            ds = client.read_dataset(dataset_name=dataset_name)
        except Exception:  # noqa: BLE001 — not found on first run
            ds = client.create_dataset(
                dataset_name=dataset_name,
                description="Published daily macro briefings — durable record for the "
                            "D+1 / D+7 / D+30 annotation reviews (survives 14-day trace retention).",
            )
        client.create_example(
            inputs=inputs, outputs=outputs, dataset_id=ds.id, metadata=metadata or {},
        )
        return True
    except Exception as exc:  # noqa: BLE001
        _warn_once(f"upsert_example failed: {exc}")
        return False
