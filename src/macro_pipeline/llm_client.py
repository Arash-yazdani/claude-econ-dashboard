"""B3 LLM integration — Claude on Amazon Bedrock (bedrock-runtime client): Haiku 4.5 writer and judge.

`LLMClient` is the seam the orchestrator depends on, so tests inject a fake and never hit
the network. `generate_briefing()` assembles the prompt, calls the primary, and on any
failure falls over to the secondary (PRD q27 config). It returns the model's raw parsed
JSON — the B4 validator is still the real gate on shape/content. The regime is chosen by
classifier.classify before this module runs; the model narrates it and never picks it.

Decision record: docs/adr/ADR-007-claude-on-bedrock-writer.md. The original Gemini/Groq clients
are frozen in legacy_llm_client.py and used only by scripts/compare_models.py.

Request shape (bedrock-runtime, Claude Haiku 4.5; ADR-007 amendment 2026-10-22):
- structured output via one forced tool (`tool_choice` = the `emit` tool whose `input_schema` is
  the schema); the tool_use block's `input` is the result. On bedrock-runtime, `output_config.format`
  returns 400 "Invalid API version" and `output_config.effort` returns 400 "Extra inputs are not
  permitted" (probe 2026-10-22), so neither is sent;
- no `temperature` / `top_p` / `thinking`: kept off so the request also works on the 5.5 models;
- `max_tokens` 4096.
Why not Mantle: the account's Mantle endpoint refused every Claude model (docs/incidents/
2026-10-21-bedrock-claude-not-available-for-account.md); bedrock-runtime serves Haiku 4.5 through
the cross-region inference profiles below.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any, Optional, Protocol

from . import observability, prompt

# Cross-region inference profile ids (us. = US regions, global. = any region). Env overrides are
# read at construction time. Haiku 4.5 is the only Claude model with account quota as of
# 2026-10-22 (Haiku 5.5 quota 0, increase case open). Failover uses the other routing profile so a
# regional capacity problem cannot take both; it is the same model, so a model-level refusal still
# takes both. ponytail: swap FAILOVER/JUDGE to Haiku 5.5 or Sonnet once quota lands (env only).
PRIMARY_MODEL = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
FAILOVER_MODEL = "global.anthropic.claude-haiku-4-5-20251001-v1:0"
JUDGE_MODEL = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
JUDGE_FAILOVER_MODEL = "global.anthropic.claude-haiku-4-5-20251001-v1:0"
PRIMARY_MODEL_ENV = "CLAUDE_PRIMARY_MODEL"
FAILOVER_MODEL_ENV = "CLAUDE_FAILOVER_MODEL"
JUDGE_MODEL_ENV = "CLAUDE_JUDGE_MODEL"
JUDGE_FAILOVER_MODEL_ENV = "CLAUDE_JUDGE_FAILOVER_MODEL"
REGION_ENV = "AWS_REGION"

WRITER_MAX_TOKENS = 4096
JUDGE_MAX_TOKENS = 4096
EMIT_TOOL = "emit"

# stop_reasons that mean "no usable briefing": raise so generate_briefing runs the failover.
_FAILOVER_STOP_REASONS = frozenset({"refusal", "max_tokens"})

# BriefingOutput (docs/schemas/briefing-io-v1.0.md) in standard JSON Schema for structured
# outputs. Same fields/required set as the original GEMINI_RESPONSE_SCHEMA (equivalence is
# asserted in tests/test_llm_client.py). Length/count caps stay in the B4 validator.
BRIEFING_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "headline": {"type": ["string", "null"]},
        "quotable_numbers": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "indicator_id": {"type": "string"},
                    "sentence": {"type": "string"},
                },
                "required": ["indicator_id", "sentence"],
                "additionalProperties": False,
            },
        },
        "whats_new": {"type": ["string", "null"]},
        "regime_change": {"type": ["string", "null"]},
        "error": {"type": ["string", "null"]},
    },
    "required": ["headline", "quotable_numbers", "whats_new", "regime_change"],
    "additionalProperties": False,
}


class BriefingLLMError(RuntimeError):
    """Any failure producing/parsing a briefing from a single provider."""


@dataclass
class LLMResult:
    output: dict[str, Any]  # parsed BriefingOutput (unvalidated — B4 validates)
    model: str              # which provider/model produced it


class LLMClient(Protocol):
    name: str
    def generate(self, system: str, user: str) -> dict[str, Any]: ...


def bedrock_region() -> str:
    """AWS_REGION, or a clear error. Deliberately no default region and no boto-profile guess."""
    region = os.environ.get(REGION_ENV, "").strip()
    if not region:
        raise BriefingLLMError(f"{REGION_ENV} is not set; Claude on Bedrock needs an explicit region.")
    return region


class BedrockClaudeClient:
    """One Claude model on Amazon Bedrock via `anthropic.AnthropicBedrock` (bedrock-runtime).

    The SDK client is built lazily on the first `generate`, so constructing this class needs
    neither AWS credentials nor a region; tests inject `client=` (a fake exposing
    `messages.create`) and never touch the network.
    """

    def __init__(self, model: str = PRIMARY_MODEL, *, max_tokens: int = WRITER_MAX_TOKENS, schema: Optional[dict[str, Any]] = None,
                 client: Any = None, timeout: float = 120.0) -> None:
        self.model = model
        self.name = model  # lands in RunRecord.model / eval_log production_model
        self.max_tokens = max_tokens
        self.schema = schema or BRIEFING_JSON_SCHEMA
        self.timeout = timeout
        self._client = client

    def _sdk(self) -> Any:
        if self._client is None:
            from anthropic import AnthropicBedrock  # lazy: import + auth only on a live call
            self._client = AnthropicBedrock(aws_region=bedrock_region(), timeout=self.timeout)
        return self._client

    def request_kwargs(self, system: str, user: str) -> dict[str, Any]:
        return {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
            "tools": [{"name": EMIT_TOOL, "description": "Return the result in this exact shape.",
                       "input_schema": self.schema}],
            "tool_choice": {"type": "tool", "name": EMIT_TOOL},
        }

    def generate(self, system: str, user: str) -> dict[str, Any]:
        import anthropic

        started = time.perf_counter()
        try:
            resp = self._sdk().messages.create(**self.request_kwargs(system, user))
        except anthropic.RateLimitError as exc:
            raise BriefingLLMError(f"{self.model} rate limited (429)") from exc
        except anthropic.APIStatusError as exc:
            raise BriefingLLMError(f"{self.model} HTTP {exc.status_code}: {str(exc.message)[:160]}") from exc
        except anthropic.APIConnectionError as exc:
            raise BriefingLLMError(f"{self.model} connection failed: {exc}") from exc
        latency_ms = round((time.perf_counter() - started) * 1000)

        usage = getattr(resp, "usage", None)
        observability.record_llm_usage(
            model=self.model,
            input_tokens=getattr(usage, "input_tokens", None),
            output_tokens=getattr(usage, "output_tokens", None),
            latency_ms=latency_ms,
            stop_reason=getattr(resp, "stop_reason", None),
        )

        if resp.stop_reason in _FAILOVER_STOP_REASONS:
            raise BriefingLLMError(f"{self.model} stop_reason={resp.stop_reason}")
        obj = next((b.input for b in resp.content
                    if getattr(b, "type", None) == "tool_use" and getattr(b, "name", None) == EMIT_TOOL), None)
        if obj is None:
            raise BriefingLLMError(f"{self.model} returned no {EMIT_TOOL} tool call")
        if isinstance(obj, str):  # defensive: some transports hand back the input as a JSON string
            try:
                obj = json.loads(obj)
            except ValueError as exc:
                raise BriefingLLMError(f"{self.model} returned non-JSON: {obj[:160]!r}") from exc
        if not isinstance(obj, dict):
            raise BriefingLLMError(f"{self.model} returned a non-object: {str(obj)[:160]!r}")
        return obj


class ChainClient:
    """Try each client in order; `name` reports the one that answered. Used for the production
    failover so a Claude-wide failure (account block, model refusal) still has a different
    provider behind it. The us./global. pair alone only covers a regional outage."""

    def __init__(self, *clients: LLMClient) -> None:
        self.clients = clients
        self.name = clients[0].name

    def generate(self, system: str, user: str) -> dict[str, Any]:
        errors = []
        for c in self.clients:
            try:
                out = c.generate(system, user)
                self.name = c.name
                return out
            except Exception as exc:  # next provider
                errors.append(f"{c.name}: {exc}")
        raise BriefingLLMError("; ".join(errors))


def make_writer_clients(*, cross_provider: bool = False) -> tuple[LLMClient, LLMClient]:
    """Production writer pair: (Haiku 4.5 us. profile, Haiku 4.5 global. profile), env-overridable.
    cross_provider=True (run_daily only) appends the original Gemini client to the failover when
    GEMINI_API_KEY is set. Eval scripts keep Claude-only so their measurements stay clean."""
    primary = BedrockClaudeClient(os.environ.get(PRIMARY_MODEL_ENV) or PRIMARY_MODEL)
    failover: LLMClient = BedrockClaudeClient(os.environ.get(FAILOVER_MODEL_ENV) or FAILOVER_MODEL)
    if cross_provider and os.environ.get("GEMINI_API_KEY"):
        from .legacy_llm_client import GeminiClient
        failover = ChainClient(failover, GeminiClient())
    return primary, failover


def to_gemini_schema(schema: Any) -> Any:
    """JSON Schema -> Gemini responseSchema (OpenAPI subset): ["x","null"] becomes
    {"type": "x", "nullable": true}; additionalProperties is dropped (Gemini rejects it)."""
    if isinstance(schema, list):
        return [to_gemini_schema(x) for x in schema]
    if not isinstance(schema, dict):
        return schema
    out = {k: to_gemini_schema(v) for k, v in schema.items() if k != "additionalProperties"}
    if isinstance(out.get("type"), list):
        types = [t for t in out["type"] if t != "null"]
        out["type"] = types[0]
        if len(types) < len(schema["type"]):
            out["nullable"] = True
    return out


def make_judge_client(schema: dict[str, Any], *, failover: bool = False) -> LLMClient:
    """A judge on Claude Haiku 4.5 (us. profile; global. when `failover`).
    JUDGE_PROVIDER=gemini (+ GEMINI_API_KEY) puts the PRIMARY judge on Gemini 2.5 Flash so the
    judge is not the writer's model family; the failover judge stays Claude. Set only in the
    daily and weekly workflows; compare-models keeps the Claude judge for a stable baseline."""
    if not failover and os.environ.get("JUDGE_PROVIDER") == "gemini" and os.environ.get("GEMINI_API_KEY"):
        from .legacy_llm_client import GeminiClient
        return GeminiClient(temperature=0.0, response_schema=to_gemini_schema(schema),
                            max_output_tokens=JUDGE_MAX_TOKENS)
    model = (os.environ.get(JUDGE_FAILOVER_MODEL_ENV) or JUDGE_FAILOVER_MODEL) if failover else \
        (os.environ.get(JUDGE_MODEL_ENV) or JUDGE_MODEL)
    return BedrockClaudeClient(model, max_tokens=JUDGE_MAX_TOKENS, schema=schema)


def generate_briefing(
    briefing_request: dict[str, Any], *, primary: LLMClient, failover: Optional[LLMClient] = None
) -> LLMResult:
    """Assemble the prompt, call `primary`, fall over to `failover` on any error."""
    messages = prompt.build_messages(briefing_request)
    try:
        return LLMResult(output=primary.generate(messages["system"], messages["user"]),
                         model=primary.name)
    except Exception as primary_err:  # any primary failure should still try the failover
        if failover is None:
            raise primary_err if isinstance(primary_err, BriefingLLMError) else \
                BriefingLLMError(f"primary failed: {primary_err}") from primary_err
        try:
            return LLMResult(output=failover.generate(messages["system"], messages["user"]),
                             model=failover.name)
        except Exception as failover_err:
            raise BriefingLLMError(
                f"both providers failed — primary: {primary_err}; failover: {failover_err}"
            ) from failover_err
