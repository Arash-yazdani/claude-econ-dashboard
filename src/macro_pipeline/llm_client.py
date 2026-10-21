"""B3 LLM integration — Claude on Amazon Bedrock (Mantle client): Opus 5.5 writer, Haiku 5.5 failover.

`LLMClient` is the seam the orchestrator depends on, so tests inject a fake and never hit
the network. `generate_briefing()` assembles the prompt, calls the primary, and on any
failure falls over to the secondary (PRD q27 config). It returns the model's raw parsed
JSON — the B4 validator is still the real gate on shape/content. The regime is chosen by
classifier.classify before this module runs; the model narrates it and never picks it.

Decision record: docs/adr/ADR-007-claude-on-bedrock-writer.md. The original Gemini/Groq clients
are frozen in legacy_llm_client.py and used only by scripts/compare_models.py.

Request shape (Claude Opus 5.5 / Sonnet 5.5 / Haiku 5.5):
- no `temperature` / `top_p`: non-default sampling values return 400 on these models;
- no `thinking` param: thinking cannot be disabled (`{"type":"disabled"}` and `budget_tokens`
  both 400); depth is controlled with `output_config.effort` instead;
- `output_config.format` = json_schema, so the first text block is valid JSON for the schema;
- `max_tokens` 4096: thinking tokens count against it (the original 512 cap was hit by Gemini
  thinking — see legacy_llm_client.MAX_OUTPUT_TOKENS).
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any, Optional, Protocol

from . import observability, prompt

# Bedrock model ids take the `anthropic.` prefix. Env overrides are read at construction time.
# Writer is Haiku 5.5 (Arash, 2026-10-21: lowest cost for a short constrained paragraph).
# Failover is a different tier (Sonnet 5.5) so one model-specific outage or refusal cannot take both.
PRIMARY_MODEL = "anthropic.claude-haiku-5-5"
FAILOVER_MODEL = "anthropic.claude-sonnet-5-5"
JUDGE_MODEL = "anthropic.claude-sonnet-5-5"
# The judge failover must not be the judge model, so it stays on Haiku 5.5.
JUDGE_FAILOVER_MODEL = "anthropic.claude-haiku-5-5"
PRIMARY_MODEL_ENV = "CLAUDE_PRIMARY_MODEL"
FAILOVER_MODEL_ENV = "CLAUDE_FAILOVER_MODEL"
JUDGE_MODEL_ENV = "CLAUDE_JUDGE_MODEL"
JUDGE_FAILOVER_MODEL_ENV = "CLAUDE_JUDGE_FAILOVER_MODEL"
REGION_ENV = "AWS_REGION"

WRITER_MAX_TOKENS = 4096
WRITER_EFFORT = "low"
JUDGE_MAX_TOKENS = 4096
JUDGE_EFFORT = "medium"

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
    """One Claude model on Amazon Bedrock via `anthropic.AnthropicBedrockMantle`.

    The SDK client is built lazily on the first `generate`, so constructing this class needs
    neither AWS credentials nor a region; tests inject `client=` (a fake exposing
    `messages.create`) and never touch the network.
    """

    def __init__(self, model: str = PRIMARY_MODEL, *, effort: str = WRITER_EFFORT,
                 max_tokens: int = WRITER_MAX_TOKENS, schema: Optional[dict[str, Any]] = None,
                 client: Any = None, timeout: float = 120.0) -> None:
        self.model = model
        self.name = model  # lands in RunRecord.model / eval_log production_model
        self.effort = effort
        self.max_tokens = max_tokens
        self.schema = schema or BRIEFING_JSON_SCHEMA
        self.timeout = timeout
        self._client = client

    def _sdk(self) -> Any:
        if self._client is None:
            from anthropic import AnthropicBedrockMantle  # lazy: import + auth only on a live call
            self._client = AnthropicBedrockMantle(aws_region=bedrock_region(), timeout=self.timeout)
        return self._client

    def request_kwargs(self, system: str, user: str) -> dict[str, Any]:
        return {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
            "output_config": {
                "effort": self.effort,
                "format": {"type": "json_schema", "schema": self.schema},
            },
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
        text = next((b.text for b in resp.content if getattr(b, "type", None) == "text"), None)
        if text is None:
            raise BriefingLLMError(f"{self.model} returned no text block")
        try:
            obj = json.loads(text)
        except ValueError as exc:
            raise BriefingLLMError(f"{self.model} returned non-JSON: {text[:160]!r}") from exc
        if not isinstance(obj, dict):
            raise BriefingLLMError(f"{self.model} returned a non-object: {text[:160]!r}")
        return obj


def make_writer_clients() -> tuple[BedrockClaudeClient, BedrockClaudeClient]:
    """Production writer pair: (primary Haiku 5.5, failover Sonnet 5.5), env-overridable."""
    return (
        BedrockClaudeClient(os.environ.get(PRIMARY_MODEL_ENV) or PRIMARY_MODEL),
        BedrockClaudeClient(os.environ.get(FAILOVER_MODEL_ENV) or FAILOVER_MODEL),
    )


def make_judge_client(schema: dict[str, Any], *, failover: bool = False) -> BedrockClaudeClient:
    """A judge on Claude (Sonnet 5.5 by default; Haiku 5.5 when `failover`), effort medium."""
    model = (os.environ.get(JUDGE_FAILOVER_MODEL_ENV) or JUDGE_FAILOVER_MODEL) if failover else \
        (os.environ.get(JUDGE_MODEL_ENV) or JUDGE_MODEL)
    return BedrockClaudeClient(model, effort=JUDGE_EFFORT, max_tokens=JUDGE_MAX_TOKENS, schema=schema)


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
