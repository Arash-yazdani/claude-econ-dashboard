# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

> **This repo is Claude Econ Dashboard**, a copy of Economy-Dashboard with the LLM layer on Claude via
> Amazon Bedrock (ADR-007). Everything below was written for the original and still holds for the
> pipeline, with three exceptions: `llm_client` is `BedrockClaudeClient` (Opus 5.5 writer, Haiku 5.5
> failover, Sonnet 5.5 judges); the Gemini/Groq quota rows in the ceilings table do not apply; and
> nothing here is in production yet (all schedules are commented out, see README). Statements about
> 83 consecutive days, Bruce, macro.modeo.io and `gh workflow run ... -R Arash-yazdani/Economy-Dashboard`
> describe the original.

## What you are working on

A live, $0-budget U.S.-macro PWA with one AI feature: the **Daily Conversation Briefing**. Twice a
day a cron pulls FRED indicators, a deterministic classifier labels the macro regime, one LLM call
narrates it, a deterministic validator gates the output, and the result is committed as
`public/briefing.json` + `public/indicators.json`. Cloudflare Pages serves `public/` at
macro.modeo.io. One reader (Bruce), free-tier APIs only, no payment method on any vendor.

**This is production, not a build.** It has published on 83 consecutive days since 2026-06-18 with
no missed day; the validator has caught two bad outputs (2026-08-25, 2026-09-02) and both runs
published the fallback card. Nearly every change you make is a change to a running
system whose reader notices. The build phase is over; treat the planning documents (`PRD.md`,
`Product Plan.md`, `Gantt chart.md`) as intent, and the code plus `docs/adr/` as truth.

Cite provenance the way this codebase already does: PRD q-numbers, ADR numbers, dated incidents,
who signed off. When you change load-bearing logic, say what broke and when, not just what the code
now does.

## Commands

```bash
.venv/bin/python -m pytest -q
```

446 tests, fully offline, no keys or AWS needed, ~1s. `pyproject.toml` sets `pythonpath = ["src"]`, so
no `PYTHONPATH` juggling. One test to run:

```bash
.venv/bin/python -m pytest tests/test_validator.py::test_o5_ungrounded_number_fails -q
```

| Task | Command |
|------|---------|
| Install from scratch | `pip install -e ".[dev,langsmith]" -c constraints.txt` (pins; refresh with `pip freeze --exclude-editable`) |
| JS formatting-set drift check | `node --test "tests/js/*.test.js"` (also runs in `ci.yml` on code pushes) |
| Check live health | read [docs/monitoring.md](docs/monitoring.md) (regenerated every run), then `tail -1 logs/runs.jsonl` |
| Query all healthchecks | `python scripts/check_healthchecks.py` (needs `HEALTHCHECKS_RO_KEY`; also runs every 6h in `healthcheck-audit.yml`) |
| Regenerate the dashboard (exit 3 = RED) | `.venv/bin/python scripts/build_monitoring.py` |
| Data pipeline only, no LLM | `.venv/bin/python -m macro_pipeline --as-of 2026-08-17` |
| One live briefing, end to end | `.venv/bin/python scripts/run_briefing.py` |
| A full cron run exactly as prod does it | `.venv/bin/python scripts/run_daily.py --run-label AM --force` |
| Ship gate, 12 cases (~3 min, spends shared LLM quota) | `.venv/bin/python scripts/run_judge.py` |
| Ship gate, prompt-change tier | `.venv/bin/python scripts/run_judge.py --strict` |
| Score today's published briefing | `.venv/bin/python scripts/run_daily_eval.py --dry-run` |
| Classifier back-test over ~12y FRED | `.venv/bin/python scripts/backtest_thresholds.py --years 12` |
| Bruce's annotation sheet | `.venv/bin/python scripts/build_review_sheet.py --round d1` |
| Bruce's data-accuracy memo | `.venv/bin/python scripts/build_accuracy_report.py --format email` |
| Manual production recovery | `gh workflow run daily-briefing -R Arash-yazdani/Economy-Dashboard -f run_label=AM` |
| Preview the PWA | Browser-pane `preview_start {name: "pwa"}` (serves `public/` on :5050) |

(The old known local failure in `tests/test_tracing.py` was fixed 2026-08-19: the test now
asserts `enabled()` against SDK availability via `find_spec`, so the suite is green with or
without the optional `langsmith` extra.)

**Secrets:** `.env` locally (gitignored), GitHub Actions Secrets in CI. `.env.example` documents
every variable and what happens when each is absent. The Cloudflare Worker has its own separate
secrets set with `wrangler secret put`, which are not in `.env`.

## Architecture

One module per stage in `src/macro_pipeline/`, one direction:

```
fred_client  (wrapped by origin-graft decorators, composed once in sources.build_source:
              treasury_client (10Y/2Y nominal + TIPS real curves), bls_client (CPI/jobs, NSA CPI
              per the 2026-08-18 Bruce sign-off — the card reads the 3.4% BLS headline, not
              FRED's SA 3.3%), nyfed_client (EFFR->DFF), cboe_client (VIX T+0 close),
              fiscaldata_client (DAILY federal debt onto quarterly GFDEBTN), gold_client.
              All keyless except BLS/gold (optional keys). Origins + plan: docs/v4-source-upgrades.md)
  -> pipeline.run_pipeline     readings, deltas, plausibility gate, staleness, releases, cache
  -> classifier.classify       rules-only regime + up to 2 drivers, no model
  -> prompt.build_messages     static-then-dynamic XML for cache efficiency
  -> llm_client                Claude Opus 5.5 primary, Haiku 5.5 failover (Bedrock, ADR-007)
  -> validator.produce_briefing  O-checks, retry once, then deterministic template
  -> daily.orchestrate         writes public/{indicators,briefing}.json + one RunRecord
  -> observability.log_run     logs/runs.jsonl, healthcheck pings, Slack alerts
```

`scripts/run_daily.py` is the only production entrypoint. `daily.orchestrate` is the seam it drives.
`src/macro_pipeline/__main__.py` runs the data half alone.

Prefer citing functions and constants over line numbers when you write things down. A doc in this
repo pinned `run_daily.py:64` and was wrong by one line within weeks.

### Invariants

- **Sources and LLM clients are injected**, so the whole flow runs offline and deterministically in
  tests. `tests/conftest.py` has an in-memory `FakeFredSource` covering every series.
- **The reader never sees a failure.** A FRED outage returns `DailyOutcome(published=False,
  source="error")` and deliberately does *not* overwrite `briefing.json`, so the PWA keeps
  last-good. An invalid LLM output falls back to `validator.fallback_template`.
- **Monitoring must never break a run** (PRD §397). Every side-effecting call in `observability.py`
  and `tracing.py` swallows its own exceptions and returns `False`. An unset env var is a pure
  no-op that never even constructs a request.
- **A decorator source must never raise.** `treasury_client`, `bls_client` and `gold_client`
  swallow everything, record degradation via `observability.record_source`, and fall back to the
  less fresh tier.
  `FredClient` is the one source allowed to abort a run. A source must also never return an empty
  observation list: `pipeline._pick_value` raises on it.
- **The regime is rules-only.** The LLM narrates the classifier's label and never re-derives it.

### Tiers

`config.INDICATORS` holds 26 specs: 7 `core`, 11 `drawer`, 8 `market`.

| Tier | Feeds the LLM | Feeds the regime | On an implausible value |
|------|---------------|------------------|-------------------------|
| `core` | yes | 6 named ids only | **fails the whole run**, skips the LLM, publishes the fallback card |
| `drawer` | no | no | value blanks to `None` ("—"), run continues, `record_blanked` fires |
| `market` | no (except the context allowlist) | no | same as drawer, plus the ADR-003 no-securities-advice posture |

Two things about that table are easy to get wrong:

- Making a new indicator `core` hands it a kill switch over the briefing. Get `plausible_min` /
  `plausible_max` wrong and every run publishes the fallback.
- A new `core` row does **not** move the regime. `classifier.classify` pulls six ids by name and
  ignores everything else. It only widens the LLM's evidence and arms the plausibility gate.

### The validator is the contract

`validator.py` implements the named O-checks: O1 not-an-object, O2 shape, O3 id whitelist, O4
conditional emptiness (`whats_new` non-null iff a release dropped, `regime_change` non-null iff the
regime moved), O5 number grounding to ±0.05, period grounding, O8 banned phrases, length caps, and
the O16 exact refusal envelope. O5, O8 and O16/O17 are PRD non-negotiables.

Two honest caveats the code already states: the implemented set is 8 named checks, not the
"17-check validator" the planning-era docs (PRD, Product Plan, Gantt, launch checklist) still claim; and O8 is a 10-phrase blocklist that
will not catch a bare "buy"/"sell" or a ticker.

## Operating it

### The schedule, and the one footgun

The **Cloudflare Worker in `cron-worker/` is the primary scheduler**, not GitHub. It fires four UTC
crons and `workflow_dispatch`es the workflow with `force:"false"`. GitHub's own `schedule:` block
(six crons) is the fallback, kept because it costs nothing; GitHub cron is best-effort and dropped
an entire AM session on 2026-06-22.

> **Editing `.github/workflows/daily-briefing.yml`'s schedule alone is inert.** The Worker keeps
> dispatching on its own crons until `cron-worker/wrangler.toml` is edited *and redeployed* with
> `wrangler deploy`. This is the highest-risk schedule change in the repo: on the 2026-08-12 AM
> move, changing only the workflow would have left the Worker publishing pre-ingest anyway.

Correctness does not come from any single trigger firing. It comes from two functions:

- `daily.resolve_run_label` maps an ET firing to a wide **session**: 10:30-13:59 is AM, 14:00-22:59
  is PM, anything else returns `None` and the run self-skips with no ping and no writes.
- `daily.already_published` scans `logs/runs.jsonl` for a successful row with the same label on the
  same ET date. A row with `source == "error"` never claims its session, so a later firing retries.
  It fails open on any read error.

**Never re-introduce an exact-time guard.** It was one, and it silently self-skipped nearly every
scheduled run once GitHub's delay pushed a firing past the minute. `docs/cron-b6.md` still describes
the old exact-hour behavior; an agent "restoring" what that document says would break production.

**The 10:30 floor is a data floor, not the market open.** BLS publishes CPI and the jobs report at
08:30 ET, but FRED, our only source, does not ingest until ~10:10 ET. On 2026-08-12 the 09:45 run
served June core CPI (2.57%) all morning while July (2.47%) had been public since 08:30 and landed
on FRED at 10:10:19. The floor buys the correct number on the ~2 mornings a month it matters, at
the cost of about an hour of latency on ordinary days.

### The publish path pushes to main

The cron commits an explicit allowlist (`public/indicators.json`, `public/briefing.json`,
`logs/runs.jsonl`, `logs/daily_eval.csv`, `logs/reconciliation.jsonl`, `docs/monitoring.md`,
`data/cache/pipeline-cache.json`) straight to `origin/main`, and lands roughly **10 commits a day**.

> **Local `main` goes stale within hours. `git fetch` and rebase before you push anything.**
> A 30-commit lag after three days is normal.

Three rules that live in the workflow and must survive any edit to it:

1. **Never `[skip ci]`.** Cloudflare Pages skips deploying any commit whose message contains it,
   which would freeze the dashboard. There is no loop to suppress: the workflow has no `push:`
   trigger.
2. **The failure path commits the audit log** via a separate `if: failure()` step. Without it,
   `logs/runs.jsonl` could only ever contain successes, and every alert built on it was blind to
   failure by construction.
3. **A RED monitoring build refuses to publish.** `build_monitoring.py` exits 3 **only** when the
   fallback rate is over 50% (the product is degenerate, so last-good beats more templates); the
   next step alerts and fails the job without committing. A critical check (O5/O8) below 100% is
   **AMBER**: it alerts and keeps publishing, because a caught check means the validator did its
   job and that run already published the safe fallback card. Pausing there swaps a safe current
   card for a stale one — it froze the live dashboard for two days on one caught O5
   (`docs/incidents/2026-08-26-red-gate-latched-on-a-caught-check.md`).

`data/cache/pipeline-cache.json` is committed on purpose. It is what release detection and
`regime_yesterday` compare against across ephemeral runners; without it `whats_new` and
regime-change can never fire.

### Alerting: six checks, and what each one exists to catch

| Check | Fails when | Exists because |
|-------|-----------|----------------|
| `daily-macro-brief` (`HEALTHCHECKS_URL`) | no ping for a session window | the dead-man switch for "the cron did not run" |
| `cron-worker` (Wrangler secret) | the Worker's dispatch fails, or it stops firing | an expired PAT once killed the primary scheduler with nothing but a `console.error` |
| `publish-quality` (`_QUALITY`) | `record.source != "llm"` | a fallback publish exits 0 and pings the main switch green, so a week of template cards looks healthy |
| `data-sources` (`_SOURCES`) | a row was blanked, or any-tier row went stale, or FRED was unreachable, or a source has been degraded 4+ consecutive runs (`observability.persistently_degraded`) | the same blindness for data quality |
| `judge-daily` (`_JUDGE`) | the daily eval's pass rate drops below 0.9 | the eval step is `continue-on-error`, so the check *is* the alert |
| `healthcheck-audit` (workflow) | any check is DOWN when queried every 6h | a missed individual check notification can go unnoticed; this is the meta-check |
| `weekly-regression` (`_REGRESSION`) | the gate verdict is not PASS, or the gate stops running | the gate silently stopped running for five weeks once |

Two consequences worth holding onto:

- **An unset URL means that signal is silently dead, not noisy.** `ping_check` deliberately never
  falls back to the main `HEALTHCHECKS_URL`, so a misconfigured named check cannot raise a false
  outage on the dead-man switch.
- **Slack and healthchecks now disagree by design** (changed 2026-08-14, commit `d5243e4`). The
  current predicate: `quality` fails iff `source != "llm"`; `sources` fails iff
  `indicators_blanked` is non-empty **or** `stale_indicators` (any tier) is non-empty. A source that
  degraded but still delivered the right number appears in the Slack alert body and pings `sources`
  *success*. Page on the outcome, not on the fetch attempt. The one exception, added 2026-09-03: a
  source degraded on **4 consecutive runs** pages `sources` anyway. The Treasury graft died silently
  for 22 runs (2026-08-24 PM to 09-03, an Akamai User-Agent tarpit) because "delivered, but day-late"
  never paged; persistence is the signal that separates a transient from a dead feed.

**Healthcheck cron slots must sit before the earliest possible ping.** Cron mode re-arms on every
ping, so a punctual early ping arms the next slot instead of closing the current one and the check
goes DOWN because the pipeline was on time. Widening the grace can never fix that; grace absorbs
lateness, and this failure is earliness. It paged twice on 2026-07-29 with `Last Ping Type: Success`.

### Runbooks

`docs/runbooks.md` has ten sections (its own header still says eight). Two are now automated because
a gate nobody runs is not a gate:

- **Runbook 6, the prompt-change gate** runs weekly in `weekly-regression.yml` (Mondays 12:00 UTC,
  non-strict). The strict tier is an opt-in `workflow_dispatch`.
- **The classifier back-test** runs monthly in `monthly-backtest.yml` (1st, 11:00 UTC). It commits
  nothing and always exits 0; an oscillation problem surfaces only as a `::warning::` annotation.

Runbooks 1-5, 7, 7b and 9 are still manual. Runbook 7b (Bruce's D+1/D+7/D+30 annotation round) is
the only mechanism that will ever produce a judge-vs-human κ, and it has not run: the `human_c1`,
`human_c3` and `human_note` columns in `logs/daily_eval.csv` are all empty.

## Changing it safely

### What is locked, and what unlocking costs

| Locked thing | Where | To change it |
|--------------|-------|--------------|
| Regime classifier, **v1.2** | `classifier.py` | Re-run `backtest_thresholds.py`, get a fresh D9 sign-off. Not a code edit. Runbook 5 forbids fixing a disputed regime in the prompt. |
| Prompt, **v1.5** | `prompt.py` `PROMPT_VERSION` | Bump the version, run `run_judge.py --strict` (0 below-3, all 12 measured), get Bruce's ack, append a row to `prompt_history.csv`. Re-opens SO8. |
| 12 regression fixtures | `tests/fixtures/regression-cases-v1.0.json` | New version + a `prompt_history.csv` note + a full re-run. Retarget a test, never edit a fixture to make something pass. |
| LLM boundary contracts | `docs/schemas/briefing-io-v1.0.md`, `schemas/*.json` | D8-locked at v1.0. Add new fields as optional, never to `required`, so old rows stay valid. |
| Six ADR decisions | `docs/adr/` | ADR-001 host+cron, ADR-002 compliance sign-offs, ADR-003 markets display-only, ADR-004 the context allowlist, ADR-005 whats_new tuning, ADR-006 gold spot. |

**The prompt gate has two tiers and they are not interchangeable.** The standing rule is no
criterion below 3 on more than 10% of items; the prompt-change tier is 0 below-3. Only `--strict`
selects the second, and only the second can re-ack SO8. Run it *early in the UTC day*: a late run
returns INCONCLUSIVE, which is not a pass. And never run it near a briefing window (the workflow
refuses inside 14:30-16:30 / 21:15-23:15 UTC) because both providers share one free-tier quota.

**Do not push a prompt bump to main before it certifies.** On 2026-08-13 v1.6 was reverted for
exactly that: it was already on main and would have gone live uncertified on the next run. Note the
consequence still visible today: `eval_log.csv` contains v1.6 rows while production is v1.5, and
`prompt_history.csv` deliberately has no v1.6 row. Never read "latest scored version" as "production
version".

### Adding one indicator

`config.INDICATORS` is a tuple of frozen dataclasses whose order is the canonical dashboard order.
Nine fields are required; only `tier`, `source_url`, `long_name` and `live_tick` have defaults.
A display-tier row touches six files:

1. `config.py` - the `IndicatorSpec` row.
2. `tests/conftest.py` - a `_base_series()` entry, or **every pipeline test raises KeyError**. Add a
   `"<SERIES>|lin"` key too when `units` is not `"lin"`. Omitting that key does not error; the fake
   silently returns the transformed series as if it were the raw level, so `level` publishes a
   percentage where an index belongs.
3. `public/app.js` - a `CATEGORIES` bucket (a missing id lands in "Other" rather than vanishing),
   plus `DEC2` / `SIGNED` if it needs two decimals or a leading sign, plus the `fmtValue` unit
   switch if it introduces a new unit string.
4. `tools/macro-widget.js` - the same `DEC2` / `SIGNED` sets, duplicated by hand. **These drift**:
   `core_cpi_yoy` reads 2.5% on the home-screen widget (1dp) and 2.48% in the app (2dp, kept level
   with the WHY-block basis line); `tests/js/formatting_sync.test.js` allowlists exactly that one and
   fails on any other difference. (`cpi_yoy` used to drift the same way;
   it is now 1dp in both, to match the published BLS headline Bruce quotes.)
5. `tests/test_v2_tiering.py` and `tests/test_pipeline.py` - about eleven hard-coded count
   assertions (currently 26 specs, 29 published rows, 30 fetch calls, 10 request indicators). These
   are the de-facto gate on registry changes; expect them to fail and update them deliberately.
6. Nothing else. `INDICATORS_BY_ID`, plausibility, staleness and the published JSON derive
   automatically.

A **core** row additionally needs a `prompt._PHRASING` entry (a missing key raises `KeyError` during
prompt assembly, and that escapes `daily.orchestrate` entirely: it is not the graceful fallback
path) and a unit within `schemas/briefing-request.schema.json`'s enum of `%`, `index`, `USD`.

### Letting the writer see a new indicator

Three places must stay mirrored: `config.BRIEFING_CONTEXT_IDS`, `prompt._CONTEXT_META`, and
`prompt._PHRASING`. Change one, change all three. The two drift directions fail differently: a
`_CONTEXT_META` entry without `_PHRASING` is a hard `KeyError`; an id in `BRIEFING_CONTEXT_IDS`
without `_CONTEXT_META` is silent, because O3 whitelists by request id while the metadata block
never mentions it. `tests/test_prompt.py` guards the silent direction.

This is the ADR-004 mechanism, and it is the sanctioned way to widen the writer's evidence without
touching the D9-signed classifier. It is also a live deviation from ADR-003's blanket "market tier
excluded from the LLM": `brent_oil` and `usd_index` are market-tier and in the allowlist. Equity
securities (S&P, Nasdaq, market cap) remain excluded regardless, and O8 hard-blocks advice language.

### Adding a data source

Implement one method: `fetch_series(series_id, units, limit) -> SeriesResult`, observations
**newest-first** (load-bearing: `_pick_value` walks forward to the first non-null and
`compute_delta` subtracts `[1]` from `[0]`). Then decorate an existing source rather than replacing
it. There are three working patterns: `TreasuryYieldSource` and `BlsSource` call FRED first and
graft a fresher same-day tip on top (Treasury for the 2Y/10Y yields, BLS for the CPI/jobs releases);
`GoldSpotSource` intercepts one synthetic series id (`XAU_SPOT_GOLDAPI`) that is never sent to FRED
and passes everything else through. `bls_client` matches each series' seasonal-adjustment convention
to its FRED backbone (NSA for CPI, SA for the jobs numbers) — a mismatched tip would splice two
conventions into one series.

The stack is composed once, in `sources.build_source()`, which every entrypoint now calls — the
durable fix for the trap that a **bare `FredClient()` sends the synthetic gold id to FRED and 400s
the entire run** (it had broken `run_briefing`, the back-test, `run_daily_eval` and even
`python -m macro_pipeline` in turn). A new entrypoint must call `build_source()`, never construct
the chain by hand.

### Adding a derived row

One tuple in `derived.DERIVED`: `(id, display_name, minuend_id, subtrahend_id, tier)`. No config
entry, no fetch. They are appended in `run_pipeline` at exactly one legal point: after
`plausibility.validate` and `staleness.apply` (which index `INDICATORS_BY_ID` and would `KeyError`)
and after `cache.update_snapshot` and `classifier.classify` (which must not see them).

Known ceilings: a missing leg is a silent skip; `unit` is hard-coded `"%"` and `source_url` is `""`
(a dead link in the PWA), so a non-percentage spread would be mislabeled; only daily-daily and
daily-monthly pairs get a history, anything else ships `history=[]` which closes the detail sheet;
and a derived row can never appear in `releases_last_24h`, because release detection runs before the
append. That last one makes the prompt's "surface the 10Y-2Y curve on a quiet day" instruction
unreachable for the curve specifically.

### Adding a consumer

The published contract is two files. `public/briefing.json` carries `schema_version: 1` and is fully
specified by `schemas/briefing-card.schema.json` (no consumer actually reads the version).
`public/indicators.json` is the wide one, 29 rows and ~415KB, and **has no schema file at all**: the
largest and most-consumed artifact is the one with no machine-readable contract. The existing second
consumer is `tools/macro-widget.js`, which re-implements the formatting layer by hand. A third
consumer would inherit that duplication.

Note the schemas are test-time only. Nothing in the production path validates against them, so
drift ships silently. There is live drift today: `classifier` v1.2 can emit `indeterminate`, but
`briefing-request.schema.json` still restricts `regime_today` to the five v1.0 labels.

### The ceilings, and which one binds first

| Dependency | Cap | Today | Notes |
|-----------|-----|-------|-------|
| FRED | PRD soft target 25/day, hard stop 1,000/day, API limit 120 req/min | ~116 HTTP/day from publishing, ~166 with the PM reconcile | Each `fetch_series` is two HTTP GETs; a non-`lin` indicator costs a second fetch. No retry, no backoff, no sleep anywhere in the path. |
| Gemini free tier | 1,500 RPD (`dashboard.DEFAULT_CAPS`) | ~6 calls/day, 0.4% | Shared with both judges and the weekly gate. This is the quota that has actually caused an outage. |
| Groq free tier | 1,000 RPD | ~0 on a healthy day | Failover only. Model decommissioning has silently killed the failover TWICE (llama-4-scout, then llama-3.3-70b on 2026-08-25); the weekly regression now probes /models and pages on absence. |
| gold-api.com | 10 requests/**hour** on `/history` | 2/day | The only per-hour cap, so it binds on frequency rather than volume. |
| LangSmith | 5,000 traces/month, 14-day retention | ~150-200/month | Never enable online evaluators: they force billable 400-day retention. |
| GitHub Actions | 2,000 min/month | <2 min/day, ~6% | Also auto-disables scheduled workflows after 60 days of repo inactivity. The cron's own commits are what keep it alive. |
| Cloudflare Pages | 500 builds/month | ~60/month | One build per data commit. |
| Payload size | 5MB spec, 5MB cache hard cap | `indicators.json` 415KB | About 12x headroom. |

**FRED binds first**, because it is the only dependency that scales with both indicator count and
run frequency. Doubling both takes it to roughly 660 HTTP/day, about 66% of the hard stop, while
everything else stays under 25%. The sharper limit is the secondary one: 58 un-throttled sequential
calls per run with no backoff, where a single transient failure aborts the whole run.

The real constraint on growth is not any API cap. It is that one display-tier indicator touches six
files and about eleven count assertions, with two hand-duplicated JS formatting layers that have no
automated cross-check.

## Traps already paid for

Each of these cost a real incident. Do not re-introduce them.

- **Do not schedule against BLS publish times or the market open.** FRED sits between us and BLS and
  takes ~100 minutes (2026-08-12).
- **Do not run the strict gate near a briefing window.** Six certification attempts, the last two
  minutes before the PM dispatch, drained both providers and produced the first fallback in 111 runs
  (2026-08-07). Production eats first; retry pressure is load.
- **Do not page on a fetch attempt.** Ask whether a published number is wrong or missing. Four of
  the six degraded-source events ever logged were one gold-api DNS transient that cost the reader
  nothing (2026-08-14).
- **Do not treat an empty external response as a degradation without asking whether the missing
  thing should exist.** An empty Treasury month file on a Saturday is a calendar fact, and the noisy
  alert it raised was sitting on top of a real one: a day-stale 10-Year, 7bp off (2026-08-01).
- **Do not let a judged model report its own rubric arithmetic**, do not report "cannot measure" as
  "failed", and do not compute a rate over `n` when the honest denominator is `measured`. All three
  produced confident false accusations in one week (2026-08-04). Prefer the denominator that can
  only shrink the pass rate.
- **Do not add `[skip ci]`** to the data commit; Cloudflare Pages would stop deploying.
- **Do not stage the audit log only on the success path.**
- **Do not construct a bare `FredClient()`** in a new entrypoint — call `sources.build_source()`.
- **Do not trust `public/app.js` to be covered.** Only the DEC2/SIGNED drift check in `tests/js/` runs
  against it; browser verification against the live feed is the only check on behavior.

## Documents that are currently wrong

Verified stale as of 2026-08-17. Read the code first, and fix the doc if you touch the area.

**Fixed 2026-08-19** (were actively dangerous; kept here so nobody "restores" an older copy):
`docs/cron-b6.md` (was: exact-hour guard, four crons, 10:15 ET, "never commits data/cache"),
`.github/workflows/daily-briefing.yml`'s header (was: 09:45 ET / market open / 09:30 window),
`docs/adr/ADR-004` + `ADR-005` (was: `wti_oil`; member is `brent_oil`), and
`docs/feedback/2026-08-13-bruce-signoff-round.md` (was: "shipped as v1.6"; a dated correction
block now records the revert).

**Superseded but harmless if you know:**

- `docs/eval-and-alerting-setup.md`: two of its three "Open items" are closed (v1.4.2 is certified;
  `prompt_history.csv` exists); the Worker cron times and the earliness arithmetic predate the
  2026-08-12 move.
- `docs/launch-readiness-checklist.md`: prompt v1.3.1, classifier v1.1, "7-indicator mapping",
  Sentry. All superseded.
- `docs/v2-indicator-expansion.md`: still framed as a proposal for 19 indicators; shipped as 26, and
  three of its recorded decisions (debt-to-GDP series, retail sales, industrial production) were
  later changed or removed.
- `docs/classifier/`: has no v1.2 document at all. v1.1 is still written as an unshipped proposal
  referencing a `classify_proposed()` that does not exist. Rule W3 lives only in the code header.
- `docs/feedback/2026-08-13-bruce-signoff-round.md` says the labor-market change "shipped as v1.6".
  It was reverted by the very commit that added the document.
- `config.py`'s own module docstring still says "the 7 V1 indicators" and documents only `lin` and
  `pc1`; `payrolls` uses `chg`.
- "17-check validator" appears in 13 places across 8 files. The implemented set is 8 named checks.
- `PRD.md`: single ~8:06 ET cron, 7 indicators, "v1.3 lock pending", Sentry in the monitoring stack.

## Conventions

- Conventional commits with a scope (`fix(alerting):`, `feat(prompt):`, `chore(cron):`). Bodies are
  long and explain the failure mode, with dates and evidence. End with
  `Co-Authored-By: Claude <model> <noreply@anthropic.com>`.
- `marketing/` and `*.excalidraw` are gitignored by user decision (local-only, never on GitHub).
- Sign-off evidence has fixed homes: `prompt_history.csv` per prompt version, `eval_log.csv` per
  judge verdict, `docs/adr/` for decisions, `docs/feedback/` for Bruce rounds, `docs/incidents/`
  for failures (`YYYY-MM-DD-short-slug.md`), `logs/runs.jsonl` for the per-run audit.

## Where the decisions live

`docs/adr/` (6 ADRs) · `docs/runbooks.md` (10 runbooks) · `docs/monitoring-runbook.md` plus the
generated `docs/monitoring.md` · `docs/incidents/` · `docs/schemas/briefing-io-v1.0.md` with machine
copies in `schemas/` · `docs/eval/regression-cases-v1.0.md` (the canonical statement of the two gate
tiers) · `docs/classifier/` · `docs/feedback/` · `docs/eval-and-alerting-setup.md` ·
`docs/cron-b6.md`.
