# V4 — Operational Runbooks

**Status:** ✅ **8 runbooks** (7 here + the Bruce FAQ in [`FAQ-bruce.md`](FAQ-bruce.md)) · **Owner:** Arash · **Jira:** [SCRUM-38](https://modeo-team-1.atlassian.net/browse/SCRUM-38)

Operating manual for the daily-briefing system. Each runbook is **trigger → detect → act →
recover → escalate**. The system's prime directive: **never surface a failure to Bruce** — the
PWA degrades (stale footer, fallback card) but always renders.

## Thresholds at a glance (PRD warn / hard-stop)

| Signal | Target | Warn | Hard-stop / action |
|---|---|---|---|
| Gemini quota (RPD/TPM) | — | 50% | 80% → expect Groq failover |
| FRED calls/day | ≤25 | — | 1,000 |
| LLM latency p95 | ≤3s | — | >5s for **5 days** → make Groq primary |
| Validator pass rate (C2/O5, C6/O16) | 100% | — | **any failure → publication pause** |
| Deterministic-fallback rate | 0 | — | **≥2/week → block deploys** |
| O8 banned-phrase escapes | 0 lifetime | — | **any escape → immediate prompt rollback** |
| Cost | $0 | $0.10 | $1.00 (cannot happen — no card on file) |
| Judge–Bruce Cohen's κ | ≥0.6 by wk 8 | — | <0.6 → re-prompt the judge |

Error-triggered rules (PRD): validator second-failure → **same-day incident**; LLM p95 >5s for 5
days → Groq primary; banned-phrase escape → **immediate rollback**; κ <0.6 → re-prompt judge;
free-tier deprecation → **Bruce paid-budget decision within 48h**. File incidents in
[`docs/incidents/`](incidents/README.md).

---

## 1 · Daily cron

**What "normal" looks like.** GitHub Actions runs **twice daily** (10:45 ET "AM" + 17:30 ET "PM",
via four DST-safe UTC crons; `run_daily.py` self-skips the off-window pair). Each run: FRED →
classifier → Gemini (+Groq failover) → 17-check validator → `public/{briefing,indicators}.json`
→ commit → **Cloudflare auto-deploys**. One audited row lands in `logs/runs.jsonl`; the dashboard
regenerates (`docs/monitoring.md`).

- **Detect health:** healthchecks.io ping (start/success/fail) per run; GitHub Actions run history;
  the freshness footer on the live PWA ("Updated … ET").
- **Manual run / re-run:** `gh workflow run daily-briefing -R Arash-yazdani/Economy-Dashboard -f run_label=AM` then `gh run watch -R …`.
- **Read a run:** `gh run view <id> -R … --log`; the audit row is the last line of `logs/runs.jsonl`.
- **Commit must NOT contain `[skip ci]`/`[CI Skip]`** — Cloudflare Pages skips those deploys (see `docs/cron-b6.md`).
- **Escalate:** a missed success ping → healthchecks.io alerts; GitHub auto-disables a workflow after
  ~60 days of repo inactivity (daily commits keep it alive).

## 2 · Validator failure (fallback rendered)

**Trigger:** a run publishes the **deterministic template** instead of an LLM briefing
(`source=fallback` with `validator_status=invalid`), or the dashboard shows an O-check < 100%.

- **Detect:** `build_monitoring.py` prints `STATUS: RED` and **exits 3** → the cron's red-gate step
  **pauses publish** (skips the commit; PWA keeps last-good). Dashboard red widget flags **O5 (=C2)**
  or **O8** below 100%.
- **Act:** the bad briefing was **never published** (fallback is safe). Pull the offending run from
  `logs/runs.jsonl` (filter on `validator_failed`) and read its `output`.
- **Diagnose:** O5 → a fabricated/ungrounded number (worst case); O8 → an advice-y/banned phrase;
  period/shape → malformed output. Reproduce with `scripts/run_judge.py` on the affected case.
- **Recover:** fix root cause (usually a prompt issue → Runbook 6). Re-green the 12-case regression
  (C2=5/5, C6=5/5) before re-enabling the cron.
- **Escalate:** validator **second-failure → same-day incident** (file in `docs/incidents/`).
  **≥2 fallbacks/week blocks deploys.** Any **O8 escape that reached publish → immediate prompt rollback.**

## 3 · LLM failover (Gemini → Groq) & quota

**Trigger:** Gemini errors, 429 (quota), or slow (p95 >5s).

- **Detect:** `logs/runs.jsonl` `model=groq-llama-4-scout` instead of `gemini-2.5-flash`; dashboard
  p95 latency; quota widget.
- **Behavior (automatic):** `generate_briefing` fails over Gemini→Groq on **any** error; `produce_briefing`
  retries once then renders the template. A failover day still publishes — nothing to do live.
- **Gemini free-tier quota** resets at **~midnight Pacific**. Heavy same-day testing can exhaust the
  RPD (seen during V1 — the run fell to Groq; re-running after reset passed clean). Warn at 50%, expect
  failover by 80%.
- **Act (sustained):** if **LLM p95 >5s for 5 straight days**, promote Groq to primary (swap order in
  the `run_daily`/`produce_briefing` wiring) and re-regress.
- **Escalate:** both providers down → template fallback publishes (safe). Free-tier **deprecation** of a
  provider → Bruce paid-budget decision **within 48h** (the no-card stance means a provider can only 429).

## 4 · FRED outage

**Trigger:** the FRED API is unreachable or returns errors.

- **Detect:** `run_daily.py` exits **1** with a healthcheck **fail** ping; **no new briefing commit**;
  `logs/runs.jsonl` gets an `error` row (`source=error`).
- **Behavior (automatic):** `orchestrate` catches `FredError` → `published=False` → **`briefing.json`
  is NOT overwritten.** The PWA keeps serving the **last-good** card; its freshness footer ages and
  flips to the stale state after 18h.
- **Act:** usually **none short-term** — last-good + the stale banner is the designed degradation. FRED
  outages are typically brief. If prolonged (>1 day), note it; consider a manual heads-up to Bruce.
- **Recover:** the next scheduled cron picks up automatically once FRED returns.
- **Budget:** FRED target ≤25 calls/day (we use ~14/run); hard-stop 1,000/day — not reachable at 2 runs/day.

## 5 · Bruce disagrees

**Trigger:** Bruce dislikes a briefing — stiff/odd tone, a number read he'd phrase differently, or a
**regime call** he disputes.

- **Capture:** log it (date, case, what he said) — this is ground truth until judge–Bruce **κ ≥0.6**
  (wk 8). Bruce's weekly 10-min Friday review (wks 1–4, then monthly) is the main intake.
- **Triage by type:**
  - **Wrong/ungrounded number** → a validator gap → Runbook 2 + same-day incident.
  - **Tone / phrasing** → Runbook 6 (prompt change), tie the new version to the named complaint.
  - **Regime he disputes** → the regime is **deterministic** (the `classifier.py` rules, D3-signed),
    **NOT** the LLM — do **not** "fix" it in the prompt. If the rule is genuinely wrong, that's a
    threshold review (D9-style, re-opens SO8) with a back-test, not a prompt edit.
- **Escalate:** a pattern of disagreement on the same theme → a prompt version (Runbook 6) or a
  threshold change; feed the monthly retro (theme aggregation + κ recalc).

## 6 · Prompt change

**Trigger:** any change to the locked production prompt (`prompt.py`) — a tone tweak, a new rule, a
fix for a named complaint.

The prompt is **D5-locked + Bruce-acked**; editing it **re-opens SO8** and needs a fresh Bruce ack.
**Never ship an unregressed prompt.** Process (this is exactly the v1.3 → v1.3.1 flow):
1. Branch; edit `prompt.py`, **bump `PROMPT_VERSION`** (e.g. 1.3.1 → 1.4).
2. Run **L1 + L2** regression: `python scripts/run_judge.py --strict` — must **measure-pass** the
   PROMPT-CHANGE tier of the ship gate: C2=5/5, C6=5/5, judge means ≥4.0, **0 below-3**, every
   fixture assertion clean, all 12 measured. `--strict` is what selects this tier; without it you
   get the standing ≤10% rule, which is **not** sufficient to re-ack SO8.
   Use a **clean Gemini** path — run early in the UTC day, before the briefings spend the free-tier
   RPD. A rate-limited run reports INCONCLUSIVE, not FAIL: it does not certify, and it is not a
   regression either. Canonical gate definition: `docs/eval/regression-cases-v1.0.md` §Usage & gate.
   - If the run reports a **judge/validator conflict**, stop and read it before touching the prompt.
     It means the deterministic O4 check passed a briefing the LLM judge failed on C4 — suspect the
     judge, not the writer, and check the `output` column in `eval_log.csv`. (2026-08-03, case T4.)
3. Get **Bruce's ack**; capture as an ADR-style commit; append to `prompt_history.csv`.
4. Deploy (push); the cron picks up the new version on its next run.
- **Rollback:** for a banned-phrase escape or a regression miss, revert to the prior locked version
  immediately and re-regress before trying again.

## 7 · Quarterly tone review

**Trigger:** the scheduled quarterly tone check, or a κ drift / voice-calibration signal.

- **Do:** Bruce reviews a sample of recent briefings for voice ("still sounds like a prepared friend?").
  The monthly retro (45 min, Bruce 15) already covers theme aggregation, κ recalculation, fallback rate,
  and threshold drift.
- **If drift:** propose a tone variant → **Runbook 6** (regression + Bruce ack + ADR commit). Healthy
  6-month cadence is ~4–8 prompt versions, each tied to a named complaint; regression set grows 12 → ~18.
- **Judge instability:** rising per-case score variance on **unchanged** regression cases (dashboard §401)
  → re-prompt the judge (re-lock `judge.py`'s prompt, re-run the regression). κ <0.6 → re-prompt judge.

## 7b · Bruce annotation round (D+1 / D+7 / D+30)

**Trigger:** the scheduled Layer-3 rounds (SCRUM-85), or any κ recalculation.

```bash
python scripts/build_review_sheet.py --round d1     # 2 briefings; d7 -> 4, d30 -> 6
```

- **Do:** print the sheet, hand it to Bruce, transcribe his two numbers and note into the
  `human_c1` / `human_c3` / `human_note` columns of `logs/daily_eval.csv`.
- **The ask is deliberately two numbers and a sentence** (PRD §269) — the minimum that yields a
  κ. He is the customer, not an evaluator; every extra field is a reason the round does not come
  back. Arash scores the 12 CTQ criteria separately, nine of which auto-fill.
- **Never show him the judge's scores**, the regime label, or the machine verdict. The sheet omits
  all three by construction. Showing them anchors him to the model's answer and destroys the very
  agreement statistic the round exists to produce.
- **Sample sizes are front-loaded 2 / 4 / 6** (his call, 2026-08-04): a badly-calibrated judge
  surfaces on day one when it is cheap, not after a month of misaligned scores.
- **The sampler prefers distinct headlines.** 94 of the first 104 briefings shared one headline, so
  a naive sample hands him the same sentence twice and two ratings come back as one. 12 paired
  items is already thin for κ; 12 duplicates measure nothing.
- **κ at D+30 is provisional.** It stabilises around 20+ paired items; the PRD targets κ ≥ 0.6 at
  **week 8**. Report the D+30 figure as provisional or it will be over-read.
- **Retention:** LangSmith traces expire at 14 days, so promote each briefing to a Dataset before
  the D+30 round or it will have nothing to read. `logs/runs.jsonl` in git is the durable copy and
  is what the sheet actually reads.

## 8 · Bruce FAQ

User-facing — see **[`docs/FAQ-bruce.md`](FAQ-bruce.md)**. Covers: what the dashboard is, when it
updates, why a number/footer looks the way it does, the fallback card, the **not-investment-advice**
boundary, install instructions, and how to give feedback (→ Arash).


## 9 · Data-accuracy report (Bruce's request, D+1 2026-08-06)

**Trigger:** Bruce asks whether he can trust the numbers; the monthly Runbook 7 retro; or any
`Data Sources` mismatch alert worth explaining to him afterward.

```bash
python scripts/build_accuracy_report.py --format email   # plain text, paste into the email body
python scripts/build_accuracy_report.py                  # printable HTML
```

- **Shape is deliberate:** a controls-testing memo — verdict first, population, exceptions table,
  scope limitations — his native format from thirty years of process engagements. Not the operator
  dashboard (`docs/monitoring.md`), which speaks O-checks and κ.
- **Source of truth:** `logs/reconciliation.jsonl`, written by every PM eval (`--reconcile`).
  The report never re-fetches; it reports the verification that already ran.
- **The three scope caveats are verbatim and test-enforced** (`tests/test_accuracy_report.py`) —
  deleting one fails the build. A report that quietly drops its limitations is the exact
  overstatement the reader is trained to distrust.
- **Cadence:** on demand + monthly with the retro. NOT daily — a daily "all 26 matched" is an
  email he stops reading, and then the one that matters arrives pre-ignored.
- **On a mismatch:** the same evening's `Data Sources` alert already paged; the report shows the
  row with both values. Fix or mark stale before the next morning's briefing, then send the
  report showing the exception honestly — never a silent carry-forward.
