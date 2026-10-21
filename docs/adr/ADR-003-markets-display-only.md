# ADR-003 — Market data is display-only ("no securities **advice**")

**Status:** ✅ **Accepted (2026-06-18)** — reframes/renews **SO7**. · **Owners:** Bruce + Arash · **Jira:** [SCRUM-40](https://modeo-team-1.atlassian.net/browse/SCRUM-40) (SO7) · **Supersedes the scope clause of:** [ADR-002](ADR-002-compliance-signoffs.md) SO7 · **Unblocks:** V2 Tier 3 (markets strip, `docs/v2-indicator-expansion.md`).

---

## Context

The original SO7 sign-off locked the product scope as **"no securities"** — the core Investment
Advisers Act (IAA) defense. Bruce has since asked to **see** market data (S&P 500, Nasdaq, total US
market cap, high-yield credit spread) as macro context. Displaying market data and *advising on*
securities are different acts; the original scope conflated them.

## Decision

Narrow the scope from **"no securities"** to **"no securities _advice_."**

The dashboard may **display** market indicators as macro context. It must never recommend, rate,
time, or opine on any security. The load-bearing guardrail makes this structural, not just a
prompt instruction:

- **Market indicators are display-only and live OUTSIDE the AI briefing.** They render in the PWA
  from `indicators.json` (tier `market`), but are **excluded from the `briefing_request`** handed to
  the LLM. The daily briefing therefore still writes only about the macro core — **it never produces
  a sentence about a stock, index, or spread.** Data is shown; the AI does not editorialize it.
- The macro classifier/regime is **unchanged** — market data does not feed the regime call (that
  would re-open D3/SO8 + require a back-test; explicitly out of scope here).
- A **"market data for context — not advice"** footnote sits on the markets strip.
- The O8 banned-phrase scan and the refusal envelope are **retained unchanged.**

## Consequences

- **SO7 is re-signed as SO7′** with this narrowed scope (Bruce + Arash, 2026-06-18). SO7 remains a
  launch-blocking compliance sign-off; its *meaning* is updated, not revoked.
- **V2 Tier 3 (markets strip) is unblocked** — implementable as display-only rows.
- The briefing, its `input_hash`, and the locked 12-case regression are **unaffected** (the
  `briefing_request` stays macro-core-only; markets never enter it).
- If the product later wants the AI to **narrate** markets (not just show them), that is a
  materially larger compliance change and requires a fresh ADR + sign-off — it is **not** authorized
  by this one.
