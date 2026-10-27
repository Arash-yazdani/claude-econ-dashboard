"""B3 LLM orchestration — failover logic, output-shape conformance, and the Claude-on-Bedrock
client request shape (offline: a fake SDK client is injected, no AWS, no network)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from jsonschema import Draft202012Validator

import anthropic
import httpx2

from macro_pipeline import llm_client, observability
from macro_pipeline import legacy_llm_client as legacy
from macro_pipeline.judge import JUDGE_JSON_SCHEMA
from macro_pipeline.llm_client import (BRIEFING_JSON_SCHEMA, BedrockClaudeClient, BriefingLLMError,
                                       generate_briefing)
from macro_pipeline.persona_judge import BRUCE_JSON_SCHEMA

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = json.loads((ROOT / "tests" / "fixtures" / "regression-cases-v1.0.json").read_text())
T1 = next(c for c in FIXTURES["cases"] if c["id"] == "T1")["input"]
OUT_SCHEMA = json.loads((ROOT / "schemas" / "briefing-output.schema.json").read_text())

NORMAL = {
    "headline": "Soft landing, with cooling prices and a steady job market.",
    "quotable_numbers": [
        {"indicator_id": "cpi_yoy", "sentence": "CPI held at 2.9% in May 2026."},
        {"indicator_id": "unemployment", "sentence": "Unemployment steady at 4.2% in May 2026."},
    ],
    "whats_new": None,
    "regime_change": None,
}
REFUSAL = {"headline": None, "quotable_numbers": [], "whats_new": None,
           "regime_change": None, "error": "data_unavailable"}


class FakeLLM:
    def __init__(self, name: str, *, output=None, fail=False):
        self.name = name
        self._output = output
        self._fail = fail
        self.calls = 0

    def generate(self, system: str, user: str):
        self.calls += 1
        if self._fail:
            raise BriefingLLMError(f"{self.name} down")
        return self._output


def test_primary_used_when_healthy():
    primary, failover = FakeLLM("opus", output=NORMAL), FakeLLM("haiku", output=NORMAL)
    res = generate_briefing(T1, primary=primary, failover=failover)
    assert res.model == "opus"
    assert res.output == NORMAL
    assert failover.calls == 0  # failover untouched


def test_falls_over_when_primary_fails():
    primary, failover = FakeLLM("opus", fail=True), FakeLLM("haiku", output=NORMAL)
    res = generate_briefing(T1, primary=primary, failover=failover)
    assert res.model == "haiku"
    assert failover.calls == 1


def test_raises_when_both_fail():
    primary, failover = FakeLLM("opus", fail=True), FakeLLM("haiku", fail=True)
    with pytest.raises(BriefingLLMError, match="both providers failed"):
        generate_briefing(T1, primary=primary, failover=failover)


def test_propagates_when_no_failover_configured():
    with pytest.raises(BriefingLLMError):
        generate_briefing(T1, primary=FakeLLM("opus", fail=True))


def test_both_output_shapes_match_briefing_output_schema():
    validator = Draft202012Validator(OUT_SCHEMA)
    assert list(validator.iter_errors(NORMAL)) == []
    assert list(validator.iter_errors(REFUSAL)) == []


def test_legacy_parse_json_recovers_from_markdown_fence():
    fenced = '```json\n{"headline": null, "quotable_numbers": []}\n```'
    assert legacy._parse_json(fenced, "x") == {"headline": None, "quotable_numbers": []}


def test_legacy_parse_json_raises_on_garbage():
    with pytest.raises(legacy.BriefingLLMError):
        legacy._parse_json("no json here", "x")


# --- schema conversion: Gemini OpenAPI dialect -> standard JSON Schema -------------------------

def to_json_schema(g: dict) -> dict:
    """Reference converter: Gemini dialect (UPPERCASE types, nullable) -> JSON Schema with
    additionalProperties false on every object. Used only to prove the hand-written schemas."""
    t = g["type"].lower()
    out: dict = {"type": [t, "null"] if g.get("nullable") else t}
    if "enum" in g:
        out["enum"] = g["enum"]
    if t == "object":
        out["properties"] = {k: to_json_schema(v) for k, v in g["properties"].items()}
        if "required" in g:
            out["required"] = g["required"]
        out["additionalProperties"] = False
    if t == "array":
        out["items"] = to_json_schema(g["items"])
    return out


def _objects(schema: dict):
    if "object" in schema.get("type", ()) or schema.get("type") == "object":
        yield schema
    for sub in schema.get("properties", {}).values():
        yield from _objects(sub)
    if "items" in schema:
        yield from _objects(schema["items"])


def test_briefing_schema_is_exact_conversion_of_original_gemini_schema():
    assert to_json_schema(legacy.GEMINI_RESPONSE_SCHEMA) == BRIEFING_JSON_SCHEMA


@pytest.mark.parametrize("schema", [BRIEFING_JSON_SCHEMA, JUDGE_JSON_SCHEMA, BRUCE_JSON_SCHEMA])
def test_every_object_is_closed_and_valid_json_schema(schema):
    Draft202012Validator.check_schema(schema)
    for obj in _objects(schema):
        assert obj["additionalProperties"] is False
        assert set(obj["required"]) <= set(obj["properties"])


@pytest.mark.parametrize("schema,machine_copy", [
    (JUDGE_JSON_SCHEMA, "judge-verdict.schema.json"),
    (BRUCE_JSON_SCHEMA, "bruce-verdict.schema.json"),
])
def test_judge_schemas_match_their_locked_machine_copies(schema, machine_copy):
    locked = json.loads((ROOT / "schemas" / machine_copy).read_text())
    assert set(schema["properties"]) == set(locked["properties"])
    assert set(schema["required"]) == set(locked["required"])


def test_both_output_shapes_validate_against_claude_schema():
    validator = Draft202012Validator(BRIEFING_JSON_SCHEMA)
    assert list(validator.iter_errors(NORMAL)) == []
    assert list(validator.iter_errors(REFUSAL)) == []


# --- BedrockClaudeClient with an injected fake SDK client -------------------------------------

def _resp(payload=NORMAL, stop_reason="tool_use", text=None):
    """A forced-tool response; `text` replaces the tool input (a str exercises the JSON-string path)."""
    return SimpleNamespace(
        stop_reason=stop_reason,
        content=[SimpleNamespace(type="text", text="ok"),
                 SimpleNamespace(type="tool_use", name="emit", input=text if text is not None else payload)],
        usage=SimpleNamespace(input_tokens=1234, output_tokens=210),
    )


class FakeSDK:
    """Stands in for AnthropicBedrock: records kwargs, returns/raises queued items."""

    def __init__(self, *items):
        self.messages = self
        self.calls: list[dict] = []
        self._items = list(items)

    def create(self, **kwargs):
        self.calls.append(kwargs)
        item = self._items.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


def _claude(sdk, model=llm_client.PRIMARY_MODEL, **kw):
    return BedrockClaudeClient(model, client=sdk, **kw)


_REQ = httpx2.Request("POST", "https://bedrock-runtime.example.invalid/model/x/invoke")


def test_request_kwargs_have_no_sampling_or_thinking_params():
    sdk = FakeSDK(_resp())
    _claude(sdk).generate("sys", "usr")
    kw = sdk.calls[0]
    for banned in ("temperature", "top_p", "top_k", "thinking"):
        assert banned not in kw
    assert kw["model"] == "us.anthropic.claude-haiku-4-5-20251001-v1:0"
    assert kw["max_tokens"] == 4096
    assert kw["system"] == "sys"
    assert kw["messages"] == [{"role": "user", "content": "usr"}]
    assert "output_config" not in kw  # 400 on bedrock-runtime (probe 2026-10-22)
    assert kw["tools"][0]["name"] == "emit" and kw["tools"][0]["input_schema"] is BRIEFING_JSON_SCHEMA
    assert kw["tool_choice"] == {"type": "tool", "name": "emit"}


def test_judge_client_uses_haiku_and_judge_schema():
    from macro_pipeline import judge
    j = judge.make_judge()
    assert j.model == "us.anthropic.claude-haiku-4-5-20251001-v1:0"
    kw = j.request_kwargs("s", "u")
    assert kw["tools"][0]["input_schema"] is JUDGE_JSON_SCHEMA
    assert "temperature" not in kw and "thinking" not in kw


def test_persona_judge_failover_keeps_bruce_schema():
    from macro_pipeline import persona_judge
    f = persona_judge.make_bruce_judge_failover()
    assert f.model == "global.anthropic.claude-haiku-4-5-20251001-v1:0"
    assert f.request_kwargs("s", "u")["tools"][0]["input_schema"] is BRUCE_JSON_SCHEMA


def test_default_writer_pair_and_env_overrides(monkeypatch):
    for env in (llm_client.PRIMARY_MODEL_ENV, llm_client.FAILOVER_MODEL_ENV):
        monkeypatch.delenv(env, raising=False)
    p, f = llm_client.make_writer_clients()
    assert (p.model, f.model) == ("us.anthropic.claude-haiku-4-5-20251001-v1:0",
                                  "global.anthropic.claude-haiku-4-5-20251001-v1:0")
    monkeypatch.setenv(llm_client.PRIMARY_MODEL_ENV, "anthropic.custom-x")
    assert llm_client.make_writer_clients()[0].model == "anthropic.custom-x"


def test_parses_emit_tool_input_and_records_usage():
    observability.reset_run_signals()
    out = _claude(FakeSDK(_resp())).generate("s", "u")
    assert out == NORMAL
    usage = observability.drain_run_signals()["llm_usage"]
    assert len(usage) == 1
    u = usage[0]
    assert (u["model"], u["input_tokens"], u["output_tokens"], u["stop_reason"]) == \
        ("us.anthropic.claude-haiku-4-5-20251001-v1:0", 1234, 210, "tool_use")
    assert isinstance(u["latency_ms"], int)


@pytest.mark.parametrize("stop_reason", ["refusal", "max_tokens"])
def test_refusal_and_max_tokens_raise_so_failover_runs(stop_reason):
    primary = _claude(FakeSDK(_resp(stop_reason=stop_reason, text='{"headline": "trunc')))
    failover = _claude(FakeSDK(_resp()), model=llm_client.FAILOVER_MODEL)
    with pytest.raises(BriefingLLMError, match=stop_reason):
        primary.generate("s", "u")
    primary = _claude(FakeSDK(_resp(stop_reason=stop_reason)))
    res = generate_briefing(T1, primary=primary, failover=failover)
    assert res.model == "global.anthropic.claude-haiku-4-5-20251001-v1:0"
    assert res.output == NORMAL


@pytest.mark.parametrize("exc", [
    anthropic.RateLimitError("slow down", response=httpx2.Response(429, request=_REQ), body=None),
    anthropic.InternalServerError("boom", response=httpx2.Response(500, request=_REQ), body=None),
    anthropic.NotFoundError("no model", response=httpx2.Response(404, request=_REQ), body=None),
    anthropic.APIConnectionError(request=_REQ),
    anthropic.APITimeoutError(request=_REQ),
])
def test_sdk_errors_wrap_into_briefing_llm_error_and_fail_over(exc):
    with pytest.raises(BriefingLLMError):
        _claude(FakeSDK(exc)).generate("s", "u")
    res = generate_briefing(T1, primary=_claude(FakeSDK(exc)),
                            failover=_claude(FakeSDK(_resp()), model=llm_client.FAILOVER_MODEL))
    assert res.model == "global.anthropic.claude-haiku-4-5-20251001-v1:0"


def test_non_json_text_is_an_error():
    with pytest.raises(BriefingLLMError, match="non-JSON"):
        _claude(FakeSDK(_resp(text="not json"))).generate("s", "u")


def test_missing_emit_tool_call_is_an_error():
    resp = SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text="{}")],
                           usage=SimpleNamespace(input_tokens=1, output_tokens=1))
    with pytest.raises(BriefingLLMError, match="no emit tool call"):
        _claude(FakeSDK(resp)).generate("s", "u")


def test_missing_region_fails_clearly_without_network(monkeypatch):
    monkeypatch.delenv("AWS_REGION", raising=False)
    client = BedrockClaudeClient()  # constructing needs no AWS config (lazy)
    with pytest.raises(BriefingLLMError, match="AWS_REGION is not set"):
        client.generate("s", "u")


def test_chain_client_falls_through_to_next_provider():
    class Bad:
        name = "claude-global"
        def generate(self, s, u): raise RuntimeError("403")
    class Good:
        name = "gemini-2.5-flash"
        def generate(self, s, u): return {"headline": "ok"}
    c = llm_client.ChainClient(Bad(), Good())
    assert c.generate("s", "u") == {"headline": "ok"}
    assert c.name == "gemini-2.5-flash"


def test_chain_client_raises_with_every_error():
    class Bad:
        def __init__(self, n): self.name = n
        def generate(self, s, u): raise RuntimeError("down")
    c = llm_client.ChainClient(Bad("a"), Bad("b"))
    with pytest.raises(llm_client.BriefingLLMError, match="a: down; b: down"):
        c.generate("s", "u")


def test_cross_provider_failover_only_with_gemini_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert not isinstance(llm_client.make_writer_clients(cross_provider=True)[1], llm_client.ChainClient)
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    assert isinstance(llm_client.make_writer_clients(cross_provider=True)[1], llm_client.ChainClient)
    assert not isinstance(llm_client.make_writer_clients()[1], llm_client.ChainClient)


def test_to_gemini_schema_nullable_and_no_additional_properties():
    from macro_pipeline.judge import JUDGE_JSON_SCHEMA
    g = llm_client.to_gemini_schema(JUDGE_JSON_SCHEMA)
    assert g["properties"]["c1_quotability"] == {"type": "integer", "nullable": True}
    assert g["properties"]["c5_length_format"] == {"type": "integer"}
    assert "additionalProperties" not in g and "additionalProperties" not in g["properties"]["rationale"]
    assert g["required"] == JUDGE_JSON_SCHEMA["required"]


def test_judge_provider_gemini_only_for_primary(monkeypatch):
    monkeypatch.setenv("JUDGE_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    from macro_pipeline.legacy_llm_client import GeminiClient
    assert isinstance(llm_client.make_judge_client({"type": "object"}), GeminiClient)
    assert isinstance(llm_client.make_judge_client({"type": "object"}, failover=True), llm_client.BedrockClaudeClient)
    monkeypatch.delenv("JUDGE_PROVIDER")
    assert isinstance(llm_client.make_judge_client({"type": "object"}), llm_client.BedrockClaudeClient)
