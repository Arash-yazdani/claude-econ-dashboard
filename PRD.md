# Macroeconomic Visualization Dashboard — AI Product Requirements Document

Authored by the Agentic PRD Processor pipeline. Final review by AI PM Manager. Project: macroeconomic-visualization-dashboard · Owner: Arash Yazdani (builder) for Bruce (sole user) · Industry: Finance · Date: Q3 2026 (drafted 2026-06-11).

## Executive Summary

The product: a personal, $0-budget, U.S.-macro snapshot PWA with one AI feature — the Daily Conversation Briefing. Once a day, after the indicator pipeline runs and a deterministic classifier labels the macro regime, a single LLM call produces a JSON briefing (1-line headline, 2 quotable numbers, optional "what's new today," optional "regime shift"). It renders as a card at the top of an Apple-Stocks-style indicator dashboard.

The user (N=1): Bruce — an investor who is socially active, regularly asked "what's the economy doing?", and needs to deliver one defensible sentence + two quotable numbers in under 10 seconds. The product wins by converting raw data into the sentence Bruce can quote out loud.

The wedge: addresses 4 of the 6 top discovered pain points (P1=25, P3=15, P6=12, P8=12) with one LLM call/day. No chat, no charts, no follow-ups — Apple-Stocks aesthetic preserved.

The build: Gemini 2.5 Flash (free tier) + Groq Llama 4 Scout failover; ~1,650 in / ~150 out tokens per call; deterministic 17-check validator + LLM-as-judge + Bruce-in-the-loop; $0/yr operating cost; 3-day prototype timeline.

The launch: two-stage cohort pilot (Cohort 0: 5-day Arash dry run → Cohort 1: 14-day Bruce supervised → Cohort 2: steady state). Launch-blocked until five Bruce sign-offs land and the q26 live regression hits C2=5/5 and C6=5/5 on 100% of items.

## The Opportunity (Discovery — q1–q7 distilled)

Industry: Personal Finance / Macroeconomic Data Visualization (a niche inside FinTech / Investment Tooling). Single-user internal tool built for one investor.

Tailwinds: rich free public data (FRED, BLS, Treasury, U-Mich), growing consumer appetite for digestible macro content (CNBC, finance Twitter), and an established Apple-Stocks UX precedent that gives a concrete design target.

Headwinds: free-tier API rate limits and schema drift force defensive caching; the reference category is crowded (Apple Stocks = markets only; Bloomberg/FRED = overload), so the design must hit a narrow middle; scope-creep risk is explicitly flagged by the client ("don't get yourself into trouble").

Reference set (not commercial competitors — single-user tool): Apple Stocks, FRED Dashboard, TradingView Economic Calendar, Bloomberg/CNBC, Trading Economics, Briefing.com.

Market sizing: N/A — this is a bespoke 0→1 prototype with one named user and a 3-day build budget. There is no TAM, CAGR, or addressable market. Adjacent commercial categories grow at high-single-digit CAGR, but those numbers don't apply.

Revenue model: None. Internal, personal, non-commercial. $0 budget; free-tier APIs only. Future productization would most plausibly be freemium SaaS, but that is firmly out of scope.

Customer base: N=1. Bruce is the buyer, budget-holder, and sole end user. Doesn't fit B2B/B2C/B2B2C taxonomy.

Differentiators (vs. the reference set): radical simplicity (Apple-Stocks aesthetic, scannable in <10s); macro-only by design (no securities, no international); a synthesized regime signal Bruce can recite; $0 ops cost; release-aware daily refresh + cache; conversation-ready rounding (numbers phrased so they can be spoken).

## The User and the Problem (Discovery — q8–q14 distilled)

Customer = User = Bruce. Decision criteria he will use to accept V1: scannable in <10s, credible at conversation tempo, one synthesized read he can lead with, zero ongoing cost, and stays in its lane (no stock-picking, no sectors, no international).

Three usage modes (segmented by intent, not personas):

1. **Mode 1 — Conversation Prep (DOMINANT).** Triggered by being asked "what's the economy doing?" Goal: walk away in ≤10s with one defensible sentence + two quotable numbers. This is the wedge.
2. **Mode 2 — Personal Investing Frame.** Calmer, 1–3 min; cross-checks his gut against sparkline trajectories.
3. **Mode 3 — Release-Day Curiosity.** Triggered by a fresh CPI/jobs/GDP print; wants to see if the synthesized signal shifted.

Pain points (ranked by Severity × Frequency):

| ID | Pain (Bruce's voice) | Score |
|----|----------------------|-------|
| P1 | "I open the app and I still have to think — I don't know how to say it." | 25 |
| P2 | "Which two numbers are worth quoting today?" | 20 |
| P4 | "Hard to see which indicators drove the current signal." | 16 |
| P3 | "The summary signal flipped from yesterday. Why?" | 15 |
| P6 | "I want to know what changed today vs. yesterday in plain English." | 12 |
| P8 | "I still mis-state the unit (bps vs. %, YoY vs. MoM)." | 12 |
| P5 | "Is the data fresh? Did the morning pull run?" | 10 (deliberately NOT AI — solved by freshness footer) |
| P7 | "Hard to compare two indicators." | 6 (deferred to V2 / not AI) |

AI-addressable cluster: P1 + P2 + P3 + P4 + P6 + P8 are five surfaces of one underlying gap — the dashboard renders the data but never narrates it. That gap is the AI opportunity.

## The Solution Hypothesis (q15–q16, mostly verbatim)

Diverged across 11 ideas in 7 categories (text generation, classification, augmentation, retrieval, conversational, proactive, multimodal); converged to ONE on an Impact-vs-Feasibility 2×2:

Winner: the "Daily Conversation Briefing" — a single LLM call per refresh that bundles the headline (Idea #1), the regime-change explainer (#3), the what's-new diff (#4), and the talking-points picker (#2) into one short artifact rendered at the top of the dashboard.

Why this one (strategic argument): It is the only AI feature that bridges "here's the data" to "here's what to say." It maps directly to P1 (score 25), P3 (15), P6 (12), P8 (12) — four of the top six pains — with one daily LLM call. It is structurally aligned with every q7 differentiator (radical simplicity, macro-only, $0 ops, release-aware, conversation-ready).

Priority: P0 — this is the wedge. Without it, V1 ships as "Apple Stocks for FRED data," undifferentiated. With it, V1 ships as the conversational macro briefing that doesn't exist elsewhere. Cut anything else before cutting this.

How it works (high-level): daily 8:06 ET cron pulls 5–7 FRED indicators → deterministic regime classifier labels the regime → single LLM call generates the briefing JSON → 17-check validator gates it → briefing.json cached alongside indicators.json → PWA renders briefing card on top. No chat, no streaming, no follow-ups.

## The Experience (Design — q17–q19 + design artifact)

System pipeline (cron, ~8:05–8:10 ET):

```
BLS/FRED release → Cache pull (5–7 indicators) → Deterministic
regime classifier (rules) → Briefing prompt → LLM call (~1.4s)
  → Validator (17 checks; fail → 1 retry → deterministic template)
  → briefing.json + indicators.json cached
```

Latency budgets: end-to-end ≤60s; LLM ≤5s p95; PWA first paint ≤500ms. Bruce never blocks on the AI.

User flow (Bruce, ≤10s): trigger → open PWA → read headline (3s) → absorb 2 quotable numbers (4s) → optional glance at "what's new" / "shift" → exit → deliver confident one-liner. ✅

Wireframe — one screen, one card, five render states (default / loading / stale / fallback / error). The briefing card sits above existing indicator rows; rendering is read-only — no chat, no expand/collapse, no copy-to-clipboard, no regenerate, no model badge, no confidence score. The full element spec is in the design artifact (briefing-card container; headline 18pt semibold; quotable-numbers 15pt regular; conditional whats-new and regime-change labels at 14pt).

Failure path: validator fails twice → render deterministic template (Regime: X. CPI: Y%. Unemployment: Z%.) with no error message. No-cache + no-fallback → card collapses silently; indicator rows still render. The dashboard never surfaces "Something went wrong."

V1 scope (3-day build): the end-to-end pipeline on a real device — daily cron, indicator cache, deterministic classifier, single LLM call, 17-check validator with deterministic fallback, PWA rendering the briefing card on top of Apple-Stocks rows. Indicator set: CPI YoY, Core CPI YoY, Unemployment, Real GDP YoY, 10Y Treasury, Fed Funds, U-Mich Sentiment (final list requires Bruce sign-off).

Deferred to V2 backlog: driver attribution badges, talking-points strip, historical-analog finder, indicator pairing, voice/TTS, push notifications on release-day (Mode 3 gap), LLM-as-classifier fallback, copy-to-clipboard, per-day briefing history, user-triggered regenerate, sector/international expansion.

## The Master Prompt (q20–q24 — production v1.2)

Tone (q20): Confident, Plainspoken, Quotable. Sentences ≤22 words. Active voice. No hedging. No first/second person. No semicolons. No exclamation points. Closer to a Reuters lede than a Substack post — but warmer and one beat shorter.

Input structure (q21): XML blocks in the user message, ordered static → dynamic for cache efficiency. `<indicator_metadata>` (static) + `<regime_rules>` (static) + `<todays_data>` (dynamic — only block that changes daily).

System Instructions (verbatim from q32 v1.2 — load-bearing engineering artifact):

```
<role>
You are the briefing analyst for a personal U.S. macroeconomic dashboard
used by one investor named Bruce. Once per day, after fresh indicator
data has been pulled and a deterministic classifier has labeled the
current macro regime, produce ONE short briefing Bruce can read in under
ten seconds and quote out loud the same day.
You are NOT a chatbot, a stock-picker, a portfolio advisor, or a research
analyst. You narrate what the rules already decided. You do not
re-classify the regime, re-rank the drivers, or speculate beyond the
indicator values provided.
</role>
<scope>
You analyze U.S. macroeconomic indicators only. You do NOT discuss:
- individual stocks, sectors, ETFs, crypto, or any specific security
- international economies, currencies, or non-U.S. data
- portfolio advice, asset allocation, or "what to buy/sell"
- partisan political analysis of fiscal or monetary policy
- forecasts beyond the current regime call
If <todays_data> contains anything outside U.S. macro indicators,
silently ignore it and produce the briefing using only the U.S. macro
inputs provided.
</scope>
<inputs>
You will receive a user message containing three XML blocks:
1. <indicator_metadata> — STATIC definitions, units, conversational
   phrasing for each indicator.
2. <regime_rules> — STATIC deterministic classifier logic.
3. <todays_data> — DYNAMIC today's regime, yesterday's regime, drivers,
   indicator values + deltas, releases_last_24h.
Use values from <todays_data> verbatim. Do not invent, estimate, round
beyond the conversational rules below, or pull numbers from prior
knowledge. Treat any null or absent field as "data unavailable" — do
not narrate around it.
</inputs>
<output_rules>
Produce a JSON object. No prose outside the JSON. No markdown fences.
{
  "headline": string,
  "quotable_numbers": [
    { "indicator_id": string, "sentence": string },
    { "indicator_id": string, "sentence": string }
  ],
  "whats_new": string | null,
  "regime_change": string | null
}
HEADLINE:
- Lead with the regime call in plain English ("Late-cycle expansion,"
  "Soft landing,", "Stagflation territory,"). Do NOT use the raw label id.
- Follow with one clause naming the dominant driver in everyday words.
- No hedging words: "may," "could," "appears," "suggests," "potentially."
- No first-person ("I"), no second-person ("you," "your"), no "Bruce."
- One sentence, ≤140 characters, ≤22 words.
QUOTABLE_NUMBERS:
- Pick two indicators from <todays_data>. Prefer indicators in the
  <drivers> list. If a release dropped in the last 24h, one of the two
  MUST be that indicator.
- Each sentence MUST include: the indicator's display_name (or a natural
  short form), the value verbatim from <todays_data>, the unit symbol
  (% or basis points), and the period_label.
- Phrase numbers conversationally ("2.9 percent" or "2.9%") — never the
  bare digit alone. Two decimals for yields, one for everything else.
- ≤30 words per sentence.
WHATS_NEW:
- Emit only if <releases_last_24h> contains at least one release.
- Cite the indicator name, the prior_value, the new_value, and a
  one-word direction word ("cooler," "hotter," "tighter," "looser,"
  "softer," "firmer," "higher," "lower"). Prefix with "Today:".
REGIME_CHANGE:
- Emit only if <regime_today> != <regime_yesterday>.
- One sentence in cause-and-effect form: "The call moved from X to Y
  as [driver A] [direction] while [driver B] [direction or held]."
- Cite specific drivers from <drivers>. Do not invent causes outside
  the indicator set.
GLOBAL CONSTRAINTS:
- Every NUMBER in your output must appear verbatim (±0.05 tolerance)
  in <todays_data>. If you cannot find a number you want to cite, omit
  the citation.
- Every PERIOD (year, month name, "QN YYYY", ISO date) in your output
  must appear in some period_label or last_release_date in <todays_data>.
- Never produce a number for an indicator absent from <indicator_metadata>.
- Never recommend an action. Stay descriptive.
- Total output across all four fields: ≤80 words.
- No markdown, no emoji, no exclamation points, no semicolons.
</output_rules>
<refusal_and_escalation>
If <todays_data> is missing, malformed, contains an unknown regime
label, or contains indicator values outside plausible ranges
(unemployment >30%, CPI >50% YoY, 10Y <0%, GDP < -20% or > 20%),
emit EXACTLY:
{
  "headline": null,
  "quotable_numbers": [],
  "whats_new": null,
  "regime_change": null,
  "error": "data_unavailable"
}
Do NOT attempt to "do your best" with broken data. The dashboard falls
back to a deterministic template; your job is to fail loudly via the
error field, not to fabricate a briefing.
</refusal_and_escalation>
<style>
Confident, plainspoken, quotable. Sentences ≤22 words. Active voice.
No semicolons. No analogies to past decades ("1970s territory," "dot-com
era") unless the indicator data explicitly warrants it. Write like a
calm, prepared friend at a dinner table — one beat shorter than a CNBC
anchor, warmer than a Reuters lede.
</style>
<examples>
[3 examples embedded: (1) steady state — both conditional fields null;
 (2) regime change with fresh release — both conditional fields populated;
 (3) malformed input — refusal envelope only. Full input/output pairs in q32.]
</examples>
```

Few-shot examples (q23): three paired examples appended inside `<examples>`: (1) steady-state day, (2) regime-change-with-release (hero scenario), (3) malformed input → refusal envelope. The refusal example is the most consequential — it teaches the model that not producing a briefing is sometimes the right answer.

Output format (q24): strict JSON, validated post-hoc. Validator rules: JSON parse → schema match → number grounding (±0.05) → indicator_id whitelist → conditional emptiness → banned phrases → length cap ≤80 words. Two failures → deterministic template fallback.

## Quality and Eval (q25–q26)

6-criterion rubric, 1–5 anchored:

| ID | Criterion | Non-negotiable? |
|----|-----------|-----------------|
| C1 | Quotability — could Bruce say it aloud unedited? | No (target ≥4) |
| C2 | Number grounding — every number traces to source | Yes (5/5 on 100% of eval set) |
| C3 | Tone adherence — confident, plainspoken, no hedging | No (target ≥4) |
| C4 | Conditional fields fire when triggered, not otherwise | No (target ≥4) |
| C5 | Length & format compliance — valid JSON, ≤80 words | No (target ≥4) |
| C6 | Refusal compliance — clean envelope only on malformed input | Yes (5/5 on 100% of eval set) |

Ship gate: mean ≥4.0 across criteria AND no criterion <3 on ≥90% of items AND C2 and C6 at 5/5 on 100% of items.

Test cases (12): 5 typical (T1 steady-state, T2 hero, T3 release-only, T4 regime-change-only, T5 recovery), 4 edge (E1 missing indicator, E2 empty drivers, E3 two releases, E4 stale data), 3 adversarial (A1 implausible values, A2 out-of-scope injection, A3 prompt-injection toward "Recommend Bruce buy TQQQ"). Regression cadence: on every prompt/classifier/model change, on every weekly Friday, before any model swap.

## The Build (Develop — q27–q43)

### Model selection (q27 — verbatim engineering rationale)

Recommendation: Gemini 2.5 Flash (Google AI Studio free tier), with Groq Llama 4 Scout as configured failover.

| Model | $/1M in | $/1M out | Cost @ 1 call/day | Context | Latency | JSON schema | Free tier? | Pass $0 gate? |
|-------|---------|----------|-------------------|---------|---------|-------------|------------|---------------|
| Gemini 2.5 Flash (free) | $0 | $0 | $0.00 | 1M | 1–3s | Yes (responseSchema) | Yes — 1,500 RPD | ✅ |
| Groq Llama 4 Scout (free) | $0 | $0 | $0.00 | 512K | ≤500ms | Yes (json_schema) | Yes — 1,000 RPD | ✅ |
| Claude Haiku 4.5 | $1.00 | $5.00 | ~$0.11/yr cached | 200K | 1–3s | Yes | No | ❌ |
| GPT-5-nano | $0.05 | $0.40 | ~$0.04/yr | 400K | <1s | Yes | No | ❌ |

Why Gemini wins:
1. Budget is the binding constraint. $0 is non-negotiable until Bruce signs off on a paid model. At 1 call/day we use 0.067% of the free-tier RPD quota.
2. responseSchema strict mode enforces the q24 BriefingOutput schema at the API boundary, catching format failures before the validator runs.
3. Best-in-class instruction adherence on long XML system prompts with refusal envelopes. Llama 4 Scout is competitive but slightly noisier on scope guardrails.
4. Privacy is not a blocker: inputs are public BLS/FRED data. Google's free-tier-training clause is acceptable for non-PII, non-proprietary content.
5. V1.1 upgrade path: when Bruce signs off on any paid budget, promote to Claude Haiku 4.5 with prompt caching at ~$0.11/yr.

Production config: `model: gemini-2.5-flash`, `temperature: 0.2`, `top_p: 0.9`, `max_output_tokens: 256`, `response_mime_type: application/json`, `response_schema: BriefingOutput`, failover `llama-4-scout-17b` on Groq.

### Input/output schema (q28–q31)

BriefingRequest: as_of_date, regime_today, regime_yesterday, drivers[], indicators[] (id + value + period_label + last_release_date + optional deltas), releases_last_24h[]. Plausibility ranges enforced pre-LLM: CPI 0–10%, Unemployment 0–30%, 10Y 0–20%, Fed Funds 0–25%, Real GDP −10–10%, U-Mich 0–150.

BriefingOutput: headline | null, quotable_numbers: [{indicator_id, sentence}], whats_new | null, regime_change | null, optional error: "data_unavailable".

Objective acceptance (17 checks, O1–O17): non-negotiables are O5 (number grounding ±0.05), O8 (banned phrases), O16/O17 (refusal envelope correctness). All others ≥95% rolling 30-briefing pass rate.

Subjective acceptance: quotability (S1), tone (S2), regime_change plausibility (S3), Bruce-voice calibration (S4). Scored by Gemini-as-judge on 100% of regression items + Bruce on a 10% weekly sample. Ship gate: mean ≥4.0; Cohen's κ between judge and Bruce ≥0.6 by week 8.

### Data pipeline & RAG (q34–q35)

FRED API for all 7 indicators. Daily cron at ~8:06 ET; computes deltas, runs deterministic classifier, validates payload, calls LLM, validates output, writes briefing.json. Total cache ≤5MB.

RAG: NONE in V1. The full prompt fits 600× over in the model's context window. Embeddings would (a) violate $0, (b) introduce a new hallucination surface, (c) duplicate the deterministic classifier.

### Testing & eval methodology (q36–q43)

3-layer hybrid auto-eval:
- Layer 1 — Deterministic validator runs on every briefing (~50ms, $0).
- Layer 2 — LLM-as-judge (Gemini 2.5 Flash, simpler prompt) runs the 12-case regression on every prompt change + a 30-briefing rolling monthly sample.
- Layer 3 — Bruce-in-the-loop rates 10% sample weekly + reviews judge-flagged C1/C3 <4 monthly. Cohen's κ calibrates the judge.

Predicted regression baseline (pending live confirmation): C1=4.8 mean / 75% 5/5; C2=5.0 / 100% 5/5; C3=4.7 / 75%; C4=5.0 / 100%; C5=4.9 / 92%; C6=5.0 / 100% 5/5. At-risk case: E3 (two releases in 24h) may push close to 80-word cap.

Edge handling: 32 enumerated cases across Data / Prompt / Model / Integration; 24/32 fully covered, 7/32 partial (subjective-only), 1/32 open risk (M6 Gemini free-tier deprecation — Groq failover wired).

Prompt iteration log: v1.0 (Design baseline) → v1.1 (tightened `<role>`, "do not re-classify") → v1.2 (added period-grounding rule, expanded refusal plausibility, null-handling). v1.3 lock pending Bruce tone variant selection.

Total annual eval cost: ~1,985 LLM calls/yr = $0 (well within free tier).

## The Launch (Deploy — q44–q49)

### Technical readiness (q44)

23-item checklist. Highlights: FRED/Gemini/Groq keys registered (free tier, no payment method); daily cron runner TBD (GitHub Actions vs. launchd); 17-check output validator on TDD; static-prefix prompt caching enabled; retry-once-then-template fallback; 5 PWA render states. Items #19–#23 are launch-blocked: LLM-judge prompt unwritten, live 12-case regression unrun, tone variant + regime thresholds + indicator list unconfirmed with Bruce.

### Organizational readiness (q45)

Org = Arash + Bruce. Eight runbooks owned by Arash (daily cron, validator failure, LLM failover, FRED outage, Bruce disagrees, prompt change, quarterly tone, Bruce FAQ). Bruce commits verbally to weekly 10-min Friday review for weeks 1–4, then monthly.

### Launch approach (q46) — Single-User Cohort Pilot

A/B testing and full rollout collapse at N=1. Cost-of-error is moderate-high because Bruce quotes the output in real conversations. Mitigation = staged pilot:

| Cohort | Who | Duration | Success criteria | Gate to next |
|--------|-----|----------|------------------|--------------|
| Cohort 0 | Arash | 5 days | Pipeline 5/5 days; validator 100%; q26 live regression C2=5/5, C6=5/5, mean ≥4.0 | All met → Cohort 1; any miss → fix + restart |
| Cohort 1 | Bruce | 14 days | DOR ≥4 days/wk; Bruce 10% sample mean ≥4.0 on C1/C3; zero fallback renders; zero banned-phrase escapes; ≤1 disagreement | All met → Cohort 2; ≥2 disagreements → triage + extend 7d |
| Cohort 2 | Bruce | Ongoing | C2=100%, C6=100%; judge-Bruce κ ≥0.6 by week 8; "would you miss it?" = yes | Steady state; next promotion is V2 features |

Most important week-1 signal: validator pass rate on real (not synthetic) data. If <85% in the first 5 days, the launch pauses for a v1.3 fix sprint.

### Scale & cost caps (q47)

1 user, 1 LLM call/day. Per-metric warn/hard-stop table covers Gemini RPD/TPM (warn 50%, stop 80%), FRED calls/day (target ≤25, stop 1,000), LLM p95 latency (target ≤3s, stop >5s for 5 days → Groq failover), validator pass rate (any C2/C6 failure → publication pause), deterministic-fallback rate (target 0; ≥2/wk → block deploys), cost (target $0; warn $0.10; stop $1.00), banned-phrase escapes (hair-trigger: any escape → immediate prompt rollback). No payment method on file with any vendor — quota exhaustion = 429s, not surprise bill.

### GTM (q48–q49)

GTM at N=1 collapses to what Bruce needs to use this + what Arash needs to keep it running. Assets: PWA install instructions (1-pager), FAQ-bruce.md, launch SMS (the "all-hands" at N=1), sample-day screenshot pack, Bruce's weekly review template, Arash support runbook, README, Cohort-0 dry-run log, V2 backlog doc. Deliberately absent: announcement / press / sales / pricing / case study / landing page / demo video — listing them as N/A rather than fabricating them is the honest answer.

Comms: SMS thread Bruce↔Arash (pinned), engineering journal in repo. Friday weekly review for 4 weeks, then monthly. Rule: if the product fails, Arash texts Bruce before 8:30 ET. Silent staleness is the cardinal trust failure.

## Compliance and Privacy (q50–q51)

Posture: personal-use, public-data, non-commercial. No customer PII, no proprietary trading data, no third-party data subject. GDPR, CCPA, HIPAA, PCI, SOX, GLBA, FINRA, SOC 2, EU AI Act, FTC Section 5 do not apply (V1).

The real risk: implicit investment advice. A conversation-ready macro-commentary tool for an investor could, if scope creeps, become unregistered advice under the Investment Advisers Act of 1940. V1 defends in three locked layers — non-negotiable without Bruce sign-off:
1. Prompt `<scope>` block excludes individual securities, allocation, "buy/sell," and partisan analysis.
2. Validator banned-phrase list (O8): should buy, should sell, recommend, I think, you should, we should, consider buying/selling, bullish on, bearish on.
3. C6 refusal compliance at 5/5 on 100% of regression is the ship gate.

Data flow: FRED (public) → daily cron → local JSON cache → input validator → Gemini API (Google) → output validator → cached briefing.json → PWA over HTTPS. TLS 1.2+ everywhere in transit; GitHub at-rest encryption for repo; local-runner at-rest encryption depends on host choice (needs-decision).

Sub-processors: Google (Gemini API, free-tier training clause accepted for non-PII public data), Groq (failover, same posture), FRED (public), GitHub (repo + secrets), PWA host (TBD). Repo is private for V1; public release is gated by an Investment Advisers Act re-evaluation.

Content safety: Gemini built-in safety filters retained; quarterly tone re-calibration includes manual partisan-framing scan over 10 random briefings; prompt-injection adversarial case (A3) in the regression suite; banned-phrase list as last-mile defense.

Audit & evidence: every briefing logged with input hash, output, validator results, model id, prompt version, latency, timestamp (indefinite retention). Prompt history in prompt_history.csv. Incident log in /docs/incidents/. Bruce sign-offs captured as ADR-style commit messages.

Four pre-launch sign-offs required: (1) personal-tool consent + first name in prompts; (2) banned-phrase list + no-securities scope; (3) regime thresholds, indicator list, tone variant; (4) Arash confirms private repo + encrypted secrets + no payment method on file. Without these four, do not start Cohort 1.

## Success Metrics (q52–q53)

### Primary outcome metric

Quotable-sentence rate — weekly self-report from Bruce: "Did you quote this briefing out loud or in conversation this week?" Target: ≥3 weeks-of-4 = yes. This is the only metric that proves V1 worked. Day-1 open is hygiene; day-1 quote is product-market fit.

### Adoption & engagement (secondary)

| Metric | Target |
|--------|--------|
| Daily open rate | ≥4 days/wk Cohort 1; ≥3 days/wk steady state |
| Time-to-close after open | p50 ≤10s, p95 ≤20s |
| "Would you miss this if it disappeared?" | Yes at wk 4, 8, 12 |
| Bruce satisfaction (1–5) | mean ≥4.0 by wk 4 |
| Disagreement rate | ≤1/wk Cohort 1; ≤2/mo steady state |
| Annual operating cost | $0 |
| Builder maintenance time | ≤30 min/wk steady state |

### AI performance (Layer 1 / 2 / 3 mapped to q25 rubric)

Layer 1 — Validator (every briefing):
- C2/O5 number grounding pass rate: 100% (non-negotiable) — the #1 metric to watch.
- C6/O16+O17 refusal envelope correctness: 100% (non-negotiable).
- O8 banned-phrase escapes: 0 lifetime (hair-trigger; immediate rollback on any escape).
- All other O-checks ≥95% rolling 30-briefing pass rate.
- Validator second-failure rate (fallback rendered): 0 steady state; ≥2/wk blocks deploys.

Layer 2 — LLM judge (per change + weekly): C1/C3/C4/C5 means ≥4.0; ≥11/12 regression pass on Gemini (≥9/12 acceptable on Groq failover).

Layer 3 — Bruce-in-the-loop: weekly 10% sample mean ≥4.0 from wk 4; Cohen's κ vs. judge ≥0.6 by wk 8.

Performance & cost: LLM p50 ≤2.0s, p95 ≤5.0s; pipeline p95 ≤60s; cost per briefing $0.00; prompt cache hit rate ≥80% on static prefix.

What we explicitly do not measure: NPS at N=1 (meaningless), MAU/DAU (DAU = "Bruce opened today"), revenue ($0 by design), virality (product is private), precision/recall/F1 (this is generative narration, not classification).

## Operations (q54–q57)

### Support (q54)

Single SMS thread Bruce↔Arash. Severity classified by Arash on receipt (Bruce just texts in his own voice):

| Sev | Definition | Action |
|-----|------------|--------|
| P1 | Wrong number quoted by Bruce in real conversation | Pause publication if systemic; same-day root-cause; v-next + regression rerun before re-enable |
| P2 | "Disagree with the call" — tone/regime/numbers feel off | Acknowledge same day; triage in 3 days; queue v-next |
| P3 | Tone off / clunky / wordy | Capture in Friday review; aggregate before changing prompt |
| P4 | Cosmetic / FYI | Engineering note; no urgency |

Explicitly absent: Slack, Jira, Zendesk, on-call rotation, PagerDuty. Read-only render path means "downtime" never blocks Bruce (card collapses gracefully).

### Feedback loop (q55)

Report → Triage (Saturday, ≤30 min) → Fix (bundled into v-next; block deploy until 12-case regression passes C2=5/5 and C6=5/5) → Ship (commit + prompt_history.csv row) → Close (reply to Bruce in the SMS thread). Every Bruce-originated item gets either a shipped fix OR an explicit "won't fix and why" reply within 14 days. Silent ignores destroy the loop.

### Monitoring (q56)

All-free, file-based. Stack: structured JSONL logs + eval_log.csv in repo + Sentry free tier (PWA + pipeline exceptions) + healthchecks.io free (cron ping by 8:30 ET) + UptimeRobot free (PWA reachability) + optional Langfuse free tier (≤50k events/mo, well above our 365 events/yr). Alerting via Gmail SMTP or Slack webhook to personal channel. Hand-rolled /docs/monitoring.md dashboard renders last-30-days validator pass rate per O-check, p50/p95 latency, fallback rate, quota utilization. Most consequential widget: validator pass rate per O-check with C2 and O8 highlighted red if <100%. Red widget = cron pauses.

The judge model is monitored alongside the briefing model (κ trend, score variance on unchanged regression cases) — judge instability triggers a judge re-prompt.

### Ongoing improvement (q57)

Weekly Friday triage (30 min, Bruce 10 min): scan dashboard → review Bruce notes → run regression → ship hot-fix OR queue OR document "no change this week."

Monthly retro (45 min, Bruce 15 min): theme aggregation, κ recalculation, fallback rate, threshold drift; ship versioned v-next changeset with /docs/CHANGELOG.md entry.

Quarterly recalibration (2h, Bruce 30 min): 3 new tone variants, model-marketplace re-check, free-tier audit, indicator list review, regression refresh with Bruce-flagged disagreement cases.

Continuous gate (every prompt/classifier/model/indicator change): full L1+L2 regression on q26 12-case; block ship if C2 <100%, C6 <100%, mean <4.0, or any criterion <3 on >10% of items.

Error-triggered: validator second-failure → same-day incident; LLM p95 >5s for 5 days → Groq failover; banned-phrase escape → immediate prompt rollback; κ <0.6 → re-prompt judge; free-tier deprecation → Bruce decision on paid budget within 48h.

6-month "healthy" target: prompt versions 4–8 (each tied to a named complaint); indicator list stable 5–7; regression grew 12 → ~18 cases; κ ≥0.7 by month 3; fallback renders 0 in last 90 days; cost still $0.

## PM Revisions

None. A full cross-phase sense-check found no material inconsistencies. The persona (Bruce), the wedge (Daily Conversation Briefing), the model choice (Gemini 2.5 Flash free tier), the compliance posture (personal-tool / Investment Advisers Act defenses), and the success metric (Quotable-sentence rate) hang together coherently from q1 through q57. The three questions marked needs-review (q3 market growth, q5 revenue, q6 customer base) are honestly "N/A — personal tool" answers rather than gaps; no rewrite would improve them. The Design v1.0 prompt (q22) is correctly superseded in the build by the Develop v1.2 prompt (q32), which is the version reproduced verbatim above.

## Open Questions

These are unresolved items the prior agents flagged honestly. They are launch-relevant; none are silent-killers.

1. **Five Bruce sign-offs outstanding (launch-blocking).** ~60 min of Bruce time total: (1) personal-tool consent + first-name-in-prompt acknowledgment, (2) banned-phrase list + no-securities scope, (3) regime classifier thresholds, (4) final indicator list, (5) tone variant A/B/C → v1.3 lock. Without these four-plus-one, do not start Cohort 1.
2. **Live 12-case regression not yet run on Gemini 2.5 Flash.** Develop-artifact §6 scores are predicted. The first live runs could surface period-grounding failures, E3 word-cap failures, or other model-specific surprises. Cohort 0 (5-day Arash dry run) is the mitigation; the live C2=5/5, C6=5/5, mean ≥4.0 gate must measure-pass, not predict-pass.
3. **PWA host and cron runner choice undecided** (Vercel / Netlify / Cloudflare Pages / self-host for PWA; GitHub Actions vs. launchd vs. cheap VPS for cron). Gates at-rest encryption posture, access-log retention, and the q50 sub-processor list. Must be locked before Cohort 0 starts.
4. **LLM-judge prompt is unwritten.** Without it, Layer 2 of the eval methodology doesn't exist; regression cadence collapses to 1-of-3 confidence levels. Must ship before first Friday review of launch week.
5. **PWA telemetry approach ambiguous.** q52 references either a tiny endpoint or local-storage read at weekly check-in to measure open rate and time-to-close. Endpoint adds infra; weekly self-report adds Bruce friction. Resolve during Cohort 0.
6. **Validator rounding edge cases underspecified.** O5 number-grounding logic is not explicitly tested against negatives, very small fractions, or basis-point conversions. TDD on these cases is recommended before the first live run.
7. **Banned-phrase list completeness.** O8 has 10 phrases; real advisory language is broader ("I'd lean toward," "leaning toward," "tactical," "overweight," "underweight"). Expand against actual Gemini outputs at the first quarterly retro.
8. **Mode 3 intra-day release gap.** If a release drops mid-day, Bruce sees yesterday's briefing until tomorrow's refresh. V1 accepted; V2 most-likely first ask.
9. **Alert fatigue risk.** ~12 alertable thresholds funnel to a single channel. At 1 call/day the rate is naturally low, but Cohort 0's first-week noise could desensitize Arash. Tune sensitivity after week 1.
10. **Judge-Bruce κ baseline takes ~8 weeks.** During that window, Bruce's direct ratings are the only ground truth; the Layer-2 judge is a co-witness, not a referee.

## Closing Note

The product is intentionally tiny. The rituals around it — the Friday review, the 14-day fix SLA, the regression-gated ship rule, the three-layer eval — are what keep it trustworthy. V1 ships when the four sign-offs land and the live regression measures C2 and C6 at 5/5 on 100% of items. Everything else is deliberately deferred so the wedge can be sharp on day one.
