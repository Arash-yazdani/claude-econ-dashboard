# ADR-005 — whats_new surfaces a notable backdrop move on quiet core days (prompt → v1.4.1)

**Status:** ACCEPTED — Bruce signed off 2026-06-24 (SO8 prompt re-ack). Prompt → v1.4.1; merged + live.
**Date:** 2026-06-24
**Amends:** [ADR-004](ADR-004-briefing-context-indicators.md) (v1.4 — the context handful in `<indicator_metadata>`)

## Context
ADR-004 (v1.4) let the writer *see* a curated context handful — `yield_curve_2s10s`, `brent_oil`
(originally `wti_oil`; swapped by Charter Rev 3 / SCRUM-77, 2026-07-20), `usd_index` — alongside
the 7 core indicators. But the WHATS_NEW rule still only said "emit when a
release dropped, cite the indicator." On a quiet **core** day (no CPI / jobs / GDP print, but a
notable move in oil, the dollar, or the 2s10s), the daily note could speak to none of the backdrop —
the very gap ADR-004 set out to close was still open in the day-to-day line.

## Decision
Tune the WHATS_NEW emission rule (prose only — no new fields, no new indicators):
- **Keep O4:** when `<releases_last_24h>` is non-empty the model MUST emit the one-line note.
- **Add a preference order:** surface the most newsworthy *fresh* move — prefer a **core**
  indicator's release; on a quiet core day, surface the most notable move in a **context**
  indicator (oil, the dollar, or the 10Y-2Y curve) so the market backdrop still shows.
- Values cited must come from `<releases_last_24h>` (unchanged grounding requirement).

The regime + drivers are still classified from **core alone** (unchanged); only which fresh move the
note prefers widens.

## Compliance (SO8 re-ack — the call for Bruce)
Editing the D5-locked production prompt re-opens **SO8** per
[ADR-002](ADR-002-compliance-signoffs.md) (Consequences: *"Any change to a signed-off item … prompt
… re-opens the corresponding sign-off"*). This is an **emission-rule** change, distinct from
ADR-004's **metadata** re-ack, so it carries its own sign-off.
- **No new indicators** — oil / dollar / 2s10s were already acked in ADR-004; they remain framed as
  **macro signals, never tradeable assets**.
- The **O8 banned-phrase validator is unchanged** and still hard-blocks "buy / sell / recommend /
  should …". The note reports context; it never advises. The no-securities-**advice** line holds.
- ADR-004's acked live sample already surfaced the dollar in `whats_new`
  ("Today: US Dollar index firmer, …"); v1.4.1 makes that behavior deliberate rather than incidental.

## Validation
- **199 offline tests green.**
- An interim attempt added an "omit when nothing moved notably" clause that **contradicted O4**;
  the 12-case gate caught it (a case went validator-**invalid** on "whats_new missing with releases
  present") and it was reverted — the shipped rule keeps O4's mandate.
- **12-case ship-gate (B7): CLEAN PASS ✅** on fresh Gemini quota, 2026-06-24 (`eval_log.csv`):
  - all 12 cases validator **valid / refusal**, **0 invalid** — incl. **T4 valid on
    `gemini-2.5-flash`**. (The prior held run failed only on a T4 Groq-failover length artifact under
    exhausted RPD — a quota artifact, not a prompt defect; it did not recur on fresh quota.)
  - judge means **c1 4.73 · c3 4.82 · c4 5.0 · c5 4.75** (all ≥ 4.0), **0/12 below-3**.

## Consequences
- The daily note reflects the commodity / FX / rate backdrop on quiet core days while staying one
  quotable read; quotable numbers still prefer the core drivers.
- `PROMPT_VERSION` 1.4 → **1.4.1**; `eval_log.csv` records the gate run; the live cron picks up the
  new version on its next scheduled run (no config / secret change).

## Sign-off
- [x] **Bruce — SO8 prompt re-ack** (WHATS_NEW emission-rule change) — approved 2026-06-24
- Recorded by Arash, 2026-06-24 (ADR-style capture per the PRD audit rule).
