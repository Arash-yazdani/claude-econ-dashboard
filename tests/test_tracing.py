"""LangSmith tracing — the optional-extra contract (offline, no network).

The load-bearing property is NEGATIVE: with tracing unconfigured, un-installed, or broken,
every entrypoint must no-op and the wrapped LLM call must still return its real result.
Tracing is observation, never control flow (PRD §397).

These tests must pass whether or not `langsmith` is installed, so they never import it.
"""

from __future__ import annotations

import pytest

from macro_pipeline import tracing
from macro_pipeline.tracing import (
    LANGSMITH_API_KEY_ENV,
    LANGSMITH_PROJECT_ENV,
    LANGSMITH_TRACING_ENV,
    TracedClient,
)


class FakeClient:
    """Minimal llm_client.LLMClient stand-in."""

    name = "fake-model"

    def __init__(self):
        self.calls: list[tuple[str, str]] = []

    def generate(self, system: str, user: str) -> dict:
        self.calls.append((system, user))
        return {"headline": "ok"}


class BoomClient:
    name = "boom"

    def generate(self, system: str, user: str) -> dict:
        raise RuntimeError("provider 503")


@pytest.fixture(autouse=True)
def _no_key(monkeypatch):
    """Default every test to tracing-OFF; opt in explicitly where needed."""
    monkeypatch.delenv(LANGSMITH_API_KEY_ENV, raising=False)
    monkeypatch.delenv(LANGSMITH_PROJECT_ENV, raising=False)
    monkeypatch.delenv(LANGSMITH_TRACING_ENV, raising=False)
    tracing._WARNED = False


# --- the env gate ------------------------------------------------------------------

def test_disabled_without_api_key():
    assert tracing.enabled() is False


def test_project_defaults_and_respects_env(monkeypatch):
    assert tracing.project() == tracing.DEFAULT_PROJECT
    monkeypatch.setenv(LANGSMITH_PROJECT_ENV, "custom-proj")
    assert tracing.project() == "custom-proj"


# --- LANGSMITH_TRACING master switch -------------------------------------------------
# @traceable itself checks this env var before it will emit a span, so our gate must agree
# with it or "the key is set" and "tracing actually happens" quietly diverge.

def test_key_alone_enables_tracing_when_flag_unset(monkeypatch):
    """Back-compat: this module shipped key-only at first; unset defaults to on.

    enabled() also requires the langsmith SDK to be importable, and this file's contract is to
    pass with or without the optional extra — so assert the gate, and assert enabled() matches
    SDK AVAILABILITY (find_spec never imports it) rather than hard-coding True. Before this,
    the test failed on any venv without the extra while CI masked it (the install step is
    continue-on-error), so a green CI could hide a red laptop."""
    import importlib.util
    monkeypatch.setenv(LANGSMITH_API_KEY_ENV, "x")
    assert tracing._tracing_flag_set() is True
    sdk_available = importlib.util.find_spec("langsmith") is not None
    assert tracing.enabled() is sdk_available


def test_explicit_false_disables_tracing_even_with_a_valid_key(monkeypatch):
    monkeypatch.setenv(LANGSMITH_API_KEY_ENV, "x")
    monkeypatch.setenv(LANGSMITH_TRACING_ENV, "false")
    assert tracing.enabled() is False


@pytest.mark.parametrize("falsy", ["false", "False", "FALSE", "0", "no", ""])
def test_tracing_flag_recognizes_falsy_spellings(monkeypatch, falsy):
    monkeypatch.setenv(LANGSMITH_TRACING_ENV, falsy)
    assert tracing._tracing_flag_set() is False


@pytest.mark.parametrize("truthy", ["true", "True", "1", "yes"])
def test_tracing_flag_recognizes_truthy_spellings(monkeypatch, truthy):
    monkeypatch.setenv(LANGSMITH_TRACING_ENV, truthy)
    assert tracing._tracing_flag_set() is True


# --- every entrypoint no-ops when off ----------------------------------------------

def test_all_entrypoints_noop_when_disabled():
    assert tracing.trace_run("run", inputs={}, outputs={}) is None
    assert tracing.log_feedback("some-id", "c1", score=5.0) is False
    assert tracing.upsert_example("ds", inputs={}, outputs={}) is False


def test_log_feedback_noop_on_missing_run_id(monkeypatch):
    monkeypatch.setenv(LANGSMITH_API_KEY_ENV, "x")
    assert tracing.log_feedback(None, "c1", score=5.0) is False


# --- TracedClient: passthrough is sacred -------------------------------------------

def test_traced_client_passes_through_when_disabled():
    inner = FakeClient()
    out = TracedClient(inner).generate("sys", "usr")
    assert out == {"headline": "ok"}
    assert inner.calls == [("sys", "usr")]


def test_traced_client_preserves_name():
    assert TracedClient(FakeClient()).name == "fake-model"


def test_traced_client_still_calls_inner_when_sdk_import_fails(monkeypatch):
    """Key case: key IS set but the SDK is missing/broken → still call the model."""
    monkeypatch.setenv(LANGSMITH_API_KEY_ENV, "x")
    monkeypatch.setattr(tracing, "enabled", lambda: True)
    inner = FakeClient()
    # Simulate `from langsmith import traceable` blowing up.
    import builtins
    real_import = builtins.__import__

    def _boom(name, *a, **k):
        if name == "langsmith":
            raise ImportError("no langsmith")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", _boom)
    assert TracedClient(inner).generate("s", "u") == {"headline": "ok"}
    assert inner.calls == [("s", "u")]


def test_traced_client_propagates_provider_errors():
    """A real provider failure MUST still raise — generate_briefing needs it to fail over."""
    with pytest.raises(RuntimeError, match="provider 503"):
        TracedClient(BoomClient()).generate("s", "u")


# --- failures inside the tracing SDK never surface ---------------------------------

def test_trace_run_swallows_client_errors(monkeypatch):
    monkeypatch.setattr(tracing, "enabled", lambda: True)
    monkeypatch.setattr(tracing, "_client", lambda: None)
    assert tracing.trace_run("r", inputs={}, outputs={}) is None


def test_log_feedback_swallows_client_errors(monkeypatch):
    class Exploding:
        def create_feedback(self, *a, **k):
            raise RuntimeError("500 from langsmith")

    monkeypatch.setattr(tracing, "enabled", lambda: True)
    monkeypatch.setattr(tracing, "_client", lambda: Exploding())
    assert tracing.log_feedback("rid", "c1", score=4.0) is False


def test_upsert_example_swallows_client_errors(monkeypatch):
    class Exploding:
        def read_dataset(self, **k):
            raise RuntimeError("nope")

        def create_dataset(self, **k):
            raise RuntimeError("also nope")

    monkeypatch.setattr(tracing, "enabled", lambda: True)
    monkeypatch.setattr(tracing, "_client", lambda: Exploding())
    assert tracing.upsert_example("ds", inputs={}, outputs={}) is False
