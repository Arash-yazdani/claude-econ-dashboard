# B6 — Cron Runner & Secrets (twice-daily GitHub Actions → commit → Cloudflare Pages)

**Status:** ✅ Implemented (Phase 1), amended in production · **Jira:** [SCRUM-30 / B6](https://modeo-team-1.atlassian.net/browse/SCRUM-30) · **ADR:** [ADR-001 v1.2](adr/ADR-001-host-and-cron-runner.md) · **Consumes:** B1 pipeline · B3 LLM · B4 validator · B8 monitoring · **Code:** `scripts/run_daily.py`, `src/macro_pipeline/daily.py`, `.github/workflows/daily-briefing.yml`, `cron-worker/`

> **Doc refreshed 2026-08-19** against the running system. Two things changed materially since
> Phase 1: the **Cloudflare Worker (`cron-worker/`) is now the PRIMARY scheduler** (GitHub's
> `schedule:` is the free fallback — it dropped an entire AM session on 2026-06-22), and
> `resolve_run_label` uses **wide session windows, not exact hours** (the exact-hour guard was
> a production bug). Any schedule change must edit **and redeploy** `cron-worker/wrangler.toml`
> with `wrangler deploy` — editing only the workflow's `schedule:` block is inert.

The **production cron machinery**. GitHub Actions runs the Python pipeline **twice daily**,
commits the fresh `public/indicators.json` + `public/briefing.json` to the repo, and
**Cloudflare Pages auto-deploys** the static PWA from that commit (ADR-001). The PWA holds
no secrets; only the cron environment carries the FRED/Gemini/Groq keys. This file documents
the schedule + DST handling, the `run_daily.py` flow + exit codes, the `briefing.json` card
contract, the secrets table, the monitoring red-gate publish-pause, and the
commit→deploy loop.

> **G1 acceptance (Product Plan):** *cron → FRED pull → classifier → LLM (+failover) →
> validator (the named O-checks) → fallback → `briefing.json` → PWA card renders, end-to-end.* B6 is the
> machinery, fully exercised **offline** via injected collaborators (`tests/test_daily.py`).
> The live FRED/LLM run on a real device is **B9**, not B6.

## Run

```bash
# manual smoke (mirrors run_briefing.py; resolves AM/PM from ET wall-clock):
python scripts/run_daily.py --as-of 2026-06-17

# force a run outside the 10/17-ET windows (what workflow_dispatch uses):
python scripts/run_daily.py --run-label PM --force --out-dir public

pytest -q tests/test_daily.py        # offline + deterministic, no network
```

Flags: `--as-of` (date, default today) · `--run-label {AM,PM}` (default: resolve from ET
now) · `--out-dir` (default `public`) · `--force` (bypass the off-window skip, for
`workflow_dispatch`).

Exit codes: `0` published **or** a clean off-window skip · `1` can't-proceed (FRED down →
last-good preserved) or an unexpected crash (re-raised after the alert + audit row).

## Decision 1 — the DST-safe schedule (ADR-001 v1.2): sessions, not exact times

GitHub `schedule` cron is **UTC-only and best-effort** — it routinely fires **1–2 hours late**
and sometimes drops a firing entirely (it dropped the whole AM session on 2026-06-22). So the
schedule is belt-and-braces: the **Cloudflare Worker (`cron-worker/`) dispatches on four
reliable UTC crons as the PRIMARY trigger** (with `force:"false"` so the de-dupe applies), and
the workflow registers **six** UTC crons of its own as the free fallback — the EDT/EST pair per
session plus a buffer. Any number may fire; `resolve_run_label` + `already_published` guarantee
**exactly one publish per ET day per AM/PM session**, with no manual clock edits at DST
boundaries. Targets: **10:45 ET (AM)** and **17:30 ET (PM)**.

| UTC cron | Fires at ET | Run | Active season |
|---|---|---|---|
| `45 14 * * *` | 10:45 EDT | AM | summer (EDT, UTC−4); 09:45 EST self-skips (pre-ingest) |
| `45 15 * * *` | 10:45 EST | AM | winter (EST, UTC−5); 11:45 EDT de-dupes |
| `15 16 * * *` | 12:15 EDT / 11:15 EST | AM buffer | both, post-ingest; catches a dropped firing |
| `30 21 * * *` | 17:30 EDT | PM | summer (EDT, UTC−4) |
| `0 22 * * *`  | 18:00 EDT / 17:00 EST | PM buffer | both |
| `30 22 * * *` | 17:30 EST | PM | winter (EST, UTC−5) |

Plus `workflow_dispatch` (manual re-run, with an optional `run_label` input).

**How the skip works:** `daily.resolve_run_label(et_now)` maps a tz-aware ET firing to a
**wide SESSION window, never an exact time**: ET **10:30–13:59 → "AM"**, **14:00–22:59 →
"PM"**, anything else → `None`; `run_daily.py` then prints `off-window firing (ET HH:MM),
skipping` and returns `0` **with no ping and no writes**. A second in-window firing of the same
session self-skips via `already_published` (a `source=="error"` row never claims its session, so
a later firing retries). `--force` bypasses both guards for manual `workflow_dispatch`.

> **Never re-introduce an exact-hour guard here.** This function WAS `hour == 10 → "AM"` /
> `hour == 17 → "PM"`, and that was a production bug, not a design: GitHub's routine 1–2h delay
> pushed nearly every real firing past the matching hour, so the cron silently self-skipped
> almost every scheduled run (fixed 2026-06-18). The **10:30 floor is a data floor**, not the
> market open: FRED ingests the 08:30 ET BLS prints ~10:10 ET, and an earlier AM run reads last
> month's number (this happened live on 2026-08-12).

### Why these two times (ADR-001 v1.1 rationale, amended v1.2)
- **10:45 ET (AM)** clears the release windows that touch this indicator set: the **8:30 ET**
  prints (CPI, Core CPI, Unemployment, GDP) and the **10:00 ET** U-Mich Sentiment release.
  What matters is not the BLS publish time but **FRED's ingest** of it: FRED is our only source
  for these series and it posted the 08:30 ET July CPI at **10:10:19 ET** on 2026-08-12.
  > **Amended 2026-08-12 (v1.2): 10:15 → 10:45 ET.** This originally read 10:15 ET for exactly
  > the reason above, and commit `969568f` (2026-06-29) moved the run to **09:45 ET** to sit 15
  > minutes after the market open — an equities-desk instinct applied to a macro-release product.
  > That traded release-day correctness for opening-bell proximity and the rationale in this
  > section was not revisited. The cost was invisible for six weeks because a release day happens
  > only ~2×/month and `releases_last_24h` was not logged until 2026-07-28. It surfaced on
  > 2026-08-12: the card served **June** core CPI (2.57%) all morning while **July** (2.47%) had
  > been public since 08:30 and on FRED since 10:10. 10:45 restores the intent with 35 minutes of
  > margin instead of the original 5.
  >
  > The **10:30 ET floor in `resolve_run_label` is what actually enforces this**, not the cron
  > times: a firing that lands before FRED has the data self-skips in both DST halves, so the
  > session is always claimed by a run that can see the release rather than by whichever cron
  > fires first. Open question, not measured: whether FRED's ~100-minute lag also holds for the
  > **10:00 ET U-Mich** release. If it does not, U-Mich lands after 10:45 and is caught by the PM
  > run that day.
- **17:30 ET (PM)** captures **FOMC decision days (2:00 ET, 8×/yr)** and settled end-of-day
  context. The PM run **always republishes** — the D7 freshness footer shows *which run*
  produced the current card, so an evening change reads as an *update*, not a contradiction.
- **Cost unchanged: $0.** Two runs ≈ 0.13% of the Gemini free RPD; GitHub Actions ≈ 6% of free
  minutes; Cloudflare ≈ 60 builds/mo of 500.

## Decision 2 — `run_daily.py` flow & exit codes

`scripts/run_daily.py` is the productionised sibling of `scripts/run_briefing.py`: same seam
pattern, plus the healthcheck bracket and the off-window skip. It owns *orchestration*; the
testable logic lives in `daily.orchestrate(...)`.

1. `load_dotenv()` (real env wins over `.env`; Sentry was removed 2026-08-06 — see observability.py).
2. `now_et = datetime.now(ZoneInfo("America/New_York"))`;
   `run_label = args.run_label or resolve_run_label(now_et)`.
   If `run_label is None and not --force` → print the off-window line and **return 0** (no
   ping, no writes — this is the planned DST skip, not a failure).
3. `observability.ping_healthcheck("start")`.
4. Build the shared source stack via `sources.build_source()` (FRED → Treasury → BLS → gold;
   NEVER a bare `FredClient()` — gold_spot's synthetic id 400s FRED); construct `GeminiClient()` / `GroqClient()` each in
   `try/except BriefingLLMError` — a **missing key → `None`** plus a logged note (a missing
   key degrades to failover or to the deterministic fallback; it does not crash the run).
5. `daily.orchestrate(...)` → `DailyOutcome`.
   - **published** → `ping_healthcheck("success")`; print a one-line summary; **return 0**.
   - **`published=False` / `source="error"`** (FRED down) → `ping_healthcheck("fail")`;
     `send_alert(...)`; print to stderr; **return 1** (the prior `briefing.json` is left
     untouched, so the PWA keeps serving last-good).
6. The whole body is wrapped: any **unexpected** exception →
   `send_alert` + `ping_healthcheck("fail")` + an
   `observability.error_record` audit row + **re-raise** (nonzero exit). Healthcheck/alert are
   best-effort and never change the exit code.

### `daily.orchestrate` — the testable core

| Step | Behavior |
|---|---|
| **Pipeline** | `run_pipeline(source, as_of=, cache_path=, write_indicators_to=out_dir/indicators.json)`. A `FredError`/any fetch failure is caught → `DailyOutcome(published=False, source="error", reason=…)` and **`briefing.json` is NOT overwritten** (last-good preserved). |
| **LLM (ok)** | `request = result.briefing_request()`; `produce_briefing(request, primary=, failover=)`. The first non-`None` client is primary, the other failover. **Both `None`** → skip the LLM and render the deterministic `FinalBriefing("fallback", …, fallback_text=fallback_template(request))`. |
| **LLM (implausible)** | `result.ok` is false (plausibility gate, B1 Decision 2). Build a minimal request from `result.readings`, emit a fallback `FinalBriefing` whose text is `validator.fallback_template(request)`; `validator_status` stays `"skipped"`. `indicators.json` was **not** written by the pipeline in this branch. |
| **Card + write** | `build_card(result, final, run_label=, now_et=)` → `out_dir/briefing.json` (`json.dumps(indent=2, ensure_ascii=False)`, parents created). |
| **Audit** | `observability.record_from(request, final, run_label=, prompt_version=prompt.PROMPT_VERSION, latency_ms=<measured around produce_briefing>, llm_calls=<1 if an LLM was called else 0; best-effort, not the exact provider-retry count>, timestamp=now_et.isoformat(timespec="seconds"), any_stale=result.any_stale)` → `log_run(record)`. |
| **Return** | `DailyOutcome(published=True, source=("refusal" if final.validation.status=="refusal" else final.source), run_label, card, record)`. |

`orchestrate` **never raises into the workflow** except a genuine can't-proceed (FRED down),
and even that is caught and returned as `published=False` — the CLI maps it to a fail ping +
exit 1.

## Decision 3 — the `briefing.json` card contract (PWA / B5)

`public/app.js` reads `public/briefing.json`. `daily.build_card` writes **exactly** this shape
(`SCHEMA_VERSION = 1`); `schemas/briefing-card.schema.json` is the machine-readable copy and
must accept `public/demo/briefing-hero.json` (default) and `public/demo/briefing-fallback.json`
(fallback) and the live `public/briefing.json`.

| Field | Type | Source |
|---|---|---|
| `schema_version` | `1` | `SCHEMA_VERSION` |
| `state` | `"default"` \| `"fallback"` | `final.source == "llm"` → `default`, else `fallback` |
| `generated_at` | ISO-8601 in America/New_York (DST-correct offset) | `now_et.isoformat()` |
| `run_label` | `"AM"` \| `"PM"` | resolved/forced run |
| `data_through` | period label of the freshest indicator (e.g. `"May 2026"`) | `data_through(result.readings)` |
| `regime` | `result.regime_today` (or `null`/`""` if unknown) | classifier (B2) |
| `regime_label` | plain-English regime | `REGIME_PLAIN.get(regime, "—")` |
| `briefing` | the LLM object \| `null` on fallback | `final.output` |
| `fallback_text` | `null` on default \| the template string | `final.fallback_text` |

The conditional is the contract's core: when `state == "default"`, `briefing` is the object
and `fallback_text` is `null`; when `state == "fallback"`, `briefing` is `null` and
`fallback_text` is the `Regime: … CPI: …% Unemployment: …%` string.
`data_through` picks the `period_label` of the reading with the latest non-null
`observation_date` (`""` if none).

### Card states the PWA must handle

| `state` | `source` (internal) | `briefing` | `fallback_text` | When |
|---|---|---|---|---|
| `default` | `llm` | object | `null` | LLM answered and passed the validator (the named O-checks) |
| `fallback` | `refusal` | `null` | string | validator status `"refusal"` (a correct data-bail) — rendered as the template card; `"refusal"` is surfaced only in `DailyOutcome.source` + the audit row, **not** the on-disk card shape |
| `fallback` | `fallback` | `null` | string | both clients absent, all providers down, validation failed → template, **or** implausible value |

## Secrets — GitHub Actions Secrets only (never in the repo or the PWA)

Set these as **repository Actions Secrets**. The workflow maps each into the run's env via the
`${{ secrets.NAME }}` expression. **Required** for a live briefing; the **optional** B8 ones
default to a no-op when unset (the code does not even build a request).

| Secret | Service | Required? | Unset behavior | Used by |
|---|---|---|---|---|
| `FRED_API_KEY` | FRED | **Required** | pipeline can't pull → `published=False`, last-good kept | `FredClient` |
| `GEMINI_API_KEY` | Google Gemini | optional (primary; absent → failover/fallback) | `GeminiClient()` → `None`; failover/fallback path | `produce_briefing` |
| `GROQ_API_KEY` | Groq | optional (failover) | `GroqClient()` → `None`; primary-only or fallback | `produce_briefing` |
| `GOLD_API_KEY` | gold-api.com | optional | gold history absent; live tick still works | `GoldSpotSource` |
| `BLS_API_KEY` | BLS API | optional | graft runs unregistered (25 queries/day cap) | `BlsSource` |
| `HEALTHCHECKS_URL` | healthchecks.io | optional | no cron ping (no request) | `ping_healthcheck` |
| `ALERT_SLACK_WEBHOOK` | Slack | optional | no alerts (no request) | `send_alert` |

The workflow **never echoes or prints a secret**. `.env`, `data/cache`, and local logs stay
gitignored. See `docs/monitoring-runbook.md` for the full opt-in setup of the three B8
services and `.env.example` for the copy-paste block.

## Decision 4 — the monitoring red-gate (publish pause)

After the run, the workflow runs `python scripts/build_monitoring.py` as a step with
`id=monitor` and `continue-on-error: true`. The generator **exits 3 on RED** (a critical
O-check — `O5` number-grounding / `O8` banned-phrase — below 100%, or fallback rate > 50%;
see the runbook). A following step gates on `steps.monitor.outcome == 'failure'`:

1. prints `::error::Monitoring RED — pausing publish`;
2. best-effort `send_alert` via
   `python -c "from macro_pipeline import observability as o; o.send_alert('Daily cron: monitoring RED — publish paused')"`;
3. **exits 1 without committing** — so the PWA keeps serving the last-good `briefing.json`
   rather than shipping more briefings on top of a number-grounding regression.

This is the cron half of the B8 cardinal rule (*red widget ⇒ cron pauses*): **B8 ships the
`exit 3` mechanism; B6 wires it to the trigger.** Triage follows the runbook
(`docs/monitoring-runbook.md` → "When it goes red").

## Decision 5 — commit → Cloudflare Pages auto-deploy

When the run is **green and files changed**, the workflow commits the fresh data back as
`github-actions[bot]` and pushes; Cloudflare Pages, connected to the repo (ADR-001),
auto-deploys the static PWA from that commit — fresh JSON ships with the build.

- `permissions: contents: write` (least privilege — only what the data commit needs).
- Staged paths (explicit allowlist, never `git add -A`): `public/indicators.json`,
  `public/briefing.json`, `logs/runs.jsonl`, `logs/daily_eval.csv`, `logs/reconciliation.jsonl`,
  `docs/monitoring.md`, `data/cache/pipeline-cache.json` — each added only **if it exists**, so an off-window self-skip (writes nothing; and `logs/runs.jsonl` is absent before
  the first run) never trips a `fatal: pathspec did not match` and red-X's the job.
- Commit message: `chore(cron): daily briefing <date> <run_label>` — **deliberately no `[skip ci]`**.
  The workflow has no `push:` trigger (so the bot's data commit can't re-trigger it — no loop), and
  **Cloudflare Pages skips the deploy for any `[skip ci]`/`[CI Skip]` commit**, which would stop the
  fresh briefing from ever publishing. The commit is left deployable so Cloudflare ships each update.
- Idempotent: commit **and** push run only inside `if ! git diff --staged --quiet; then … fi`,
  so a no-op run (nothing changed) succeeds without an empty commit or a stray push.
- Never commits secrets or `.env`. `data/cache/pipeline-cache.json` IS committed, deliberately:
  it is what release detection and `regime_yesterday` compare against across ephemeral runners —
  without it `whats_new` and regime-change could never fire.

## Module / file map

| File | Responsibility |
|---|---|
| `src/macro_pipeline/daily.py` | `resolve_run_label`, `data_through`, `build_card`, `orchestrate`, `DailyOutcome` — the production orchestrator (collaborators injected for tests) |
| `scripts/run_daily.py` | productionised CLI: `load_dotenv` → resolve/skip/de-dupe → healthcheck bracket → `build_source()` + real clients → `orchestrate` → exit codes |
| `.github/workflows/daily-briefing.yml` | the six fallback UTC crons + `workflow_dispatch` (dispatched by `cron-worker/`, the primary), the run step, the red-gate, the commit/push |
| `schemas/briefing-card.schema.json` | machine-readable `briefing.json` contract (draft 2020-12, `additionalProperties:false`); accepts both demos + live card |
| `tests/test_daily.py` | offline coverage: `resolve_run_label` DST guard, `build_card` states, end-to-end orchestrate (writes + schema-validates + logs), fallback path, FRED-down last-good |

## Open / handoffs

- **B9 is the live run.** B6 is fully offline (injected `FredSource` + stub LLM clients,
  `tests/test_daily.py`). The first real FRED/Gemini/Groq end-to-end (G1 acceptance on a real
  device) is **B9**, gated on the live keys in Actions Secrets.
- **Operate it** via `docs/monitoring-runbook.md` (the static runbook) and the auto-generated
  `docs/monitoring.md` (live numbers). The red-gate and the healthcheck bracket described here
  are the cron entrypoint the runbook previously deferred to B6/B9.
