# ADR-004 — Let the writer see a curated context handful (oil · dollar · yield curve)

**Status:** ACCEPTED — Bruce signed off 2026-06-22 (SO7″ compliance + prompt re-ack). Prompt → v1.4; merged + live.
**Date:** 2026-06-22
**Supersedes/amends:** [ADR-003](ADR-003-markets-display-only.md) (SO7′ — "market tier excluded from the LLM")

## Context
The daily summary is built from the **7 core** indicators only; the ~26 display-tier indicators
(international, commodities, rates, sectors) render on the dashboard but are **invisible to the
writer** (ADR-003/SO7′ deliberately excludes market-tier data from the LLM, to hold the
no-securities-**advice** line). The result: a read that speaks only to jobs + inflation and never
reflects the broader market backdrop, even when it's the story.

## Decision
Feed the writer a **curated context handful** in addition to the 7 core — `config.BRIEFING_CONTEXT_IDS`:
- **`yield_curve_2s10s`** — 10Y−2Y Treasury spread (a recession signal)
- **`brent_oil`** — Brent crude (energy cost / inflation pressure). *Originally `wti_oil`; swapped
  for Brent by Charter Rev 3 / SCRUM-77 (CEO-approved, prompt v1.4.2, 2026-07-20) — a metadata-only
  context-id swap, prose unchanged.*
- **`usd_index`** — trade-weighted dollar (import prices / financial conditions)

These are added to `briefing_request()` and the prompt's `<indicator_metadata>` (framed as **macro
signals, never tradeable assets**). They are **NOT** promoted to core display rows. The regime +
drivers are still classified from **core alone**, so only the *narration's evidence base* widens —
the regime read never changes.

## Compliance (SO7″ — the call for Bruce)
This deviates from ADR-003/SO7′'s *implementation* (blanket market-tier exclusion), but arguably
stays within its *principle* ("no securities **advice**"):
- Oil is a commodity, the dollar is FX, the 2s10s is a rate spread — **none are securities**.
- The **O8 banned-phrase validator is unchanged** and still hard-blocks "buy/sell/recommend/should…"
  on every output. The writer reports context; it never advises.
- They're framed in the prompt as economic signals (recession warning / inflation pressure /
  financial conditions), not as assets to trade.

**Sign-off needed:** is feeding macro/commodity/FX/rate data to the writer **as context** acceptable
under SO7, or do we keep the conservative blanket exclusion? (Equity securities — S&P, Nasdaq,
market cap — remain excluded regardless.)

## Validation (done, on branch `feat/briefing-context-indicators`)
- **199 offline tests green** — incl. the metadata↔request mirror (the LLM may only cite an
  indicator present in both).
- **12-case ship-gate (B7): PASS ✅** with the new prompt — validator C2/C6 100%, judge means
  c1 4.91 / c3 4.91 / c4 4.64 / c5 4.67 (all ≥4.0), 1/12 below-3 (T4, within tolerance). Proves the
  expanded prompt does **not regress** the non-negotiables. *(The 12 fixtures are core-only, so the
  gate proves no-regression; the live sample below proves the new context behavior.)*
- **Live run** (new prompt, real FRED+Gemini): stayed grounded + no-advice + `state:default`
  (validator-valid), and used the dollar in `whats_new`:
  > "Today: US Dollar index firmer, from 119.51 to 120.40 index."

## Consequences
- The summary can reflect the commodity/FX/rate backdrop while staying one quotable read; quotable
  numbers still prefer the core drivers.
- Prompt version should bump (D5 prompt changed) once acked; `eval_log.csv` records the gate run.
- **Deploy is gated** on Bruce's SO7″ + prompt sign-off. Until then this lives on the branch only.

## Sign-off
- [x] **Bruce — SO7″ compliance** (feed macro/commodity/FX/rate context to the writer) — approved 2026-06-22
- [x] **Bruce — prompt re-ack** (D5-locked prompt metadata change) — approved 2026-06-22
