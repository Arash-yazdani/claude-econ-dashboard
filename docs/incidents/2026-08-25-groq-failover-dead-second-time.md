# 2026-08-25 — Groq failover model dead again (second silent occurrence)

**Impact on the reader: none, by luck.** Production ran Gemini-only the entire window; the
failover was never called. Had Gemini failed on any run in that window, the card would have
published the deterministic fallback template instead of a briefing.

## What happened

Groq decommissioned `llama-3.3-70b-versatile` at some point after 2026-07-20 (the date it was
last verified, when it REPLACED the previously decommissioned `llama-4-scout` — this exact
failure, one model earlier). Nothing errored, nothing paged: the failover path only runs when
Gemini fails, and Gemini did not fail. The death was discovered on 2026-08-25 only because a
Groq deprecation email about an UNRELATED model (Compound Mini) prompted a "does this affect
us?" probe of `/models`, which showed our configured model absent and a live generation call
returning 404 `model_not_found`.

Detection window: dead for up to 36 days (2026-07-20 … 2026-08-25); the exact decommission
date is unknown.

## Fix (same day)

- `GROQ_MODEL` -> `openai/gpt-oss-120b`, the largest general model Groq now serves.
- gpt-oss is a REASONING model, which surfaced the same trap Gemini's thinking tokens did
  (llm_client.py header): with the shared 512-token cap, reasoning consumed the budget, the
  JSON truncated, and Groq's `json_object` validation returned HTTP 400. The Groq call now
  carries `reasoning_effort: "low"` and its own `GROQ_MAX_TOKENS = 2048`.
- Verified live end-to-end through the real `GroqClient` + validator on locked fixtures T1 and
  T2: both `valid`, correctly styled headlines. Client `name` -> `groq-gpt-oss-120b` (the
  audit-log model string).

## The durable fix

A standby that is never exercised is dead until proven otherwise. `weekly-regression.yml` now
probes Groq `/models` every Monday for `GROQ_MODEL`; on absence it sends the Slack alert and
FAILS the job, which fails the `weekly-regression` healthcheck and pages. The next decommission
is discovered within a week, by machinery, instead of within a month, by accident.

## Lessons

1. **A failover you never exercise is not a failover; it is a hope.** Liveness of a standby
   must be probed on a schedule, not assumed from the last manual verification.
2. **"Does this vendor notice affect us?" is worth answering with a probe, not a grep.** The
   grep said no (no Compound references); the probe found the real problem sitting next to it.
3. Reasoning-model token budgets are a repeating class: Gemini (thinkingBudget, 2026-06) and
   now Groq gpt-oss (reasoning_effort). Any future model swap should assume thinking tokens
   count against the output cap until proven otherwise.
