"""Live B3 smoke test: B1/B2 pipeline -> assemble prompt -> LLM -> BriefingOutput.

Needs FRED_API_KEY + AWS_REGION + AWS credentials (env or boto chain). Prints which model
answered and the raw briefing JSON (the B4 validator is a separate gate).

Doubles as the worked example of B8 run logging (PRD §339): the produce_briefing call is
wrapped to emit one audited RunRecord to logs/runs.jsonl, and any top-level failure is
forwarded to the alert webhook. Monitoring is best-effort — it never changes the
exit code or the printed output of the run itself (PRD §397).

    python scripts/run_briefing.py --as-of 2026-06-17
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date, datetime

from macro_pipeline import observability, prompt
from macro_pipeline._env import load_dotenv
from macro_pipeline.fred_client import FredError
from macro_pipeline.sources import build_source
from macro_pipeline.llm_client import BriefingLLMError, bedrock_region, make_writer_clients
from macro_pipeline.pipeline import run_pipeline
from macro_pipeline.validator import produce_briefing


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Live B3 briefing smoke test")
    parser.add_argument("--as-of", type=date.fromisoformat, default=None)
    args = parser.parse_args(argv)

    load_dotenv()

    try:
        return _run(args)
    except Exception as exc:  # noqa: BLE001 — audit + alert the failure, then re-raise
        observability.send_alert(f"run_briefing.py crashed: {type(exc).__name__}: {exc}")
        # PRD §339: every run leaves an audit row, including a crash before a briefing exists.
        observability.log_run(observability.error_record(
            run_label="manual",
            prompt_version=prompt.PROMPT_VERSION,
            timestamp=datetime.now().astimezone().isoformat(timespec="seconds"),
            error=f"{type(exc).__name__}: {exc}",
            as_of_date=args.as_of.isoformat() if args.as_of else None,
        ))
        raise


def _run(args: argparse.Namespace) -> int:
    try:
        # The shared source stack (the ONE constructor every entrypoint calls). Building a bare
        # FredClient here instead sent gold_spot's SYNTHETIC id (XAU_SPOT_GOLDAPI) to FRED and 400d
        # the whole run — the bug that killed this pre-flight, run_daily_eval and the back-test in
        # turn. sources.build_source is the durable fix; it also reads GOLD_API_KEY / BLS_API_KEY.
        source = build_source()
        result = run_pipeline(source, as_of=args.as_of, write_indicators_to=None)
    except FredError as exc:
        print(f"FRED error: {exc}", file=sys.stderr)
        return 1

    if not result.ok:
        print("Plausibility fallback — implausible values; LLM skipped.", file=sys.stderr)
        return 2

    request = result.briefing_request()
    print(f"regime_today={result.regime_today}  regime_yesterday={result.regime_yesterday}  "
          f"drivers={result.drivers}\n")

    try:
        bedrock_region()
    except BriefingLLMError as exc:
        print(f"Claude unavailable: {exc}", file=sys.stderr)
        return 1
    primary, failover = make_writer_clients()

    started = time.perf_counter()
    fb = produce_briefing(request, primary=primary, failover=failover)
    latency_ms = round((time.perf_counter() - started) * 1000)

    # Audit the run (PRD §339). llm_calls is a best-effort 1 — one provider call per run.
    # any_stale is passed explicitly: briefing_request() omits it (it is not LLM-facing).
    record = observability.record_from(
        request, fb,
        run_label="manual",
        prompt_version=prompt.PROMPT_VERSION,
        latency_ms=latency_ms,
        llm_calls=1,
        timestamp=datetime.now().astimezone().isoformat(timespec="seconds"),
        any_stale=result.any_stale,
    )
    observability.log_run(record)

    if fb.source == "llm":
        print(f"=== briefing via {fb.model} (passed B4 validator) ===")
        print(json.dumps(fb.output, indent=2, ensure_ascii=False))
    else:
        reason = fb.validation.status if fb.validation else "both providers down"
        print(f"=== FALLBACK template (reason: {reason}) ===")
        print(fb.fallback_text)
        if fb.validation and fb.validation.failed:
            print("failed checks:", fb.validation.failed, file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
