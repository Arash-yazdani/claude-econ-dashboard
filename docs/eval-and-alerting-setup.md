# Eval & alerting — setup and operating guide

Everything in this doc is **free with no payment method on file** (SO9). Nothing here can bill:
Healthchecks.io's free tier is permanent, and LangSmith without a card is *rate-limited* at 5,000
traces/month rather than charged. We use ~150–200.

The code is already committed and dormant. It activates when the secrets below exist — every
shim no-ops silently until then, exactly like the Sentry shims (PRD §397: monitoring must never
break a run).

---

## 1 · Healthchecks.io — deterministic failure alerting

**Why this and not Sentry:** every dominant failure mode here is an *absence* — the cron never
fired, it self-skipped, it published a fallback, a source quietly degraded. Exception trackers
only see events that happen; they are structurally blind to "nothing happened." A dead-man's
switch is the only instrument that catches it. The charter already promises the CEO "a monitoring
service watches that the updates themselves happen."

### Create the account
1. Sign up at healthchecks.io (no card). Set the account email to **hello@modeo.io** — that is
   where every alert lands.
2. Create the checks below. For each, copy its **ping URL** into a GitHub Actions secret.

**All schedules are UTC.** GitHub Actions' `schedule:` cron is UTC-only with no timezone option,
so every expression below is written to match it. Picking a different timezone in the
healthchecks.io form would silently shift these hours and desync the checks from the workflow.

| Check name | Slug | Schedule (UTC) | Grace | Secret |
|---|---|---|---|---|
| `Daily Macro Brief` | `daily-macro-brief` | cron `25 13,21 * * *` | 5h | `HEALTHCHECKS_URL` |
| `Cron Worker` | `cron-worker` | **Simple**, period 1 day | 6h | Wrangler secret `HEALTHCHECKS_URL` |
| `Publish Quality` | `publish-quality` | cron `25 13,21 * * *` | 5h | `HEALTHCHECKS_URL_QUALITY` |
| `Data Sources` | `data-sources` | cron `25 13,21 * * *` | 5h | `HEALTHCHECKS_URL_SOURCES` |
| `Judge Daily` | `judge-daily` | cron `25 13,21 * * *` | 5h | `HEALTHCHECKS_URL_JUDGE` |
| `Weekly Regression` | `weekly-regression` | cron `0 12 * * 1` | 24h | `HEALTHCHECKS_URL_REGRESSION` |

> **Cron mode, not period mode**, and a wide grace — GitHub's scheduler habitually fires
> 1–2h late, and a period-mode check would page on every normal delay. This is the config the
> monitoring runbook already specifies, justified by the 2026-06-22 dropped-session incident.

> **The slot must sit BEFORE the earliest possible ping — this is the whole trick.**
> healthchecks.io cron mode re-arms on every ping: it sets the next deadline to the first slot
> *strictly after* the ping it just received. So a ping that lands **before** its own slot does not
> satisfy that slot — it arms it, and the session, having already published, never pings again.
> The check then goes DOWN *because the pipeline was punctual*.
>
> That is not hypothetical. The original spec was `15 14,21 * * *` / 4h. The AM run publishes at
> ~13:46 UTC (the Worker dispatches at 13:45), i.e. **29 minutes early**, so every on-time day armed
> a 14:15 slot and paged at 18:15 UTC. `Publish Quality` and `Data Sources` fired exactly that way
> on **2026-07-29** — both emails read `Last Ping Type: Success`, which is the signature of this bug.
>
> `25 13,21 * * *` puts both slots ahead of the earliest ping the system can produce (Worker 13:45
> → publish ~13:46; Worker 21:30 → publish ~21:32), so a ping always *closes* the current slot and
> arms the next one. It holds in EST too, when the primaries shift to 14:45 / 22:30 UTC — those land
> inside the 5h grace (deadlines 18:25 / 02:25 UTC) rather than before the following slot.
>
> **Never widen the grace to silence this class of alert.** Grace absorbs *lateness*; this failure is
> caused by *earliness*, and no grace value fixes it — the deadline it extends belongs to a slot the
> run already passed.

> **Why the quality/sources/judge checks carry a schedule too.** They could be "on demand"
> (ping-only) checks, but giving them the same cron as the briefing means they also catch
> *"this step silently stopped running"* — not just "it ran and reported a problem". That is
> exactly the failure that let the weekly regression go five weeks unnoticed.
>
> Note they are strictly *more* exposed to the earliness bug than `Daily Macro Brief` is:
> `report_run_health` pings them **only on a real publish**, once per session, whereas the de-dupe
> path (`run_daily.py:64`) pings `HEALTHCHECKS_URL` on *every* in-window firing. Those extra buffer
> pings land after 14:15 and were masking the mis-specified slot on the main check — so on
> 2026-07-29 the two per-publish checks paged and the briefing check did not. Do not read that
> asymmetry as "the briefing check is configured correctly"; it was one dropped buffer firing away
> from the same false DOWN.

> **AM/PM note:** `run_daily.py` pings a single `HEALTHCHECKS_URL`, so one check with cron
> `25 13,21 * * *` covers both sessions. Use two separate checks only if you want to know
> *which* session missed — that needs a small change to pass a per-session URL.

> **Reading a `Weekly Regression` alert — FAIL vs INCONCLUSIVE.** `run_judge.py` reports a
> three-way verdict, and the distinction is the difference between "stop, the prompt regressed"
> and "we learned nothing, run it again":
>
> | Verdict | Means | Ping | Job | What to do |
> |---|---|---|---|---|
> | `PASS ✅` | all 12 measured, all clean | success | green | nothing |
> | `FAIL ❌` | a quality failure **provable** from the cases that ran | `/fail` | red | investigate the prompt |
> | `INCONCLUSIVE ⚠` | nothing failed, but <12 cases could be measured | `/fail` | red | re-run on fresh quota |
>
> Both non-PASS verdicts ping `/fail`, go red, and email — an uncertified prompt must never read
> as green, and Runbook 6 blocks a ship on either. Only the wording differs, which is the entire
> point: the old code called a free-tier 429 a prompt failure, and under SO9 (no card, ever)
> throttling is permanent, so that wording would have taught you to ignore this channel. A
> provable quality failure **outranks** incomplete coverage, so a real regression can never hide
> behind a rate limit (`judge.summarize`, `tests/test_judge.py`).

> **Why `Cron Worker` is the one Simple-mode check.** The Worker's own crons are
> `45 13 * * *`, `45 14 * * *`, `30 21 * * *`, `30 22 * * *` — four fires a day at irregular
> minutes that no single cron expression matches cleanly. All this check needs to answer is
> *"is the Worker alive at all"*, and any one of the four pings proves that. A 1-day period
> with 6h grace does it without pretending to mirror four fire times. A partial failure (AM
> dispatches die, PM still works) is already caught by the briefing's own dead-man switch,
> which sees the missing publish.

### Set the secrets
```bash
gh secret set HEALTHCHECKS_URL              # the AM/PM dead-man switch
gh secret set HEALTHCHECKS_URL_QUALITY      # fires when a fallback/refusal is published
gh secret set HEALTHCHECKS_URL_SOURCES      # fires when a vendor degrades or a row blanks
gh secret set HEALTHCHECKS_URL_JUDGE        # fires when the daily eval drops below threshold
gh secret set HEALTHCHECKS_URL_REGRESSION   # fires when the weekly q26 ship gate fails, cannot be measured, or stops running
```
And for the Cloudflare Worker (this one closes the **biggest** blind spot — a silently expired
GitHub PAT killing the primary scheduler):
```bash
cd cron-worker && wrangler secret put HEALTHCHECKS_URL
```

### What each check actually catches
- **`brief-am` / `brief-pm`** — the run never published at all. The only signal that survives a
  total outage, because nothing runs to report it.
- **`cron-worker`** — the Worker stopped dispatching (expired/revoked PAT). Previously this
  failed to `console.error` only, visible solely to someone actively running `wrangler tail`.
- **`publish-quality`** — a *fallback or refusal* was published. This exits 0 and pings the
  dead-man switch green, so without its own check a week of template cards looks perfectly
  healthy. (The existing RED gate only trips above a **50%** fallback rate over 30 days.)
- **`data-sources`** — Treasury tip missing, gold-api down, an indicator blanked by the
  plausibility guard, or a core indicator stale. All of these were completely silent before.
- **`judge-daily`** — the daily 3-dimension eval fell below its pass threshold.

---

## 2 · LangSmith — quality observability

Free Developer tier: 1 seat, 5,000 base traces/month, **14-day trace retention**.

```bash
gh secret set LANGSMITH_API_KEY
```
`LANGSMITH_PROJECT` defaults to `macro-daily-briefing`.

**We deliberately do NOT use LangSmith's online evaluators.** They auto-upgrade every trace they
touch to extended (400-day) retention, which is billable — a direct SO9 breach. Instead the judge
runs in our own CI on the free Gemini quota and pushes its scores here as **feedback**, which is
free. Same data, no card.

**The 14-day retention problem, solved:** each published briefing is also promoted into the
persistent LangSmith **Dataset** `macro-daily-briefings`. Dataset examples are not subject to
trace retention, so the D+30 review still has its material. (Belt-and-braces: `briefing.json`,
`indicators.json`, `runs.jsonl` and `daily_eval.csv` are in git forever anyway.)

**Bruce has no seat** — the free tier allows one, and a second is $39/mo *and* needs a card.
Arash holds the seat; Bruce reviews a one-page sample sheet as he already does on Fridays, and
Arash enters the scores into the `human_c1` / `human_c3` / `human_note` columns of
`logs/daily_eval.csv` (and as LangSmith feedback). **This is what finally unblocks Cohen's κ** —
`dashboard.aggregate_judge` reports `kappa: pending` today purely because no human-label column
existed.

---

## 3 · The three eval dimensions

Deterministic-first by design. Two of the three are better served by exact checks than by an LLM,
and the locked judge prompt refuses to score numbers on purpose ("double-judging them would muddy
the κ calibration").

| Dim | What it measures | Checks |
|---|---|---|
| **D1** Brief quality | genuinely subjective → LLM judge | `D1_c1_quotability`, `D1_c3_tone`, `D1_c4_conditionals`, `D1_c5_length_format`, `D1_no_below_3` |
| **D2** Regime accuracy | rules-only; the LLM never picks the regime | `R1_regime_valid` (daily) / `R1_regime_reproducible` (in-pipeline), `R2_regime_narration` |
| **D3** Indicator accuracy | deterministic; O5 already at 100% | `I1_o5_grounding`, `I3_no_banned_phrases`, `I4_direction_framing`, `I2_source_reconciliation` |

Two of these are **new signals nothing had before**:
- **`R2_regime_narration`** — the briefing is handed a regime label and told to narrate it, but no
  check ever compared the prose against it. R2 fails if the text names a *different* regime.
- **`I2_source_reconciliation`** — re-fetches every indicator and compares it to what we published
  **at the same observation date**. O5 only proves the briefing quotes `indicators.json` faithfully;
  it can't tell you `indicators.json` itself is right. Matching by date matters: the source gains
  newer observations between publish and check, and comparing against "newest now" flags every
  healthy run (this bit on the first live run — `treasury_10y` published 4.69 @07-24 vs a fresh
  4.65 @07-27, which was correct all along).
- **`I4_direction_framing`** — O5 grounds the number but never the verb, so "unemployment rose to
  4.2 percent" passes O5 even when the delta is negative. I4 closes that.

Run it manually:
```bash
python scripts/run_daily_eval.py --dry-run              # score, print, change nothing
python scripts/run_daily_eval.py --dry-run --reconcile  # + re-verify all 28 against source
```

---

## 4 · Annotation reviews at D+1, D+7, D+30

| Review | Scope | Purpose |
|---|---|---|
| **D+1** | every briefing from day 1 | catch anything structurally wrong immediately |
| **D+7** | all ~14 briefings | the Cohort-1 style read; first real pass-rate number |
| **D+30** | ~15 sampled from ~60 | drift check; κ calculation |

Process per review:
1. Open the `macro-daily-briefings` dataset in LangSmith → add the examples to an annotation queue.
2. Score each against the 12 CTQ criteria below.
3. Send Bruce the one-page sheet; transcribe his C1/C3 ratings into `logs/daily_eval.csv`.
4. Recompute the pooled pass rate.

---

## 5 · Bruce's CTQ rubric and the ship decision

Grounded in his own acceptance criteria (PRD §37: *"scannable in <10s, credible at conversation
tempo, one synthesized read he can lead with, zero ongoing cost, and stays in its lane"*).

| # | Criterion | Source of truth |
|---|---|---|
| 1 | Scannable in <10s (≤80 words, headline ≤22) | `length` |
| 2 | Quotable aloud | C1 ≥4 |
| 3 | Confident, no hedging | C3 ≥4 |
| 4 | **Every number grounded** | O5 = 100% · **non-negotiable** |
| 5 | **Stays in its lane (no advice)** | O8 = 0 escapes · **non-negotiable** |
| 6 | **Refusal envelope correct** | O16 = 100% · **non-negotiable** |
| 7 | Conditionals fire iff triggered | C4 ≥4 + O4 |
| 8 | One synthesized read | `R2_regime_narration` |
| 9 | Regime defensible | `R1` + Bruce disagreement log |
| 10 | Indicator values true to source | `I2_source_reconciliation` |
| 11 | Published on time | dead-man's switch window |
| 12 | **Zero cost, no fallback** | `source == "llm"`, cost $0 · **non-negotiable** |

**Scoring:** pooled — pass rate = passed checks ÷ (12 × N briefings).

- **≥90% → ship to Bruce's phone**, then keep optimizing toward **≥99%**.
- **Override:** criteria 4, 5, 6 and 12 must be **100%**, not 90%. A pooled 90% must never let a
  banned-phrase escape ship. This matches PRD §267/§363, where those four are the only hard stops
  among the 16 KPIs.

---

## Open items for you

1. **Prompt v1.4.2 is live with no *certified* ship-gate run and no Bruce ack.** Runbook 6 says any
   `prompt.py` edit re-opens SO8 and needs a fresh regression + ack.
   The 2026-07-29 22:44 UTC manual run logged 12 v1.4.2 rows but came back **INCONCLUSIVE**: only
   8/12 cases measured (`E2 E3 A1 A2` hit the free-tier ceiling), so it does not certify. The 8 that
   ran were clean — means 4.88/4.88/5.0/4.88, zero below-3 — so there is no evidence of a
   regression, only a measurement gap. **Still open until a complete run.** Do not re-run it late in
   the day: that attempt drained an RPD budget already spent by ~9 briefings + their evals. The
   Monday 12:00 UTC slot exists precisely for this (04:00 PDT — after Gemini's midnight-PT reset,
   before the 13:45 UTC AM briefing).
2. ~~**The ship gate exists in four non-identical forms.**~~ **RESOLVED 2026-08-04 — two tiers,
   canonical in `docs/eval/regression-cases-v1.0.md` §Usage & gate.** Two of the four "forms"
   collapsed on inspection: "≥11/12 on Gemini" and "no criterion <3 on >10% of items" are the same
   rule stated twice (11/12 = 91.7%), and checklist #20 is a record of the V1 run's result, not a
   threshold. The real distinction is by event, and both now live in one function:
   **standing gate** = ≤10% below-3 (`run_judge.py`); **prompt-change gate** = 0 below-3
   (`run_judge.py --strict`), because editing `prompt.py` re-opens SO8.
3. **`prompt_history.csv` does not exist**, though PRD §339 and Runbook 6 step 3 both require it.
4. ~~**The publish-time target is ambiguous** — the charter says 9:45 AM, the Bruce-facing FAQ says
   ~10:15, and sign-off item #5 records the morning run as ~30 min late. CTQ-11 needs one number.~~
   **RESOLVED 2026-08-12 — the number is 10:45 ET** (ADR-001 v1.2). Set by FRED's ~100-minute
   ingest lag on the 08:30 ET releases, not by preference: anything earlier serves last month's
   CPI on release morning. FAQ, runbooks, monitoring docs and both schedulers now agree.
   **Still open:** the client Charter (all three revisions) states 9:45 AM and is now wrong by an
   hour. It is a signed .docx and needs a Rev 4 or an erratum, which is a client-facing action.
