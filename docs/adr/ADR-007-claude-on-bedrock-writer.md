# ADR-007 — Move the writer to Claude on Bedrock

**Status:** PROPOSED — Phase 1 built locally 2026-10-20. Not deployed; no AWS resources exist yet.
**Date:** 2026-10-20
**Relates to:** PRD q27 (LLM config), ADR-001 (host + cron), SO9 ($0 budget, no card on file),
the original repo `Arash-yazdani/Economy-Dashboard` (macro.modeo.io), which stays live as the baseline.

## Decision

This repo (Claude Econ Dashboard) runs the same pipeline as Economy-Dashboard with the LLM layer on
Claude via Amazon Bedrock, called through the Anthropic Python SDK's `AnthropicBedrockMantle` client.
Gemini 2.5 Flash and Groq gpt-oss-120b are removed from every runtime path. Their clients are frozen
in `src/macro_pipeline/legacy_llm_client.py`, imported only by `scripts/compare_models.py`.

This copy breaks SO9: Bedrock bills per token on an AWS account with a payment method. The
original repo is unaffected and stays at $0.

| Role | Model | Effort | `max_tokens` | Why |
|------|-------|--------|--------------|-----|
| Briefing writer | `anthropic.claude-haiku-5-5` | low | 4096 | Changed from Opus 5.5 by Arash on 2026-10-21 for cost. The task is one short paragraph inside a fixed schema, checked by the deterministic validator. |
| Writer failover | `anthropic.claude-sonnet-5-5` | low | 4096 | Different model tier, so one model-specific outage or refusal does not take both. |
| Craft judge, persona judge | `anthropic.claude-sonnet-5-5` | medium | 4096 | Not the writer's model, so the judge does not score its own output. Same family; see Consequences. |
| Judge failovers | `anthropic.claude-haiku-5-5` (`CLAUDE_JUDGE_FAILOVER_MODEL`) | medium | 4096 | Replaces the Groq judge failover. Each judge's failover keeps that judge's schema. |

All ids are constants in `llm_client.py`, overridable with `CLAUDE_PRIMARY_MODEL`,
`CLAUDE_FAILOVER_MODEL` and `CLAUDE_JUDGE_MODEL`.

## Request shape

- `output_config.format` = `json_schema` with `BRIEFING_JSON_SCHEMA` (writer), `JUDGE_JSON_SCHEMA`
  or `BRUCE_JSON_SCHEMA` (judges). Standard JSON Schema: `additionalProperties: false` on every
  object, `required` lists, nullable fields as `{"type": ["string", "null"]}`. The briefing schema
  is asserted equal to a mechanical conversion of the original `GEMINI_RESPONSE_SCHEMA`; the judge
  schemas are asserted to carry the same properties and required set as their locked copies in
  `schemas/`. The first text block is parsed with `json.loads`.
- `output_config.effort` = `low` (writer) or `medium` (judges).
- No `temperature` or `top_p`: non-default sampling values return 400 on Opus 5.5, Sonnet 5.5 and
  Haiku 5.5. The original judge's "temp 0.0 determinism" is gone; consistency now rests on the
  schema and the rubric.
- No `thinking` parameter: thinking cannot be disabled on Opus 5.5 (`{"type": "disabled"}` and
  `budget_tokens` both 400). Thinking tokens count against `max_tokens`, so the cap is 4096, not
  the original 512 that Gemini thinking exhausted.
- `stop_reason` `refusal` or `max_tokens` raises `BriefingLLMError`, so `generate_briefing` runs
  the failover. SDK errors are caught most-specific-first (`RateLimitError`, `APIStatusError`,
  `APIConnectionError`) and wrapped in `BriefingLLMError`.
- `AWS_REGION` is required. No default region; unset is a clear `BriefingLLMError`, and
  `run_daily.py` publishes the deterministic fallback card.

Not used: refusal fallbacks (the server-side `fallbacks` parameter is not offered on Bedrock, and
the SDK's client-side `BetaRefusalFallbackMiddleware` would duplicate the app-level Haiku failover) and prompt caching (the ~2,400-token static system prefix repeats only twice a
day, so cache entries would expire between runs).

## What stays deterministic

- **The model does not pick the regime.** `classifier.classify` (v1.2, D9-signed) labels the regime
  with rules before any LLM call. The writer narrates the label it is given.
- **The validator is the gate.** `validator.produce_briefing` runs the same O-checks (O1-O5,
  period grounding, O8 banned phrases, length caps, O16 refusal envelope), retries once, and falls
  back to `fallback_template`. A structurally valid Claude response that fails O5 still publishes
  the fallback card.
- Ingest, plausibility gate, staleness, release detection, the prompt (v1.5, unchanged), the card
  builder and the audit log are unchanged.

## Observability

Each Claude call records `{model, input_tokens, output_tokens, latency_ms, stop_reason}` from
`response.usage` via `observability.record_llm_usage`. `record_from` drains them into a new optional
`llm_usage` array on the RunRecord (`logs/runs.jsonl`, `schemas/run-record.schema.json`); the
existing `model` field carries the Bedrock id. `run_daily_eval.py` adds `llm_usage` to the LangSmith
root-run metadata.

## Cost estimate

Token counts are a local approximation (4 characters per token, no API call) on a
production-shaped request built offline from the test fake source (10 request indicators, prompt
v1.5). Re-measure with `messages.count_tokens` or the logged `llm_usage` before relying on them.

| Call | Input tokens | Output tokens | Source of output figure |
|------|--------------|---------------|-------------------------|
| Writer (per run) | 3,392 (system 2,366 + user 1,026) | 600 | assumption: ~130-token JSON plus low-effort thinking |
| Craft judge (per run) | 1,098 | 800 | assumption |
| Persona judge (per run) | 3,996 | 800 | assumption |
| Weekly gate, per case | 3,215 writer + 1,523 judge | 600 + 800 | fixture average from `compare_models.py --dry-run` |

First-party Claude API prices (Anthropic, per MTok input/output): Opus 5.5 $4 / $20, Sonnet 5.5
$2 / $10, Haiku 5.5 $0.10 / $0.50. **Bedrock pricing is set by AWS and may differ; check
aws.amazon.com/bedrock/pricing for the chosen region before enabling the schedule.**

At 2 runs/day (61 runs/month):

| Line | Per unit | Monthly |
|------|----------|---------|
| Writer, Opus 5.5: 3,392 x $4/M + 600 x $20/M | $0.0256/run | **$1.56** |
| Writer at the 4,096 output cap (worst case) | $0.0955/run | $5.82 |
| Daily eval judges, Sonnet 5.5: craft $0.0102 + persona $0.0160 | $0.0262/run | $1.60 |
| Weekly gate, 12 cases (Opus writer + Sonnet judge), 4.35 runs/month | $0.43/run | $1.87 |
| Haiku 5.5 failover, per call | $0.0006 | ~$0 |
| **Total, first-party prices, assumed outputs** | | **~$5.03** |

## Consequences

- The judge and the candidate writer share a model family. `compare_models.py` scores both stacks
  with the same Sonnet judge; validator results carry no such bias and are the primary comparison.
- The κ calibration in `eval_log.csv` was against Gemini judge scores. Sonnet judge scores are not
  comparable until re-calibrated against Bruce's annotations (Runbook 7b, never run).
- Bedrock has no Models API on the Mantle endpoint. `probe_models.py` sends one
  `messages.count_tokens` per model; a 404 means not served or not enabled in the region.
- The free-tier quota table in CLAUDE.md (Gemini 1,500 RPD, Groq 1,000 RPD) no longer applies.
  Bedrock quotas are per account and region (AWS Service Quotas).

## Access

The Mantle client signs requests with SigV4 for service name `bedrock-mantle`
(`anthropic/lib/bedrock/_mantle.py`, `DEFAULT_SERVICE_NAME`), against
`https://bedrock-mantle.{region}.api.aws/anthropic`. That may need IAM actions under a
`bedrock-mantle:` prefix rather than, or in addition to, `bedrock:InvokeModel`. Not verified: confirm
the exact action names in the AWS Bedrock IAM documentation before writing the policy.


## Amendment 2026-10-21: writer moved to Haiku 5.5

Arash chose Claude Haiku 5.5 as the writer. Recomputed with the same token approximations (first-party list prices; Bedrock prices must be checked on aws.amazon.com/bedrock/pricing):

| Line | Math | Monthly (61 runs) |
|---|---|---|
| Writer, Haiku 5.5 | 3,392 in x $0.10/M + 600 out x $0.50/M = $0.00064/run | $0.04 |
| Daily eval judges, Sonnet 5.5 | unchanged, $0.0262/run | $1.60 |
| Weekly gate, 12 cases, Haiku writer + Sonnet judge | ($0.00062 + $0.0110) x 12 = $0.14/gate x 4.35 | $0.61 |
| Total | | about $2.25 |

The judges are now about 97% of spend. Turning off the daily eval and the weekly gate cuts the total to about $0.04 a month, at the cost of the quality signal.
