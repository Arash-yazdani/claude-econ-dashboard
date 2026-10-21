# V2 Proposal — Indicator Expansion (7 → 19) + Markets strip

**Status:** ✅ **Decisions made 2026-06-18 — Phase 2a SHIPPED; 2b unblocked.** SO7 reframed to
"no securities advice" ([ADR-003](adr/ADR-003-markets-display-only.md)). · **Author:** Arash.

Give Bruce his full must-have list **without breaking his own #1 acceptance bar — "scannable in
<10s"** — and **without weakening the no-advice compliance posture** that is the product's
Investment-Advisers-Act defense.

## The tension

7 → 19 indicators is 2.7× the rows. A flat 19-row wall breaks the glance Bruce signed up for. The
fix is to **tier the screen**: the glance stays 7, the depth is one tap away, and markets are a
clearly separate, data-only strip.

## Proposed layout — 4 tiers

### Tier 0 · Regime snapshot (NEW, top)
The "CNBC-economist read" Bruce asked for, as a first-class element: a labeled gauge —
**Soft landing · Late-cycle · Stagflation risk · Slowdown · Recovery** — with its driver. Already
computed by the classifier today (it's behind the headline); V2 just promotes it to a visible dial.

### Tier 1 · Core glance (the current 7 — always visible, unchanged)
GDP · CPI · Core CPI · Unemployment · 10-yr · Fed funds · U-Mich sentiment. **The daily briefing
reads only from these.** This is the <10s layer.

### Tier 2 · Macro drawer (collapsed by default — Bruce's `CAP` items)
Pure FRED adds, all free. One tap on "More indicators" expands:

| Indicator | FRED series | transform | freq | note |
|---|---|---|---|---|
| Net jobs added (Δ payrolls) | `PAYEMS` | chg | monthly | the headline jobs number |
| 2-year Treasury | `DGS2` | lin | daily | front of the curve |
| Initial jobless claims | `ICSA` | lin | weekly | high-frequency labor signal |
| Labor participation | `CIVPART` | lin | monthly | |
| Retail sales | `RSAFS` | pc1 / pch | monthly | demand (YoY or MoM — see Q3) |
| 30-yr mortgage | `MORTGAGE30US` | lin | weekly | housing affordability |
| Housing starts | `HOUST` | lin | monthly | |
| Federal debt (% GDP) | `GFDEGDQ188S` | lin | quarterly | debt-to-GDP reads better than raw $ (Q2) |

### Tier 3 · Markets strip (NEW — Bruce-requested; the `LANE` items, **display-only**)

| Indicator | FRED series | freq | note |
|---|---|---|---|
| S&P 500 | `SP500` | daily | index level |
| Nasdaq Composite | `NASDAQCOM` | daily | index level |
| US market cap | `WILL5000IND` | daily | Wilshire 5000 ≈ total US market (Buffett-indicator proxy) |
| High-yield credit spread | `BAMLH0A0HYM2` | daily | the macro-stress gauge |

**ISM manufacturing PMI — blocked.** ISM pulled its license from FRED, so there is no free series.
Options: substitute **industrial production (`INDPRO`)** or a regional-Fed survey (Empire State /
Philadelphia) as a free manufacturing proxy, or pay for a feed (breaks the $0 rule). Recommend:
park it, or ship `INDPRO` as the proxy (Q4).

## Compliance — the `LANE` change (a conscious decision)

Adding securities/market data **re-opens SO7** (per ADR-002: any change to a signed-off item
re-opens its sign-off). To honor Bruce's request *and* keep the IAA defense intact, narrow the scope
rather than drop it:

- **From** "no securities" **to** "no securities **advice**." The dashboard *displays* market data as
  macro context; it never recommends, rates, times, or opines on any security.
- **Guardrail — markets are display-only and live OUTSIDE the AI briefing.** The market rows render
  from `indicators.json`, but are **excluded from the `briefing_request`** handed to the LLM. So the
  daily briefing still writes only about the macro 7 + regime — **it never produces a sentence about
  stocks.** The numbers are shown; the AI doesn't editorialize them. That's the clean line: data is
  data; the AI stays in the macro lane.
- A small **"market data for context — not advice"** footnote on the strip.
- Keep O8 + the refusal envelope; optionally extend O8 to market-advice phrasings.
- Capture as **ADR-003** and a re-signed **SO7′** (Bruce + Arash) before shipping Tier 3.

> If you later want the briefing to *narrate* markets (not just show them), that's a materially bigger
> compliance step — a separate decision, not part of this proposal.

## Plumbing impact (all small)

- The pipeline and PWA are already **generic over the `INDICATORS` list** — most adds are a `config.py`
  row (FRED id + transform + a plausibility range + a stale-after cadence) plus a test. The 5MB cache
  cap is untouched (19 × ~70 obs is tiny).
- **Display formatting:** two new value types — large counts (payrolls, claims, housing starts → "K")
  and index levels (S&P, Nasdaq, Wilshire) — need a small `fmtValue` extension in `app.js`.
- **FRED budget:** ~19 calls/run × 2 runs ≈ **38/day**. Past the PRD's ≤25/day *soft* target, but ~4%
  of the 1,000/day hard-stop and trivial for FRED (120 req/min). Acceptable — bump the soft target.
- **Regime classifier UNCHANGED** in the MVP: new indicators are display-only. (A later pass *could*
  let payrolls / claims / credit-spreads inform the regime — but that re-opens the D3/SO8 thresholds
  and needs a back-test. Out of scope for 2a.)

## Phasing

| Phase | Scope | Compliance | Risk |
|---|---|---|---|
| **2a** | Tier 0 regime gauge + Tier 2 macro drawer | none (pure adds) | low — ship first |
| **2b** | Tier 3 markets strip | **SO7′ re-sign + ADR-003** + display-only guardrail | medium — ship after re-sign |
| **2c** | ISM manufacturing | needs a free source or a paid decision | parked |

## Decisions (2026-06-18)

1. **SO7 reframed** → "no securities advice" ([ADR-003](adr/ADR-003-markets-display-only.md)); re-signed as SO7′. **Tier 3 unblocked.**
2. **Federal debt → debt-to-GDP** (`GFDEGDQ188S`). ✅ shipped in Tier 2.
3. **Retail sales → month-over-month** (`RSAFS` `pch`). ✅ shipped in Tier 2.
4. **ISM → `INDPRO`** (industrial production, YoY) accepted as the free manufacturing proxy. ✅ shipped in Tier 2.

## What shipped / what's next

- **Phase 2a — SHIPPED 2026-06-18.** Tier 0 regime gauge + Tier 2 macro drawer (9 indicators incl.
  `INDPRO`). The briefing stays macro-core-only (`briefing_request` filtered to `tier=="core"`); the
  classifier/regime is unchanged; plausibility **and** staleness hard-gate/roll-up on the core tier
  only. 181 offline tests green; live-verified in the PWA (regime gauge + collapsed "More indicators"
  drawer + K/count/signed formatting, zero console errors). The service-worker shell is bumped to v4
  so installed clients re-cache.
- **Phase 2b — SHIPPED 2026-06-18.** 4 display-only `market`-tier rows + a "market data for context —
  not advice" footnote (ADR-003 / SO7′), excluded from `briefing_request` by the existing core filter.
  Series: `SP500`, `NASDAQCOM`, `BAMLH0A0HYM2`, and — because Wilshire's `WILL5000IND` total-market
  index was **discontinued on FRED** — `NCBEILQ027S` (market value of US corporate equities, the
  "Buffett indicator" numerator, quarterly $millions) rendered as `$T` for the "US market cap" row
  (user decision 2026-06-18). SW shell → v5.
- **Phase 2c — SHIPPED 2026-06-18.** Apple-Stocks change chip: tap any chip to cycle a global display
  mode — **percent** (Δ/prior) · **points** (native Δ) · **raw level** — persisted to `localStorage`.
  Backend publishes `level`: a rate/change series (`pc1`/`pch`/`chg`) gets a 2nd `units=lin` fetch for
  the raw level (CPI index ~334 behind 4.2% YoY; total payrolls ~159M); a `lin` series' level is its
  value. `level` stays out of `to_request_indicator`, so the LLM contract / regression is byte-unchanged.
  SW shell → v6.
- **Phase 2d — SHIPPED 2026-06-18.** Tap a row → an Apple-Stocks-style detail sheet: a timeframe
  selector (`6M·1Y·2Y·5Y·10Y·ALL`, plus `1M·3M` for daily; **no 1D** — FRED is daily-close), a
  dependency-free hand-rolled SVG chart (date x-axis, value y-axis, gridlines, area fill), a stats grid
  (current, latest change, range-shown, 52-week range, underlying level, frequency, last release), and
  two **$0** dive-deeper links (View on FRED + Search news). Backend pre-bakes a downsampled `history`
  (~220 pts, freshest ~90 kept full-res for smooth short ranges) + `source_url` + `frequency`; no extra
  FRED *calls* (wider responses only). Feed ≈280KB (~60KB over the wire with brotli). 183 offline tests
  green; live-verified in the PWA (chip cycle + persistence, sheet open/close, timeframe re-slice,
  FRED/news links), zero console errors. SW shell → v7.
- **Phase 2d.1 — sheet motion polish (2026-06-18, user request).** The detail sheet now behaves like
  the Apple Stocks app: CSS slide-up on open, animated slide-down on close, and finger-following
  drag-to-dismiss (grab handle; distance OR flick-velocity threshold; backdrop dims proportionally;
  sub-threshold springs back). Designed via a 3-candidate design panel + synthesis, then adversarially
  reviewed for iOS Safari and hardened: the entrance animation re-triggers on EVERY open (WebKit won't
  replay a descendant animation on an ancestor display toggle → none→reflow→"" restart, no rAF), the
  post-drag synthetic click is swallowed so a dismiss can't reopen the row behind it, and the body is
  position:fixed scroll-locked (iOS ignores body{overflow:hidden}). SW shell → v9.

- **V3 expansion — SHIPPED 2026-06-19.** +6 display-only rows (dashboard 20→26 config + 3 derived):
  drawer — `tips_10y_real` (DFII10), `capacity_util` (TCU), `mfg_employment` (MANEMP),
  `construction_employment` (USCONS); markets — `usd_index` (DTWEXBGS), `wti_oil` (DCOILWTICO).
  **FRED ids probed live 2026-06-19** (each returned a recent non-null obs). **Gold dropped:** FRED's
  LBMA $/oz series `GOLDAMGBD228NLBM` is discontinued and the only fallback (`IR14270`) is an ambiguous
  index, so no clean gold price was available. All `lin`, +6 FRED calls/run. SW shell → v17.
