# Incident 2026-08-07 — first-ever published fallback, caused by our own gate runs

- **Date / time (ET):** 2026-08-07 17:30 (the PM publish)
- **Trigger:** user report — the live card showed the deterministic template
  (*"Regime: Soft landing. CPI: 3.5%. Unemployment: 4.1%."*) instead of a briefing
- **Signal:** `logs/runs.jsonl` 2026-08-07T17:30:35-04:00 — `source: fallback`,
  `validator_status: skipped`, `error: none`. The `Publish Quality` healthcheck `/fail`ed as
  designed (`report_run_health`: `source != llm`).
- **Impact:** Bruce-visible. The PM card carried the degraded template overnight — the first
  fallback in **111 runs**, breaking the CTQ-12 "zero fallback" streak. Numbers on the card
  remained correct and current; only the narrated briefing was absent.

## Root cause

**Our own strict-gate attempts starved production.** Six certification attempts for prompt v1.5
ran on 2026-08-06/07, the last two at 21:23 and 21:28 UTC — the second still in flight when the
PM briefing dispatched at 21:30. Gemini's daily quota was already exhausted (every gate case had
fallen to Groq), and the gate's two-calls-per-case load drained Groq's window too. Production's
primary call got a 429, its failover got a 429, and `produce_briefing` correctly rendered the
deterministic fallback.

Not a v1.5 defect: `validator_status: skipped` means no LLM output ever existed to validate. The
same morning's AM run — v1.5's first production output — was `source: llm`, `valid`, an original
(non-echoed) causal headline, and correctly carried the day's jobs release in `whats_new`.

The deeper cause is structural: **the gate and the product share one free-tier quota, and nothing
gave production priority.** The scheduled Monday slot was deliberately placed clear of the
briefing windows, but manual dispatch carried no such protection, and six attempts in two days
treated the shared quota as if it were the gate's.

## What worked

- The degradation path: fallback template rendered (with rounded values — the raw-float fix from
  W3 was already live), never an error to the reader.
- The alert: `Publish Quality` paged, correctly and immediately.
- The audit: the run left a complete row.
- The W1 basis line rendered on the live card — the "WHY:" block did its job on day one.

## Fix

| # | Change | Where |
|---|---|---|
| 1 | **Window guard**: manual gate runs refuse to start inside 13:30–15:30 / 21:15–23:15 UTC unless `override_window_guard` is set | `weekly-regression.yml` |
| 2 | Basis display rounding (the card showed `Real GDP 2.1018`) | `public/app.js` |
| 3 | No recovery run needed: the 13:45 UTC AM cron replaces the fallback automatically | — |

## Lessons

1. **Production eats first.** A gate exists to protect the product; a gate that starves the
   product has it backwards. Shared-quota consumers need an explicit priority rule, not an
   assumption that there is enough for everyone.
2. **Retry pressure is load.** Each "one more attempt" at the gate was rational in isolation and
   collectively drained two providers. The cost of attempt N was invisible at attempt N-1.
3. The degradation + alerting design earned its keep: the failure was loud, legible, audited,
   and self-healing by the next scheduled run.

## Follow-up

- One strict gate run tomorrow 07:10–13:30 UTC (fresh quota, outside the guard windows) — the
  v1.5 certification that all of this was in service of.
- Watch item: v1.5's AM output described core CPI 2.6 as "near the Fed's target"; the prompt's
  guard reserves "near" for essentially-at-target. If it recurs post-certification, it is a
  wording fix at the next SO8 event, not an emergency.
