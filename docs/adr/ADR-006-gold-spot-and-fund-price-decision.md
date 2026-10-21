# ADR-006 — Gold spot price via gold-api.com; IWM/BUZZ deferred

**Status:** ACCEPTED — Bruce signed off 2026-07-21 (SO7/SO9 compliance). Gold spot row built +
live; IWM/BUZZ formally deferred to V3.0.
**Date:** 2026-07-20 (accepted 2026-07-21)
**Relates to:** Charter Rev 3 (V2.1/V2.2), Jira SCRUM-78/80, the IWM/BUZZ proxy-correlation report
(`docs/IWM-BUZZ-Proxy-Report-2026-07-17.docx`, awaiting CEO signature)

## Context
Charter Rev 3 asked for three additions to the markets strip — GLD (gold), IWM (Russell 2000),
BUZZ (VanEck social sentiment) — none of which FRED or any government source carries. A July 17
marketplace sweep (19 candidates, 3 lanes, adversarial live-probed verification) found:

- **No free, no-card, redisplay-permitted vendor exists for IWM or BUZZ.** Every candidate failed
  on a hard constraint: unofficial/bot-blocked endpoints (Stooq, Yahoo, Cboe), a paid license
  required specifically to *display* the data (Tiingo, Twelve Data, EODHD, Polygon — the same
  clause that disqualified Financial Modeling Prep in the June review), or a history/rate cap that
  makes the data unusable (Marketstack, Alpha Vantage, Finnhub). Even the fund sponsors' own free
  download endpoints (iShares, VanEck) carry personal/non-commercial, no-redistribution terms that
  bar exactly this dashboard's public-display use case.
- **gold-api.com clears every hard constraint for a real gold spot price.** Live-probed 2026-07-17:
  `GET https://api.gold-api.com/price/XAU` → 200 OK, no key, no card, real-time (`$4,018.30` at
  probe time). Its terms invite website display ("Use it on your website — we have CORS enabled")
  with no redistribution restriction found. The one honest risk: it is a small, apparently
  single-operator service with no track record — supplementary-feed grade, not FRED-grade.

A follow-up proxy-correlation report (`docs/IWM-BUZZ-Proxy-Report-2026-07-17.docx`) measured, on
real price history, that the dashboard's existing S&P 500 and Nasdaq rows already carry 86–91% of
IWM's and BUZZ's daily movement — the residual signal (small-cap spread; social-selection alpha)
has no lawful free carrier at any quality. That report recommends **not** adding IWM/BUZZ and is
itself awaiting the CEO's signature.

## Decision
1. **Add a gold spot price row** (`gold_spot`, market tier) sourced from **gold-api.com**, built
   supplementary-grade: display-only, never reaches the LLM, degrades to "—" on any fetch failure
   rather than blocking the cron, and is re-verified quarterly alongside the standing source review
   (next: October 2026).
2. **Do not add IWM or BUZZ rows.** No compliant free source exists; the existing S&P 500/Nasdaq
   rows already carry the large majority of their signal per the measured proxy report. VIX
   (Phase 1, live) serves as the market-mood row in their place. This is not a permanent
   foreclosure — Charter Rev 3's V3.0 (Q4 2026 paid-data re-evaluation) is the correct venue to
   revisit IWM/BUZZ if the no-paid-data posture ever changes.

## Compliance (SO7/SO9 — the call for Bruce)
- **SO9 (no payment method, ever):** gold-api.com requires no card and no key for the endpoint
  used. Confirmed via live signup-flow and terms-page inspection, 2026-07-17.
- **SO7 (no-securities-advice posture):** gold is a commodity, not a security — consistent with
  the same reasoning ADR-004 applied to oil/dollar/curve. The row is display-only market tier; the
  O8 banned-phrase validator still hard-blocks any buy/sell/recommend language on every output;
  gold is never fed to the writer and never appears in `briefing_request`.
- **Vendor-durability caveat (new axis, not present in prior ADRs):** unlike FRED and
  Treasury.gov, gold-api.com is not a government or long-established institutional source. The
  mitigation is architectural, not contractual: graceful degradation to "—" (never a crash, never
  a stale number presented as current), plus the quarterly re-check already standing for the rest
  of the data layer.
- **The optional `GOLD_API_KEY` (added 2026-07-21) is SO9-clean too:** confirmed directly with the
  user — obtaining it required no card. It only unlocks the `/history` endpoint (real daily gold
  prices, ~10y); the live price the row shows either way is the same free, keyless, no-card
  endpoint from the original decision above.

## Validation (done, 2026-07-21)
- `GoldSpotSource` behind the `fetch_series` seam (mirrors `TreasuryYieldSource`'s decorator
  pattern): intercepts one synthetic series id, passes everything else through untouched.
- One real observation per run (the free tier's history endpoint returned `401` without a key —
  outside what this ADR approves — so the row is current-value-only, same honest pattern the
  real-rate rows used before their forward-fill fix). Any fetch/parse failure degrades to a
  null-value observation, never a raise.
- **216 offline tests green** (6 new, incl. a config↔client id-sync guard so a future drift can't
  silently route the synthetic id to a real FRED call and crash the cron).
- **Live smoke against the real API:** $4,068.40, correct tier/unit/`source_url`, confirmed absent
  from `briefing_request`.
- **Browser-verified:** row renders in the Markets section at the correct position; a single-point
  history correctly keeps its detail sheet closed (existing codebase convention, unchanged); a
  normal FRED row's "View source" link still resolves to FRED.
- Shipped: commit `cb212b1` on `main`.

## Addendum (2026-07-21) — real ~10-year daily history unlocked
The current-value-only limitation above turned out to be a wrong-endpoint problem, not a real
vendor ceiling. Two dead ends were tried and ruled out before finding the right one:
- `/history` with no grouping returns ONE row per **year** holding that year's *peak* price — not
  a time series, and actively misleading next to the live tick (2026's "high" ran ~$1,500 above
  the live price the same day). Not usable for a chart.
- `/ohlc/{symbol}` collapses the *entire* requested range into one open/high/low/close object —
  also not a per-day series.
- **The actual fix:** `/history?groupBy=day&aggregation=avg` (documented at `gold-api.com/llms.txt`,
  which rendered endpoint specs a plain `curl`/WebFetch couldn't get from the JS-rendered docs
  page) returns a genuine daily average, one row per calendar day. Live-confirmed 2026-07-21:
  **3,196 daily rows, 2016-07-24 → today.** This endpoint (like `/ohlc`) needs `GOLD_API_KEY` and
  shares a 10-request/hour free-tier cap with it — trivial for a twice-daily cron (2 calls/day).

`GoldSpotSource` now grafts the precise live tick over `/history`'s same-day average — identical
pattern to `TreasuryYieldSource`'s Treasury.gov graft on FRED — so the row keeps the exact "current
value" behavior from launch while `deltas.compute_delta`/`compute_delta_3m` get real numbers for
the first time (previously always `None`, rendered as "—"). Falls back gracefully at each layer:
no key → current-value-only (the original shipped behavior, unchanged); history call fails for any
reason (429, network, auth) → live tick still stands alone; live tick *also* fails → history's own
newest day stands in; both fail → a null-value observation, never a raise.

**Validation:** offline suite 220 green (10 new/rewritten gold-client tests covering the graft, the
per-layer fallbacks, and a malformed-row guard). Live smoke via the full pipeline: value $4,081.60,
delta +68.53 (+1.71%), delta_3m -682.75, 220 downsampled history points. Browser-verified: the
detail sheet now opens (previously blocked by the single-point guard) and renders a real 10-year
chart on the "ALL" timeframe, correct stats, "View source" still resolves to gold-api.com.
`GOLD_API_KEY` wired through `scripts/run_daily.py` (optional — absent is a no-op, not a failure)
and added to `.github/workflows/daily-briefing.yml`'s secrets env block; **the GitHub Actions
secret itself still needs to be set by the user** (`gh secret set GOLD_API_KEY` or the repo UI) for
production to pick up the history upgrade — local `.env` alone does not reach the cron.

## Consequences
- Closes Charter Rev 3's Phase 2/V2.2 scope for the markets strip: gold arrives, IWM/BUZZ are
  formally deferred to V3.0 rather than left as an open question.
- `docs/IWM-BUZZ-Proxy-Report-2026-07-17.docx` and this ADR should be signed together — the report
  is the evidence base for the "do not add" half of this decision.

## Sign-off
- [x] **Bruce — SO7/SO9 compliance** (gold-api.com source; IWM/BUZZ deferral) — approved 2026-07-21
- Recorded by Arash, 2026-07-20 (drafted ahead of sign-off so Phase 2 could start the moment it
  landed); accepted 2026-07-21, same day as Bruce's approval.
