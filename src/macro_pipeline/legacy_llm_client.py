"""LEGACY — the original Economy-Dashboard B3 clients (Gemini primary + Groq failover), frozen.

Not on any runtime path. Imported only by scripts/compare_models.py to replay the original
stack as the baseline against Claude on Bedrock (docs/adr/ADR-007). Do not wire it back in.

Original docstring follows.


`LLMClient` is the seam the orchestrator depends on, so tests inject a fake and never hit
the network. `generate_briefing()` assembles the prompt, calls the primary, and on any
failure falls over to the secondary (PRD q27 config). It returns the model's raw parsed
JSON — the B4 validator (separate task) is the real gate on shape/content.

Production config (PRD q27): gemini-2.5-flash, temp 0.2, top_p 0.9, max_output_tokens 256,
response_mime_type application/json + responseSchema=BriefingOutput; failover llama-4-scout.
The exact Gemini responseSchema dialect and the Groq model id need verifying against the
live APIs (no LLM keys in the test env); both are isolated to the client classes below.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Optional, Protocol

import requests

from . import prompt

TEMPERATURE = 0.2
TOP_P = 0.9
# PRD q27 said 256, but that predates gemini-2.5-flash being a *thinking* model: its
# thinking tokens count against this cap, so 256 left ~0 tokens for the JSON (live run hit
# finishReason=MAX_TOKENS). We disable thinking (thinkingBudget=0) and keep headroom here.
MAX_OUTPUT_TOKENS = 512

GEMINI_MODEL = "gemini-2.5-flash"
GROQ_MODEL = "openai/gpt-oss-120b"  # verified against Groq /models + a live validator-VALID
# generation on fixture T1, 2026-08-25. This is the SECOND silent failover death: Groq
# decommissioned llama-4-scout (2026-07-20 incident), then llama-3.3-70b-versatile (found 404
# on 2026-08-25, discovered only via an unrelated deprecation email). The weekly regression now
# probes /models for this id and alerts on absence — see weekly-regression.yml.
# gpt-oss is a REASONING model: its thinking tokens count against max_tokens (the same trap as
# Gemini's thinkingBudget above), so the Groq call caps reasoning at "low" and carries its own
# larger budget — with the shared 512 cap the JSON truncates and Groq's json_object validation 400s.
GROQ_MAX_TOKENS = 2048
GROQ_REASONING_EFFORT = "low"

# BriefingOutput in Gemini's schema dialect (OpenAPI subset). maxItems/maxLength are not
# enforced here (Gemini ignores them) — the B4 validator catches those.
GEMINI_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "headline": {"type": "STRING", "nullable": True},
        "quotable_numbers": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "indicator_id": {"type": "STRING"},
                    "sentence": {"type": "STRING"},
                },
                "required": ["indicator_id", "sentence"],
            },
        },
        "whats_new": {"type": "STRING", "nullable": True},
        "regime_change": {"type": "STRING", "nullable": True},
        "error": {"type": "STRING", "nullable": True},
    },
    "required": ["headline", "quotable_numbers", "whats_new", "regime_change"],
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


def _between_braces(text: str) -> Optional[str]:
    """The first '{' … last '}' span — recovers JSON from a fence/preamble wrapper."""
    if not isinstance(text, str):
        return None
    i, j = text.find("{"), text.rfind("}")
    return text[i:j + 1] if 0 <= i < j else None


def _parse_json(text: str, provider: str) -> dict[str, Any]:
    # JSON mode returns a bare object, but be robust to an occasional ```json fence/preamble.
    for candidate in (text, _between_braces(text)):
        if candidate is None:
            continue
        try:
            obj = json.loads(candidate)
        except (TypeError, ValueError):
            continue
        if isinstance(obj, dict):
            return obj
    raise BriefingLLMError(f"{provider} returned non-JSON: {str(text)[:160]!r}")


class GeminiClient:
    name = "gemini-2.5-flash"

    def __init__(self, api_key: Optional[str] = None, model: str = GEMINI_MODEL,
                 session: Optional[requests.Session] = None, timeout: float = 30.0,
                 temperature: float = TEMPERATURE, response_schema: Optional[dict[str, Any]] = None,
                 max_output_tokens: int = MAX_OUTPUT_TOKENS) -> None:
        # temperature + response_schema are overridable so the B7 judge can reuse this client
        # (judge runs temp 0.0 with the JudgeVerdict schema); defaults stay the production values.
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY", "")
        if not self.api_key:
            raise BriefingLLMError("No GEMINI_API_KEY (see .env.example).")
        self.model = model
        self.session = session or requests.Session()
        self.timeout = timeout
        self.temperature = temperature
        self.response_schema = response_schema or GEMINI_RESPONSE_SCHEMA
        self.max_output_tokens = max_output_tokens

    def generate(self, system: str, user: str) -> dict[str, Any]:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {
                "temperature": self.temperature, "topP": TOP_P, "maxOutputTokens": self.max_output_tokens,
                "responseMimeType": "application/json", "responseSchema": self.response_schema,
                "thinkingConfig": {"thinkingBudget": 0},  # deterministic JSON; no thinking budget
            },
        }
        try:
            # key in a header, not the URL query — keeps it out of logs/error strings
            resp = self.session.post(url, headers={"x-goog-api-key": self.api_key},
                                     json=body, timeout=self.timeout)
        except requests.RequestException as exc:
            raise BriefingLLMError(f"Gemini request failed: {exc}") from exc
        if resp.status_code != 200:
            raise BriefingLLMError(f"Gemini HTTP {resp.status_code}: {resp.text[:160]}")
        try:
            text = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError, ValueError) as exc:
            raise BriefingLLMError("Gemini response shape unexpected") from exc
        return _parse_json(text, "Gemini")


class GroqClient:
    name = "groq-gpt-oss-120b"

    def __init__(self, api_key: Optional[str] = None, model: str = GROQ_MODEL,
                 session: Optional[requests.Session] = None, timeout: float = 30.0) -> None:
        self.api_key = api_key or os.environ.get("GROQ_API_KEY", "")
        if not self.api_key:
            raise BriefingLLMError("No GROQ_API_KEY (see .env.example).")
        self.model = model
        self.session = session or requests.Session()
        self.timeout = timeout

    def generate(self, system: str, user: str) -> dict[str, Any]:
        try:
            resp = self.session.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": self.model,
                    "messages": [{"role": "system", "content": system},
                                 {"role": "user", "content": user}],
                    "temperature": TEMPERATURE, "top_p": TOP_P, "max_tokens": GROQ_MAX_TOKENS,
                    "reasoning_effort": GROQ_REASONING_EFFORT,
                    "response_format": {"type": "json_object"},
                },
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise BriefingLLMError(f"Groq request failed: {exc}") from exc
        if resp.status_code != 200:
            raise BriefingLLMError(f"Groq HTTP {resp.status_code}: {resp.text[:160]}")
        try:
            text = resp.json()["choices"][0]["message"]["content"]
        except (KeyError, IndexError, ValueError) as exc:
            raise BriefingLLMError("Groq response shape unexpected") from exc
        return _parse_json(text, "Groq")


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
