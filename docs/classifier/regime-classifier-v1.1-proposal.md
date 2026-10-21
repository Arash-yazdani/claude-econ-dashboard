# Regime Classifier — v1.1 (carry-forward #1 back-test findings)

**Status:** ✅ **Accepted — D9 re-sign-off (Bruce), 2026-06-17.** Locked in `classify()` and wired into the pipeline (`prev_regime` from the cache). · **Jira:** [SCRUM-26 / B2](https://modeo-team-1.atlassian.net/browse/SCRUM-26) · **Supersedes:** thresholds in `regime-classifier-v1.0.md`

> Accepted as written (Fix A + Fix B). The 2024 "late_cycle vs soft_landing" nuance (trade-off #1) was reviewed and accepted; revisit at the first O3 quarterly recalibration if desired. Finding C (stagflation untested) left open for O3.

This proposal comes from running the **carry-forward #1 back-test** (the #1 D9 validation
task) over **144 months of real FRED history (2014-07 … 2026-06)**. v1.0 is **not changed**
in code — it remains the locked `classify()`; this is implemented as `classify_proposed()`
and is **not wired into the pipeline** until/unless signed off.

## What the back-test found (v1.0)

| # | Severity | Finding | Evidence |
|---|---|---|---|
| **A** | 🔴 correctness | The `soft_landing` **default** masks hot inflation | **2022-04 → 2023-03** (core CPI 5–6.6%, the 40-yr peak) was labeled **soft_landing** |
| **B** | 🟠 robustness | `recovery ↔ soft_landing` **oscillation** | **10 flips in 2015–2017**, driven only by Δ3m-unemployment wobbling across −0.2 |
| **C** | 🟡 blind spot | `stagflation_risk` **never fired** in 12y | requires core≥3 **and** GDP YoY<1 simultaneously — never coincided |

Root cause of A: when core is hot (≥3.0) but GDP YoY sits between "weak" (<1.0 → stagflation)
and "strong" (≥2.5 → late-cycle), **every rule fails and it falls through to `soft_landing`**,
the most benign label. 2022 sat exactly there (GDP YoY ~2.4–2.5).

Validated correctly by v1.0 (no change needed): **2020 COVID → `slowdown`** (gdp −7.4,
unemp 14.8, core 1.4), and the unemployment spike to 14.8% passed the B1 plausibility gate.

## Proposed v1.1 changes

**Fix A — hot inflation is never a "soft landing".** Add one rule before the default: if
nothing else matched **and** `core ≥ 3.0`, label `late_cycle_expansion` ("running hot")
instead of `soft_landing`. Minimal and easy to reason about; touches only the fallthrough.

**Fix B — recovery hysteresis.** Enter `recovery` only on a clear fall (`Δ3m unemployment ≤ −0.3`,
was −0.2); once in recovery, **stay** until `Δ3m > +0.1`. Sticky band stops the chatter.

`stagflation_risk` (Finding C) is **left unchanged** — widening it is risky (e.g. it would
mislabel the 2021 reopening boom), so we flag it for Bruce rather than guess.

## Before / after (same 144 months)

| | transitions | soft_landing | late_cycle | recovery | slowdown | stagflation |
|---|---|---|---|---|---|---|
| **v1.0** | 20 / 12y | 66% | 15% | 13% | 6% | 0% |
| **v1.1** | **14 / 12y** | **37%** | **37%** | 20% | 6% | 0% |

Hot-inflation months rescued from `soft_landing` → `late_cycle_expansion`: **2022-04 (core 6.2)**,
2021-05 (3.8), 2024-06 (3.3), 2025-07 (3.1). 2015–17 recovery chatter: **~10 flips → ~4**.

## Trade-offs to weigh (the sign-off decision)

1. **late_cycle stickiness.** v1.1 labels most of **2021–2025** `late_cycle_expansion`. This
   correctly kills the 2022 bug, but it is coarse: **mid-2024** (core ~3.3 and falling, solid
   growth) becomes `late_cycle` when the popular read was an actual *soft landing*. If Bruce
   wants 2024 to read `soft_landing`, the hot-inflation cutoff (3.0) or the rule needs nuance
   (e.g. only force late_cycle when core is *rising* or ≥ ~3.5).
2. **Finding C unaddressed** — `stagflation_risk` still cannot fire on this history. Acceptable
   if we accept it's a rare regime; otherwise the growth gate (YoY<1) needs rethinking
   (sequential vs YoY growth).
3. **Label vocabulary** — 5 labels can't cleanly separate "overheating" from "late-cycle". A
   dedicated label is a V2 consideration, out of scope for a threshold tune.

## Recommendation

Adopt **Fix A** before G1/launch — "6% inflation = soft landing" is a credibility risk worth
fixing now. Adopt **Fix B** too (pure robustness win, no downside seen). Decide the **2024
nuance** (trade-off #1) with Bruce. On sign-off, promote `classify_proposed` → `classify`,
update `regime-classifier-v1.0.md` → v1.1, and re-run this back-test as the gate.

Reproduce: `FRED_API_KEY=… python scripts/backtest_thresholds.py --years 12`
