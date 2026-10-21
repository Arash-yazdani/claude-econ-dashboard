# Wireframe & Render States — v1.0

**Status:** ✅ Accepted (2026-06-11) · **Jira:** [SCRUM-19 / D7](https://modeo-team-1.atlassian.net/browse/SCRUM-19) · **Feeds:** B5 (PWA)

One screen, one card on top of Apple-Stocks-style indicator rows, a freshness footer at the bottom. **Read-only** — no chat, no expand/collapse, no copy-to-clipboard, no regenerate, no model badge, no confidence score.

## Layout (default state)

```
┌──────────────────────────────────────────────────────────┐
│  ┌────────────────────────────────────────────────────┐  │
│  │  DAILY BRIEFING                                     │  │  ← briefing-card container
│  │  Soft landing, led by cooling core inflation.      │  │  ← headline · 18pt semibold
│  │                                                    │  │
│  │  • Core CPI cooled to 2.4 percent in May.          │  │  ← quotable_number 1 · 15pt regular
│  │  • Unemployment held at 4.2 percent in May.        │  │  ← quotable_number 2 · 15pt regular
│  │                                                    │  │
│  │  Today: CPI came in cooler, 2.9 → 2.6 percent.     │  │  ← whats_new · 14pt · CONDITIONAL
│  │  The call moved from late-cycle to soft landing…   │  │  ← regime_change · 14pt · CONDITIONAL
│  └────────────────────────────────────────────────────┘  │
│                                                            │
│   CPI               2.9%    ▁▂▃▃▄                          │  ← indicator rows (Apple-Stocks)
│   Core CPI          3.1%    ▂▃▃▄▄                          │
│   Unemployment      4.2%    ▄▃▂▂▁                          │
│   Real GDP          2.1%    ▂▃▃▄▄                          │
│   10-Year Treasury  4.25%   ▃▄▄▃▂                          │
│   Fed Funds         4.50%   ▄▄▄▄▄                          │
│   U-Mich Sentiment  67.5    ▁▂▃▃▄                          │
│ ──────────────────────────────────────────────────────── │
│   Updated 9:02 AM ET · data through May 2026               │  ← freshness footer (P5)
└──────────────────────────────────────────────────────────┘
```

## Element spec

| Element | Type | Size / weight | Notes |
|---|---|---|---|
| briefing-card | container | full-width, rounded, subtle fill | pinned above the rows |
| headline | text | **18pt semibold** | regime call in plain English + dominant-driver clause; ≤22 words |
| quotable_numbers (×2) | bulleted text | 15pt regular | display_name + value + unit + period; spoken phrasing |
| whats_new | label row | 14pt | conditional — only if a release dropped in 24h; prefixed "Today:" |
| regime_change | label row | 14pt | conditional — only if today's regime ≠ yesterday's |
| indicator rows | list (5–7) | Apple-Stocks row | name · value · sparkline; tap = nothing in V1 |
| freshness footer | caption | 12–13pt muted | last-updated time **incl. which run** (10:15 ET AM / 5:30 ET PM) + data-through period — on the twice-daily schedule (ADR-001 v1.1) the time signals an AM-vs-PM update, not a contradiction (solves P5) |

## The 5 render states

| State | When | What renders |
|---|---|---|
| **1. default** | briefing.json valid & fresh | full card (headline + 2 numbers + any conditional fields) above rows |
| **2. loading** | first paint, data fetching | rows render **immediately from cache** (first paint ≤500ms); card shows a brief skeleton shimmer — never a blocking spinner |
| **3. stale** | data older than expected / cron didn't refresh | last good card still shown; **footer turns amber**: "Data may be stale — last updated <time>". Deterministic, non-AI |
| **4. fallback** | validator failed twice | deterministic template card — "Regime: X. CPI: Y%. Unemployment: Z%." — same container, **no** whats_new / regime_change, **no** error text |
| **5. error** | no cache & no fallback | **card collapses silently**; indicator rows still render. Never shows "Something went wrong." |

**Invariant:** the dashboard never surfaces a failure to Bruce. States 3–5 degrade quietly; the rows always render.

## Out of scope for V1 (deferred to V2 backlog)
Driver-attribution badges, talking-points strip, copy-to-clipboard, per-day history, expand/collapse, push notifications.

## Open item
A hi-fi visual mock (SVG/HTML) can be produced from this spec on request — this doc is the contract B5 builds against.
