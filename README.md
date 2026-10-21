# Claude Econ Dashboard

Derived from **Economy-Dashboard** (GitHub `Arash-yazdani/Economy-Dashboard`, live at
macro.modeo.io). Same pipeline, with the LLM layer moved from Gemini 2.5 Flash + Groq to **Claude on
Amazon Bedrock**. The original stays in production as the baseline; `scripts/compare_models.py`
replays the same requests through both stacks and scores them with the same validator and judge.

Decision record: [docs/adr/ADR-007-claude-on-bedrock-writer.md](docs/adr/ADR-007-claude-on-bedrock-writer.md).

| Role | Model (Bedrock id) | Env override | Effort |
|------|--------------------|--------------|--------|
| Briefing writer (primary) | `anthropic.claude-haiku-5-5` | `CLAUDE_PRIMARY_MODEL` | low |
| Writer failover | `anthropic.claude-sonnet-5-5` | `CLAUDE_FAILOVER_MODEL` | low |
| Judge failover | `anthropic.claude-haiku-5-5` | `CLAUDE_JUDGE_FAILOVER_MODEL` | medium |
| Craft judge, persona judge | `anthropic.claude-sonnet-5-5` | `CLAUDE_JUDGE_MODEL` | medium |

Unchanged and deterministic: FRED ingest, the rules-only regime classifier (the model does not pick
the regime), the prompt (v1.5), the B4 validator and its fallback card, the audit log.

## Before anything runs

**Nothing is scheduled.** Every `schedule:` block in `.github/workflows/` is commented out, and the
Cloudflare Worker's `[triggers] crons` in `cron-worker/wrangler.toml` is commented out. Nothing
fires until AWS and GitHub secrets are wired and those blocks are restored.

1. AWS account with Bedrock model access to the three models above in your chosen region.
2. IAM principal scoped to invoking those models on Bedrock (see ADR-007, "Access").
3. GitHub repo for this copy (the Worker targets `Arash-yazdani/claude-econ-dashboard`).
4. GitHub Actions secrets: `AWS_REGION`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, plus the
   existing `FRED_API_KEY`, optional source keys, healthcheck URLs and `ALERT_SLACK_WEBHOOK`.
   Use new healthchecks for this repo; pinging the original's checks would mix the two signals.
5. Cloudflare Pages project serving `public/` on its own subdomain. `public/index.html` and
   `public/methodology.html` still carry `macro.modeo.io` canonical/OG URLs; change them to the new
   subdomain. The OG PNGs still read "Economy Dashboard"; re-render them from the updated SVGs.
6. Then uncomment the schedules and `wrangler deploy` the Worker (CLAUDE.md: editing the workflow
   schedule alone is inert while the Worker dispatches).

## Environment variables

Real environment wins over a local env file (`macro_pipeline._env`). Names only:

| Variable | Needed for | Absent means |
|----------|-----------|--------------|
| `AWS_REGION` | every Claude call | clear `BriefingLLMError`; the run publishes the deterministic fallback card. No default region. |
| `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` | Claude calls (or any standard boto credential source) | the call fails, failover fails, fallback card |
| `CLAUDE_PRIMARY_MODEL`, `CLAUDE_FAILOVER_MODEL`, `CLAUDE_JUDGE_MODEL` | optional overrides | defaults in `llm_client.py` |
| `GEMINI_API_KEY`, `GROQ_API_KEY` | `scripts/compare_models.py --live` only | compare refuses to run |
| `FRED_API_KEY`, `BLS_API_KEY`, `GOLD_API_KEY`, `EIA_API_KEY`, `BEA_API_KEY`, `CENSUS_API_KEY` | data sources, unchanged | as in the original |
| `HEALTHCHECKS_URL*`, `HEALTHCHECKS_RO_KEY`, `ALERT_SLACK_WEBHOOK`, `LANGSMITH_*` | monitoring, unchanged | that signal is a silent no-op |

`.env.example` is still the original's copy (it lists Gemini/Groq); a local hook blocks editing it
from the agent session. Update it to the table above by hand.

## Commands

```bash
.venv/bin/python -m pytest -q                        # offline suite, no keys, no AWS
.venv/bin/python scripts/compare_models.py           # dry run: request count + token estimate, no calls
.venv/bin/python scripts/compare_models.py --live    # both stacks, writes docs/comparison.md (spends money)
.venv/bin/python scripts/probe_models.py             # Bedrock liveness: one count_tokens per model
.venv/bin/python scripts/run_briefing.py             # one live Claude briefing end to end
```

Every other command in [CLAUDE.md](CLAUDE.md) works as in the original.

---

## Original README (Economy-Dashboard, copied 2026-10-20)

Kept verbatim below as the baseline's description. Where it says Gemini, Groq, $0 budget, 424 tests
or macro.modeo.io, it describes the original, not this repo.

### Macroeconomy Dashboard — Project Workspace

**Product:** a personal, $0-budget U.S.-macro PWA whose one AI feature is the **Daily Conversation Briefing** — one LLM call per run, twice a day, that turns fresh indicators from 13 keyless-or-free sources (FRED backbone, grafted Treasury, BLS, NY Fed, CBOE, BEA, EIA, Census, FiscalData, gold spot) into one quotable headline + two quotable numbers, rendered as a card atop an Apple-Stocks-style dashboard.
**Owner/Builder:** Arash Yazdani · **Sole user (N=1):** Bruce · **Budget:** $0/yr (free-tier APIs)
**Kickoff:** Mon Jun 15, 2026 · **Live in production since:** Thu Jun 18, 2026

> **Live now.** The build is done and the product is in steady-state operation. This README is the
> planning workspace. For working on the code, read [CLAUDE.md](CLAUDE.md); for today's health, read
> [docs/monitoring.md](docs/monitoring.md), which regenerates on every run.

---

## What's in this folder

### The running product

| Path | What it is |
|------|-----------|
| [CLAUDE.md](CLAUDE.md) | **Start here to work on the code.** Operating guide: commands, architecture, the locks, how to scale it, the traps. |
| `src/macro_pipeline/` | The pipeline package: FRED ingest, regime classifier, prompt, LLM clients, validator, observability. |
| `scripts/` | Entrypoints: the cron run, the eval, the ship gate, the back-test, the report builders. |
| `public/` | The deployed PWA plus the two published data files the cron commits. |
| `tests/` | The offline suite (424 tests in the original, no network, no keys, ~0.3s) plus `tests/js/` for the widget formatting drift check. |
| `schemas/` | Machine-readable contracts for the LLM boundary, the card, and the run record. |
| `cron-worker/` | The Cloudflare Worker that is the primary scheduler. Also warns 14 days before its GitHub PAT expires. |
| `.github/workflows/` | `daily-briefing` (the publish run), `healthcheck-audit` (every 6h, P0/P1/P2 Slack alerts), `weekly-regression` (prompt gate + model liveness probe), `monthly-backtest`, `ci` (tests on code pushes). |
| `constraints.txt` | Pinned dependency versions every workflow installs with `pip install -c`. Refresh with `pip freeze --exclude-editable`. |
| `tools/macro-widget.js` | The Scriptable iOS home-screen widget (the second consumer of the feed). |
| `docs/` | ADRs, runbooks, incidents, eval specs, and the generated monitoring dashboard. |
| `.env.example` | Every environment variable the pipeline reads, and what happens when each is absent. |

### The planning artifacts

| File | Purpose | Open it in |
|------|---------|-----------|
| [PRD.md](PRD.md) | The product requirements (q1–q57). The *what* and *why*. Source of truth for intent. | Any markdown viewer |
| [Product Plan.md](Product%20Plan.md) | The execution roadmap — Design → Develop → Deploy → Operate (+ V2). Phases, gates, owners, dates, risks, KPIs. | Any markdown viewer |
| [Gantt chart.md](Gantt%20chart.md) | Visual timeline — a Mermaid gantt + a plain-text schedule table. | GitHub / VS Code / Obsidian / Notion (Mermaid) |
| `Project Tracker.xlsx` | The live tracker — 6 sheets you update day-to-day. | Excel (recommended), Numbers, Google Sheets |

The three planning artifacts share **identical task IDs, dates, gates, and owners** — they are one plan in three views. Change a date in one, change it in all three.

They describe the plan as written before the build, and several of their specifics have been
superseded by shipped decisions (the prompt is on v1.5, not v1.3; the indicator set is 26, not 7;
the cron runs twice daily at 10:45 and 17:30 ET). Where a planning artifact and the code disagree,
the code and the ADRs in `docs/adr/` win.

---

## How to use them

1. **Read once:** `Product Plan.md` (the narrative) → `Gantt chart.md` (the picture).
2. **Drive daily work from `Project Tracker.xlsx` → `Task Tracker` sheet.** Set each row's **Status** via the dropdown (`Not Started / In Progress / Blocked / Done`) — cells auto-color.
3. **At each gate (G0–G4)**, update the `Milestones & Gates` sheet with the actual date and pass/fail.
4. **Log every Bruce sign-off** in the `Sign-off Tracker` sheet (status, date, and the ADR-style commit hash as evidence — per the PRD's audit rule).
5. **Record measurements** in the `KPI Dashboard` sheet's `Current` column as the live regression and cohorts produce them.

### The Excel workbook — 6 sheets
- **Overview** — project meta, status legend, gate quick-view, and an as-of status snapshot.
- **Task Tracker** — all 46 line items (D/B/V/C/O), with start/end/duration/dependency/status.
- **Milestones & Gates** — G0–G4 + steady-state milestones, each with exit criteria.
- **Sign-off Tracker** — the 5 design + 4 compliance sign-offs (launch-blocking).
- **Risk & Open-Questions** — the PRD's 10 open questions + 2 schedule risks + the q47 hard-stops.
- **KPI Dashboard** — PRD success metrics; the three non-negotiables (C2, C6, O8) flagged red.

---

## Roadmap: how it went

```
G0 Design Lock → G1 Build Complete → G2 Launch-Ready (2026-06-18)
→ live twice-daily publishing from 2026-06-18 → V2 / V2.1 / V2.2 / V3 indicator expansion → Ops
```

| Gate | Planned | Means | Outcome |
|------|---------|-------|---------|
| G0 | Jun 18 | 5 sign-offs + host decision + prompt locked | Passed. Host + cron in [ADR-001](docs/adr/ADR-001-host-and-cron-runner.md). Prompt locked at v1.3, now v1.5. |
| G1 | Jun 24 | Pipeline runs end-to-end on a real device | Passed. First audited production run 2026-06-18. |
| G2 | Jun 26 | Live regression green (C2 & C6 = 5/5) + 4 compliance sign-offs | Passed 2026-06-18: [launch-readiness checklist](docs/launch-readiness-checklist.md) 22/23, sign-offs in [ADR-002](docs/adr/ADR-002-compliance-signoffs.md). |
| G3 / G4 | Jul 2 / Jul 17 | Cohort 0 then Cohort 1 (Bruce) to steady state | Cohort evidence is in `Project Tracker.xlsx` and `docs/feedback/`, not in this repo's data files. |

Everything after G2 has been operational work rather than build work: the indicator set grew from 7
to 26 across V2/V3, the classifier reached locked v1.2, the prompt reached v1.5, and the eval and
alerting layers were built out. The decisions are in `docs/adr/`; the failures are in
`docs/incidents/`.

---

## Current status

**Live and publishing twice daily.** The only status numbers worth trusting are the generated ones,
because a hand-typed snapshot in this file is exactly what went stale for two months here before.

| Where | What it tells you | Freshness |
|-------|-------------------|-----------|
| [docs/monitoring.md](docs/monitoring.md) | RED/GREEN, validator pass rate per O-check, latency, fallback rate, quota, judge stability | Regenerated by `scripts/build_monitoring.py` on every run |
| `logs/runs.jsonl` | One audit row per run since 2026-06-18, indefinite retention (PRD §339) | Appended and committed every run |
| `prompt_history.csv` | The production prompt version and its certification evidence | Appended on every certified prompt change |
| `logs/reconciliation.jsonl` | Every published number re-checked against its source on the PM run | Appended on every PM eval |

To print the live figures rather than read a number someone typed:

```bash
python3 -c "import json,collections; r=[json.loads(l) for l in open('logs/runs.jsonl') if l.strip()]; print(len(r),'runs |',r[0]['timestamp'][:10],'->',r[-1]['timestamp'][:10],'|',dict(collections.Counter(x['source'] for x in r)))"
```

As of 2026-09-07 that reads 177 runs across 83 consecutive publishing days with no missed day,
171 live LLM briefings, 3 fallbacks, and 3 FRED-outage errors (which never overwrite last-good).
The validator has rejected an LLM output twice (2026-08-25 PM, 2026-09-02 AM); both runs published
the deterministic fallback card, so no rejected text ever reached a reader.

**Open items** (the honest ones, verified against the repo):
1. **Judge-vs-human agreement (κ) is unmeasured.** The `human_c1` / `human_c3` / `human_note`
   columns in `logs/daily_eval.csv` are empty. Judge quality is machine-measured only, and
   `docs/monitoring.md` says so. Runbook 7b is the mechanism to close this.
2. **Several planning and design documents predate shipped decisions.** CLAUDE.md keeps the list of
   which ones are known to be wrong and how.

---

## Monitoring

The live ops dashboard is **`docs/monitoring.md`** (auto-generated by
`scripts/build_monitoring.py`): last-30-days validator pass rate per O-check, p50/p95 latency, fallback rate, quota, and judge
stability. 🔴 RED (the cron refuses to publish) only when the fallback rate exceeds 50%; a critical
check (O5/O8) below 100% is 🟡 AMBER, which alerts and keeps publishing because a caught check means
the validator did its job (changed 2026-08-26 after a RED latch froze the dashboard for two days). The static operator guide is [`docs/monitoring-runbook.md`](docs/monitoring-runbook.md);
incidents live in [`docs/incidents/`](docs/incidents/README.md) (PRD §339).

The twice-daily cron that drives all of this (10:45 and 17:30 ET, dispatched by the Cloudflare
Worker in `cron-worker/` with the GitHub Actions schedule as fallback) is specified in
[ADR-001 v1.2](docs/adr/ADR-001-host-and-cron-runner.md) and described in
[`docs/cron-b6.md`](docs/cron-b6.md). (`docs/cron-b6.md` was brought back in line with the code on
2026-08-19.)

Alerting is layered: six healthchecks.io dead-man switches pinged per run, a `healthcheck-audit`
workflow every 6h that pages Slack with P0/P1/P2 severity, and a Claude Code routine
(*Daily Morning Triage*, weekdays 11:00 UTC) that reads `logs/runs.jsonl` and opens a GitHub issue
on anything P0/P1.

---

*Maintained by Arash. Keep the tracker honest — the PRD's cardinal rule is that silent staleness is the trust failure. Update status as work lands, not in batches.*
