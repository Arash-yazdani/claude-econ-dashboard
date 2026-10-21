# 2026-08-26 — the RED gate froze publishing for two days over one caught check

**Reader impact: real.** macro.modeo.io served the **2026-08-25 AM** card until 2026-08-26
evening. Every run in between computed correctly and then refused to commit. Left alone, the
freeze would have lasted ~28 more days.

## What happened

1. **2026-08-25 PM** the writer produced an output that failed **O5 (number grounding)** and
   the period check. The validator did exactly its job: rejected it, retried, rejected again,
   and published the deterministic **fallback card**. The reader saw a plain-but-correct card.
   `publish-quality` pinged fail and alerted, as designed.
2. That single row put the 30-day **O5 pass rate at 98%** (60/61).
3. `build_metrics` set `red = True` on *any* critical check below 100%. RED ⇒ `exit 3` ⇒ the
   workflow's "Pause publish on RED" step failed the job **before the commit step**.
4. So from 2026-08-25 PM onward every run: fetched fine, wrote fine, **committed nothing**. The
   audit rows kept landing (the `if: failure()` path), which is why `logs/runs.jsonl` looks
   healthy while the site went stale. The last data commit was 2026-08-25; the four commits
   after it are all "audit row for FAILED run".

## The design error

The rule conflated two opposite situations:

- **The validator caught a bad number** → the fallback card published → reader protected. This
  is the safety system *succeeding*.
- **The product is degenerate** (most cards are templates) → serving last-good is genuinely
  better.

Pausing on the first case makes the reader **worse off**: it swaps a safe, current card for a
stale one, which is the "silent staleness is the trust failure" harm the PRD names. And because
the metric is a 30-day rate, a single event latches the pause for a month — the gate's blast
radius was 60x the event that triggered it.

## Fix

`build_metrics` now separates severity:

| Condition | Status | Action |
|---|---|---|
| fallback rate > 50% | 🔴 RED | `exit 3`, pause publishing |
| critical check (O5/O8) < 100% | 🟡 AMBER | `::warning::` + dashboard banner, **keep publishing** |
| otherwise | 🟢 GREEN | publish |

O5/O8 remain non-negotiable where it actually counts: `validator.py` gates **every** publish, so
a bad number cannot reach the card under either rule. The caught event also still pages through
its right channel — `publish-quality` fails on any `source != "llm"` run.

## Lessons

1. **A safety mechanism firing is not an incident; treating it as one can create a real one.**
   Ask what the gate's action does to the reader in the exact scenario that triggers it.
2. **Rate-over-window metrics latch.** A one-off event divided by a 30-day denominator keeps
   tripping a binary gate long after the event is over. If a gate has a blast radius measured in
   weeks, its trigger needs a recency or severity qualifier.
3. **A frozen publish is invisible in the audit log** — `runs.jsonl` kept filling with healthy
   rows while nothing shipped. Liveness must be measured on the PUBLISHED artifact
   (`briefing.json` freshness), not on the run log. *(Open: no check watches published freshness
   directly; the healthchecks dead-man switch pings on a run, not on a commit.)*
