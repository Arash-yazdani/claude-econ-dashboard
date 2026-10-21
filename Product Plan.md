# Product Plan — Stock Market Analysis Dashboard (Daily Conversation Briefing)

**Project:** stock-market-analysis-dashboard · **Owner/Builder:** Arash Yazdani · **Sole User (N=1):** Bruce · **Industry:** Personal Finance / Macro · **Budget:** $0/yr (free-tier APIs only)
**Kickoff:** Mon Jun 15, 2026 · **Hard deadline:** enter steady state (Phase 3c) by **Fri Jul 17, 2026** · **Source of truth:** `PRD.md` (q1–q57)
**Companion artifacts:** [Gantt chart.md](Gantt%20chart.md) · `Project Tracker.xlsx`

---

## Context — why this plan exists

`PRD.md` is a complete product requirements document: it defines *what* the product is (a personal U.S.-macro PWA whose single AI feature is the **Daily Conversation Briefing** — one LLM call/day producing a JSON briefing rendered as a card atop an Apple-Stocks-style dashboard) and *why* (Bruce needs one defensible sentence + two quotable numbers in <10 seconds). What the PRD does **not** contain is a sequenced, gated execution plan with owners and dates.

This Product Plan converts the PRD into that roadmap: **Design → Develop → Deploy → Operate**, plus an outlined V2 epic. Every PRD launch-blocker (the five Bruce sign-offs, the host/cron decision, the prompt v1.3 lock, the *live* 12-case regression, the LLM-judge prompt) is promoted to a gated, owned, dated line item so nothing trust-critical can slip through silently.

**Intended outcome:** a trustworthy V1 in Bruce's hands at steady state by Fri Jul 17, 2026, achieved without compromising the two user-validation cohorts that earn that trust.

---

## Operating assumptions

| Assumption | Value |
|---|---|
| Organization | Arash (builds + operates everything) + Bruce (sole user; sign-offs + reviews). No other staff. |
| Budget | $0/yr. Free-tier Gemini 2.5 Flash + Groq Llama 4 Scout failover. **No payment method on file** with any vendor (quota exhaustion = 429s, never a surprise bill). |
| Calendar convention | **Design / Build / Validation** scheduled on business days (Mon–Fri). **Cohort pilots** run on **calendar days** — the daily cron and Bruce's usage run every day, including weekends. |
| Critical path | G0 → LLM integration (B3) → validator (B4) → live regression (V1) → G2 → Cohort 0 (G3) → Cohort 1 (G4 = deadline). The live regression gate is the single highest-risk node. |
| Owner key | **A** = Arash · **B** = Bruce |

### How the Jul 17 deadline is met
The PRD's **5-day Cohort 0** (Arash dry run) and **14-day Cohort 1** (Bruce supervised) are preserved in full — these cohorts are where trust is earned and must not be shortened. The pull-in is absorbed entirely on the engineering front: **Design + Build + Validation** compressed from ~12 to ~10 working days (build starts Fri Jun 19; the integration buffer is trimmed onto Jun 24). Steady state begins exactly **Fri Jul 17, 2026**.

### Two schedule risks (carried in the Risk register)
1. **Thin front-end buffer.** If the live 12-case regression (V1, Jun 25) misses its C2/C6 gate, Cohort 0 slips and Jul 17 is at risk. The PRD's own rule — validator pass rate <85% in the first 5 days → v1.3 fix sprint — is the primary slip trigger. **Watch this node first.**
2. **Cohort 1 spans the Jul 4 holiday weekend** (Jul 3–16). Bruce's first real-use weekend coincides with Independence Day; expect possibly low open-rate Jul 4–5. The automated cron is unaffected; only Bruce's daily-open metric is exposed.

---

## Phase summary

| Phase | Window (2026) | Owner | Exit gate |
|---|---|---|---|
| **0 — Design Lock & Decisions** | Mon Jun 15 – Thu Jun 18 | A + B | **G0** Design Lock (Jun 18) |
| **1 — Develop / Build** | Fri Jun 19 – Wed Jun 24 | A | **G1** Build Complete (Jun 24) |
| **2 — Pre-Launch Validation & Readiness** | Thu Jun 25 – Fri Jun 26 | A + B | **G2** Launch-Ready (Jun 26) |
| **3a — Deploy: Cohort 0 (Arash dry run)** | Sat Jun 27 – Wed Jul 1 | A | **G3** Cohort 0 Exit (Jul 2) |
| **3b — Deploy: Cohort 1 (Bruce supervised)** | Fri Jul 3 – Thu Jul 16 | B (+A) | **G4** Cohort 1 Exit (Jul 17) |
| **3c — Deploy: Cohort 2 (steady state)** ★ | **Fri Jul 17 → ongoing** | B (+A) | — (next promotion = V2) |
| **4 — Operations & Continuous Improvement** | from Jul 10 → ongoing | A + B | recurring cadence |
| **5 — V2 Epic (outlined)** | ~Q4 2026 → | A + B | gated by paid-budget + IAA re-eval |

★ Jul 17 is the hard deadline; reaching steady state is the terminal planned milestone of V1.

---

## Phase 0 — Design Lock & Decisions (Jun 15–18)
*Goal: convert every open design decision and Bruce sign-off into a locked, written artifact. Resolves PRD Open Questions #1 (sign-offs), #3 (host/cron), #4 (judge prompt).*

| ID | Task | Owner | Start | End | Dur | Depends |
|----|------|-------|-------|-----|-----|---------|
| D1 | Sign-off #1: personal-tool consent + first-name-in-prompt acknowledgment | B | Jun 15 | Jun 15 | 1d | — |
| D2 | Sign-off #2: banned-phrase list (O8) + no-securities scope | B | Jun 15 | Jun 15 | 1d | — |
| D3 | Sign-off #3: regime classifier thresholds (A drafts → B approves) | A→B | Jun 16 | Jun 16 | 1d | — |
| D4 | Sign-off #4: final indicator list 5–7 (A proposes → B approves) | A→B | Jun 16 | Jun 16 | 1d | — |
| D5 | Sign-off #5: tone variant A/B/C → **prompt v1.3 lock** | A→B | Jun 17 | Jun 17 | 1d | — |
| D6 | **Decision:** PWA host + cron runner (Vercel/Netlify/CF Pages; GH Actions/launchd/VPS) | A | Jun 15 | Jun 16 | 2d | — |
| D7 | Finalize wireframe + 5 render states (default/loading/stale/fallback/error) | A | Jun 16 | Jun 17 | 2d | — |
| D8 | Lock schemas: BriefingRequest / BriefingOutput + plausibility ranges | A | Jun 17 | Jun 17 | 1d | — |
| D9 | Lock deterministic regime classifier rules | A | Jun 17 | Jun 17 | 1d | D3 |
| D10 | Finalize 12-case regression test set (5 typical / 4 edge / 3 adversarial) | A | Jun 18 | Jun 18 | 1d | D5, D8 |
| D11 | Draft LLM-judge prompt (Layer 2) | A | Jun 18 | Jun 18 | 1d | D10 |

**◆ G0 — Design Lock (Jun 18):** all five Bruce sign-offs captured (ADR-style commits) · host + cron chosen · prompt v1.3 locked · schemas, classifier rules, 12-case set and judge prompt drafted. *Miss → build cannot start; resolve before Jun 19.*

---

## Phase 1 — Develop / Build (Jun 19–24)
*Goal: stand up the end-to-end pipeline on a real device. Days Jun 19/22/23 are the PRD's "3-day build"; Jun 24 adds the eval harness + monitoring.*

| ID | Task | Owner | Start | End | Dur | Depends |
|----|------|-------|-------|-----|-----|---------|
| B1 | Data pipeline: FRED ingest, cache (≤5MB), delta compute, pre-LLM plausibility validation | A | Jun 19 | Jun 19 | 1d | G0 |
| B2 | Deterministic regime classifier implementation | A | Jun 19 | Jun 19 | 1d | D9 |
| B3 | LLM integration: Gemini 2.5 Flash (responseSchema) + Groq Llama 4 Scout failover + prompt assembly | A | Jun 22 | Jun 22 | 1d | D5, B1 |
| B4 | 17-check output validator (TDD) + retry-once-then-deterministic-template fallback | A | Jun 22 | Jun 23 | 2d | B3 |
| B5 | PWA: Apple-Stocks rows + briefing card + 5 render states + freshness footer | A | Jun 23 | Jun 23 | 1d | D7, B4 |
| B6 | Cron runner + secrets (FRED/Gemini/Groq keys, free tier, no payment method) | A | Jun 24 | Jun 24 | 1d | D6 |
| B7 | LLM-judge harness (Layer 2) wired to the 12-case regression set | A | Jun 24 | Jun 24 | 1d | D11, B4 |
| B8 | Monitoring: JSONL logs, eval_log.csv, Sentry, healthchecks.io, UptimeRobot, /docs/monitoring.md | A | Jun 24 | Jun 24 | 1d | B4 |
| B9 | End-to-end integration on real device + buffer | A | Jun 24 | Jun 24 | 1d | B5, B6, B7, B8 |

**◆ G1 — Build Complete (Jun 24):** cron → FRED pull → classifier → LLM (+failover) → 17-check validator → fallback → briefing.json → PWA card renders, end-to-end on a real device. *Miss → no validation phase; protect the design buffer to recover.*

---

## Phase 2 — Pre-Launch Validation & Readiness (Jun 25–26)
*Goal: prove the system measure-passes the ship gate before any cohort sees it. Resolves PRD Open Question #6 (validator rounding edge cases).*

| ID | Task | Owner | Start | End | Dur | Depends |
|----|------|-------|-------|-----|-----|---------|
| V1 | **Live 12-case regression on Gemini 2.5 Flash** — must measure C2=5/5, C6=5/5, mean ≥4.0 | A | Jun 25 | Jun 25 | 1d | G1 |
| V2 | Validator TDD on edge cases: negatives, small fractions, basis-point conversions (O5) | A | Jun 25 | Jun 25 | 1d | B4 |
| V3 | 23-item technical readiness checklist | A | Jun 25 | Jun 26 | 2d | G1 |
| V4 | 8 operational runbooks (cron, validator-fail, LLM failover, FRED outage, Bruce-disagrees, prompt-change, quarterly-tone, Bruce-FAQ) | A | Jun 26 | Jun 26 | 1d | — |
| V5 | 4 pre-launch compliance sign-offs (consent / scope / thresholds+list+tone / private-repo+secrets) | B | Jun 26 | Jun 26 | 1d | V1 |

**◆ G2 — Launch-Ready (Jun 26):** live regression green (C2 & C6 at 5/5, mean ≥4.0) · 4 compliance sign-offs captured · 23-item checklist green · runbooks written. *This is the last gate before real data and the cohort pilot.*

---

## Phase 3a — Deploy: Cohort 0 — Arash dry run (Jun 27 – Jul 1, 5 calendar days)
*Goal: run the live pipeline against real (not synthetic) FRED data for five straight days before Bruce touches it.*

| ID | Task | Owner | Start | End | Dur | Depends |
|----|------|-------|-------|-----|-----|---------|
| C0.1 | Daily pipeline run + validator pass-rate monitoring | A | Jun 27 | Jul 1 | 5d | G2 |
| C0.2 | Live regression re-run on real data | A | Jun 27 | Jul 1 | 5d | G2 |

**Most important week-1 signal:** validator pass rate on real data. **If <85% across the first 5 days → launch pauses for a v1.3 fix sprint** (this is the primary Jul 17 slip trigger).

**◆ G3 — Cohort 0 Exit (Jul 2):** pipeline ran 5/5 days · validator 100% · live regression C2=5/5, C6=5/5, mean ≥4.0. *All met → Cohort 1. Any miss → fix + restart Cohort 0.*

---

## Phase 3b — Deploy: Cohort 1 — Bruce supervised (Jul 3 – Jul 16, 14 calendar days)
*Goal: Bruce uses the briefing daily in real conversations under close supervision.*

| ID | Task | Owner | Start | End | Dur | Depends |
|----|------|-------|-------|-----|-----|---------|
| C1.0 | Onboarding: PWA install 1-pager + FAQ-bruce.md + sample-day screenshots + launch SMS | A | Jul 3 | Jul 3 | 1d | G3 |
| C1.1 | Bruce daily use (Mode 1 — conversation prep) | B | Jul 3 | Jul 16 | 14d | C1.0 |
| C1.2 | Friday weekly review #1 (10 min) | A+B | Jul 10 | Jul 10 | — | C1.1 |
| C1.3 | Friday weekly review #2 + promotion review (10 min; coincides with G4) | A+B | Jul 17 | Jul 17 | — | C1.1 |
| C1.4 | Bruce 10% weekly sample ratings (C1 quotability / C3 tone) | B | Jul 3 | Jul 16 | 14d | C1.1 |

**◆ G4 — Cohort 1 Exit (Jul 17) — THE DEADLINE:** daily-open rate ≥4 days/wk · Bruce 10% sample mean ≥4.0 on C1/C3 · **zero** fallback renders · **zero** banned-phrase escapes · ≤1 disagreement. *All met → steady state. ≥2 disagreements → triage + extend 7 days (deadline at risk).*

---

## Phase 3c — Deploy: Cohort 2 — Steady state (Jul 17 → ongoing) ★ deadline met
*Goal: trustworthy daily operation at ≤30 min/wk maintenance.*

| ID | Task / Milestone | Owner | Target | Depends |
|----|------------------|-------|--------|---------|
| C2.1 | Steady-state operations (Arash ≤30 min/wk) | A | Jul 17 → ongoing | G4 |
| M-κ | Judge–Bruce Cohen's κ ≥0.6 **by week 8 (~Aug 28)** | A+B | Aug 28 | C2.1 |
| M-miss | "Would you miss this if it disappeared?" = yes at wk 4 / 8 / 12 (~Jul 31 / Aug 28 / Sep 25) | B | Sep 25 | C2.1 |
| M-healthy | 6-month healthy target: 0 fallback renders/90d · κ ≥0.7 · cost $0 · prompt versions 4–8 · regression grown 12→~18 cases | A+B | ~Jan 2027 | C2.1 |

---

## Phase 4 — Operations & Continuous Improvement (recurring, from Jul 10)

| ID | Ritual | Owner | Cadence | Starts |
|----|--------|-------|---------|--------|
| O1 | Weekly Friday triage (30 min; Bruce 10 min): scan dashboard → review Bruce notes → run regression → ship hot-fix OR queue OR "no change" | A+B | weekly | Jul 10 |
| O2 | Monthly retro (45 min; Bruce 15 min): theme aggregation, κ recalc, fallback rate, threshold drift → versioned changeset + CHANGELOG entry | A+B | monthly | ~Jul 31 |
| O3 | Quarterly recalibration (2h; Bruce 30 min): 3 new tone variants, model-marketplace re-check, free-tier audit, indicator-list review, regression refresh | A+B | quarterly | ~Oct 2026 |
| O4 | Continuous regression gate on every prompt/classifier/model/indicator change — block ship if C2<100%, C6<100%, mean<4.0, or any criterion <3 on >10% of items | A | event-driven | always-on |

**Error-triggered rules (always-on):** validator second-failure → same-day incident · LLM p95 >5s for 5 days → Groq failover · banned-phrase escape → immediate prompt rollback · κ <0.6 → re-prompt judge · free-tier deprecation → Bruce paid-budget decision within 48h.

---

## Phase 5 — V2 Epic (outlined; sequenced, not detail-scheduled)
*Out of V1 scope. Each item ships only through the O4 continuous regression gate, and public release is gated by an Investment Advisers Act (1940) re-evaluation.*

- **Candidate V2.0 (~Q4 2026)** — gated by a Bruce paid-budget sign-off + IAA re-eval:
  - **Release-day push notifications** — closes the Mode 3 intra-day gap (PRD Open Q#8, the "most-likely first ask").
  - **Driver-attribution badges** — addresses pain P4 ("hard to see which indicators drove the signal").
- **Backlog pool:** talking-points strip · historical-analog finder · indicator pairing · voice/TTS · LLM-as-classifier fallback · copy-to-clipboard · per-day briefing history · user-triggered regenerate · sector/international expansion.
- **V1.1 model upgrade path** (independent of V2): when Bruce approves any paid budget, promote Gemini → Claude Haiku 4.5 with prompt caching at ~$0.11/yr.

---

## Milestones & Decision Gates

| Gate | Name | Date | Pass condition (summary) |
|------|------|------|--------------------------|
| **G0** | Design Lock | Jun 18 | 5 sign-offs + host decision + prompt v1.3 lock |
| **G1** | Build Complete | Jun 24 | E2E pipeline runs on a real device |
| **G2** | Launch-Ready | Jun 26 | Live regression green + 4 compliance sign-offs + checklist |
| **G3** | Cohort 0 Exit | Jul 2 | 5/5 pipeline days, validator 100%, C2/C6 5/5, mean ≥4.0 |
| **G4** | Cohort 1 Exit ★ | **Jul 17** | DOR ≥4/wk, mean ≥4.0, 0 fallback, 0 escapes, ≤1 disagreement |
| M-κ | Judge–Bruce κ ≥0.6 | ~Aug 28 | Cohen's κ ≥0.6 (Layer-3 calibration) |
| M-healthy | 6-month healthy | ~Jan 2027 | 0 fallbacks/90d, κ ≥0.7, cost $0 |

---

## Sign-off Tracker (launch-blocking)

| # | Sign-off | Owner | Required by | Captured as |
|---|----------|-------|-------------|-------------|
| 1 | Personal-tool consent + first-name-in-prompt | B | Jun 15 (G0) | ADR-style commit |
| 2 | Banned-phrase list (O8) + no-securities scope | B | Jun 15 (G0) | ADR-style commit |
| 3 | Regime classifier thresholds | B | Jun 16 (G0) | ADR-style commit |
| 4 | Final indicator list (5–7) | B | Jun 16 (G0) | ADR-style commit |
| 5 | Tone variant → prompt v1.3 lock | B | Jun 17 (G0) | ADR-style commit |
| 6 | Compliance: personal-tool consent (re-confirm) | B | Jun 26 (G2) | ADR-style commit |
| 7 | Compliance: banned-phrase + no-securities scope | B | Jun 26 (G2) | ADR-style commit |
| 8 | Compliance: thresholds + indicator list + tone | B | Jun 26 (G2) | ADR-style commit |
| 9 | Compliance: private repo + encrypted secrets + no payment method | A | Jun 26 (G2) | ADR-style commit |

*Without sign-offs 6–9, do not start Cohort 1.*

---

## Risk & Open-Questions Register (from PRD + schedule)

| ID | Item | Resolved in / Mitigation | Sev |
|----|------|--------------------------|-----|
| R-S1 | **Thin front buffer** — V1 regression miss slips Jul 17 | Cohort 0 <85% rule → v1.3 fix sprint; protect design buffer | High |
| R-S2 | Cohort 1 spans Jul 4 weekend — low open-rate risk | Cron unaffected; weight DOR around holiday | Med |
| OQ1 | 5 Bruce sign-offs outstanding (launch-blocking) | Phase 0 (D1–D5) | High |
| OQ2 | Live 12-case regression not yet run on Gemini | Phase 2 (V1) + Cohort 0 (C0.2) — measure-pass, not predict-pass | High |
| OQ3 | PWA host + cron runner undecided | Phase 0 (D6) — must lock before Cohort 0 | High |
| OQ4 | LLM-judge prompt unwritten | Phase 0 (D11) + Phase 1 (B7) | Med |
| OQ5 | PWA telemetry approach ambiguous (endpoint vs self-report) | Resolve during Cohort 0 | Med |
| OQ6 | Validator rounding edge cases underspecified | Phase 2 (V2 TDD) | Med |
| OQ7 | Banned-phrase list completeness | Expand at first quarterly retro (O3) | Low |
| OQ8 | Mode 3 intra-day release gap | V1 accepted; V2.0 first ask (Phase 5) | Low |
| OQ9 | Alert fatigue (~12 thresholds, 1 channel) | Tune sensitivity after Cohort 0 week 1 | Low |
| OQ10 | Judge–Bruce κ baseline takes ~8 weeks | Bruce ratings are ground truth until M-κ | Low |
| HS | q47 quota/cost hard-stops (Gemini RPD/TPM, FRED calls, p95 latency, fallback rate, cost, banned-phrase escapes) | Monitored in /docs/monitoring.md; auto-pause on C2/C6 fail | — |

---

## Success Metrics & KPI Targets

**Primary outcome (the only PMF metric):** Quotable-sentence rate — weekly self-report "Did you quote this briefing out loud this week?" Target **≥3 of 4 weeks = yes**.

| Category | Metric | Target | Non-negotiable? |
|----------|--------|--------|-----------------|
| Adoption | Daily open rate | ≥4 days/wk (Cohort 1); ≥3 (steady) | No |
| Adoption | Time-to-close after open | p50 ≤10s, p95 ≤20s | No |
| Satisfaction | Bruce satisfaction (1–5) | mean ≥4.0 by wk 4 | No |
| Satisfaction | "Would you miss it?" | Yes at wk 4 / 8 / 12 | No |
| Trust | Disagreement rate | ≤1/wk (Cohort 1); ≤2/mo (steady) | No |
| Cost | Annual operating cost | $0 | Yes |
| Effort | Builder maintenance | ≤30 min/wk steady | No |
| AI L1 | C2/O5 number grounding | **100%** | **Yes** |
| AI L1 | C6/O16+O17 refusal envelope | **100%** | **Yes** |
| AI L1 | O8 banned-phrase escapes | **0 lifetime** (hair-trigger rollback) | **Yes** |
| AI L1 | All other O-checks | ≥95% rolling 30-briefing | No |
| AI L2 | Judge means C1/C3/C4/C5 | ≥4.0; ≥11/12 regression pass | No |
| AI L3 | Bruce 10% weekly sample | mean ≥4.0 from wk 4; κ ≥0.6 by wk 8 | No |
| Perf | LLM latency | p50 ≤2.0s, p95 ≤5.0s; pipeline p95 ≤60s | No |

**Explicitly not measured:** NPS, MAU/DAU, revenue, virality, precision/recall/F1 (all meaningless at N=1 / generative narration).

---

## Critical Path & deadline confidence

```
G0 (Jun 18) → B3 LLM integration (Jun 22) → B4 validator (Jun 23) →
V1 live regression (Jun 25) → G2 (Jun 26) → Cohort 0 (Jun 27–Jul 1) →
G3 (Jul 2) → Cohort 1 (Jul 3–16) → G4 = STEADY STATE (Jul 17) ★
```

The critical path is fully sequential with no parallel slack after G2. **The Jul 17 deadline is achievable but single-threaded** — V1 (live regression) and G3 (Cohort 0 validator pass) are the two nodes that, if missed, push the date. Both have PRD-defined fallback rules (retry-once-then-template; v1.3 fix sprint) that protect *quality* at the cost of *schedule*. If a slip occurs, the honest call is to extend the date, not to shorten Cohort 1 — the 14-day supervised pilot is the trust-earning core of V1.
