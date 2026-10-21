"""B6 productionised cron entrypoint (ADR-001 v1.2) — the sibling of run_briefing.py.

The Cloudflare Worker (primary) or GitHub's own schedule (fallback) invokes this twice daily
(~10:45 ET "AM" + 17:30 ET "PM"); it runs the full
daily.orchestrate pipeline and writes public/{indicators,briefing}.json, which the workflow
commits → Cloudflare Pages auto-deploys. The PWA holds no secrets; only this cron reads the
FRED key + AWS Bedrock config (GitHub Actions Secrets) plus the optional B8 monitoring URLs.

DST guard: GitHub cron is UTC-only and best-effort, so the workflow fires SIX fallback UTC crons
(the EDT+EST pair per session plus a buffer) and the Worker four; resolve_run_label's wide
session windows (AM 10:30-13:59, PM 14:00-22:59 ET) + already_published de-dupe publish exactly
one run per session — no manual DST edits. --force (workflow_dispatch) bypasses both guards.

Healthchecks.io bracket: ping "start" before the run, "success" on a published outcome,
"fail" on a can't-proceed (FRED down) or an unexpected crash. Monitoring is best-effort and
never changes the printed output; it only changes the exit code on a genuine failure.

    python scripts/run_daily.py                          # cron: resolve AM/PM from ET now
    python scripts/run_daily.py --run-label AM --force   # workflow_dispatch / manual
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from zoneinfo import ZoneInfo

from macro_pipeline import daily, observability, prompt
from macro_pipeline._env import load_dotenv
from macro_pipeline.llm_client import BriefingLLMError, bedrock_region, make_writer_clients
from macro_pipeline.sources import build_source

ET = ZoneInfo("America/New_York")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="B6 daily cron briefing run")
    parser.add_argument("--as-of", type=date.fromisoformat, default=None)
    parser.add_argument("--run-label", choices=["AM", "PM"], default=None)
    parser.add_argument("--out-dir", default="public")
    parser.add_argument("--force", action="store_true",
                        help="bypass the off-window skip (workflow_dispatch)")
    args = parser.parse_args(argv)

    load_dotenv()

    now_et = datetime.now(ET)
    # The ET window governs every automated (non-forced) firing — the Worker/GitHub pass a label
    # hint, but resolve_run_label is the guard, so a pre-ingest firing (before the 10:30 ET data
    # floor; FRED ingests the 08:30 BLS prints ~10:10) self-skips. --force trusts the passed label.
    run_label = (args.run_label or "AM") if args.force else daily.resolve_run_label(now_et)
    if run_label is None and not args.force:
        print(f"off-window firing (ET {now_et:%H:%M}), skipping")
        return 0  # overnight or pre-open — no ping, no writes
    if not args.force and daily.already_published(run_label, now_et):
        print(f"{run_label} briefing already published today (ET {now_et.date()}) — skipping")
        # The session IS published (by an earlier firing) — the cron is alive and the data is fresh.
        # Ping "success" even on a de-dupe skip: otherwise a later on-time fire (the Cloudflare
        # Worker, a buffer cron) that skips leaves the healthcheck without a ping for this session's
        # window → false DOWN (happened 2026-06-22 after a manual run published before the schedule).
        observability.ping_healthcheck("success")
        return 0  # another firing of this session already published; stay idempotent

    try:
        return _run(args, run_label or "AM", now_et)
    except Exception as exc:  # noqa: BLE001 — audit + alert the crash, then re-raise (nonzero exit)
        observability.send_alert(f"run_daily.py crashed: {type(exc).__name__}: {exc}")
        observability.ping_healthcheck("fail")
        # PRD §339: every run leaves an audit row, even a crash before a briefing exists.
        observability.log_run(observability.error_record(
            run_label=run_label or "AM",
            prompt_version=prompt.PROMPT_VERSION,
            timestamp=now_et.isoformat(timespec="seconds"),
            error=f"{type(exc).__name__}: {exc}",
            as_of_date=args.as_of.isoformat() if args.as_of else None,
        ))
        raise


def _run(args: argparse.Namespace, run_label: str, now_et: datetime) -> int:
    observability.ping_healthcheck("start")  # cron liveness — best-effort

    # The full source stack (sources.build_source): FRED backbone -> Treasury same-day 2Y/10Y tip
    # -> BLS same-day CPI/jobs release -> gold spot. Composed in ONE place so no entrypoint builds a
    # bare FredClient (which 400s on gold_spot's synthetic id). GOLD_API_KEY and BLS_API_KEY are both
    # OPTIONAL (absent -> that decorator degrades to FRED, never a hard failure); build_source reads
    # them from the env. FredClient raises on a missing FRED key — caught by main().
    source = build_source()

    # Missing Bedrock config is non-fatal: drop the LLM and let the deterministic template carry
    # the run. Primary Claude Opus 5.5, failover Claude Haiku 5.5 (ADR-007). AWS credentials are
    # resolved by the SDK on first call; a bad credential surfaces as a failover, then the template.
    try:
        bedrock_region()
        primary, failover = make_writer_clients()
    except BriefingLLMError as exc:
        print(f"(no Claude writer: {exc})", file=sys.stderr)
        primary = failover = None

    outcome = daily.orchestrate(
        source=source,
        primary=primary,
        failover=failover,
        as_of=args.as_of,
        run_label=run_label,
        now_et=now_et,
        out_dir=args.out_dir,
    )

    if outcome.published:
        observability.ping_healthcheck("success")
        # A fallback/refusal publish and a degraded source BOTH exit 0 and ping success — the
        # dead-man switch cannot see either. report_run_health fails the named quality/source
        # checks so they alert on their own.
        if outcome.record is not None:
            for reason in observability.report_run_health(outcome.record):
                print(f"::warning::degraded — {reason}", file=sys.stderr)
        print(f"published {run_label} briefing — source={outcome.source} "
              f"regime={outcome.card.get('regime')} data_through={outcome.card.get('data_through')!r}")
        return 0

    # can't-proceed (FRED down): last-good briefing.json is preserved (orchestrate did not write).
    observability.ping_healthcheck("fail")
    observability.ping_check("sources", "fail")
    observability.send_alert(f"Daily cron {run_label}: {outcome.reason}")
    # PRD §339: this path used to leave NO audit row at all — a FRED outage was invisible in
    # logs/runs.jsonl. Write one, carrying the per-source health that explains why we bailed.
    observability.log_run(observability.error_record(
        run_label=run_label,
        prompt_version=prompt.PROMPT_VERSION,
        timestamp=now_et.isoformat(timespec="seconds"),
        error=outcome.reason,
        as_of_date=args.as_of.isoformat() if args.as_of else None,
    ))
    print(f"run failed ({outcome.source}): {outcome.reason}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
