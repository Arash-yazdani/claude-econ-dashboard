"""B7 live regression: produce + judge all 12 cases, roll up to the q26 ship gate.

FRED is NOT required (cases are pre-built BriefingRequests), but AWS_REGION + AWS
credentials for Claude on Bedrock are. Writes every verdict to eval_log.csv.

    python scripts/run_judge.py
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from macro_pipeline import judge, prompt
from macro_pipeline._env import load_dotenv
from macro_pipeline.judge import log_verdicts, run_regression, summarize
from macro_pipeline import observability
from macro_pipeline.llm_client import BriefingLLMError, bedrock_region, generate_briefing, make_writer_clients


VERDICT_BANNER = {"PASS": "PASS ✅", "FAIL": "FAIL ❌", "INCONCLUSIVE": "INCONCLUSIVE ⚠"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="B7 live LLM-judge regression")
    parser.add_argument("--log", default="eval_log.csv")
    parser.add_argument("--pace", type=float, default=10.0, help="seconds between cases (free-tier RPM)")
    parser.add_argument(
        "--strict", action="store_true",
        help="PROMPT-CHANGE gate (Runbook 6): zero below-3 instead of the standing <=10%%. "
             "Use this for any prompt.py edit — it re-opens SO8 and needs a fresh Bruce ack.")
    args = parser.parse_args(argv)

    load_dotenv()
    cases = json.loads(Path("tests/fixtures/regression-cases-v1.0.json").read_text())["cases"]
    try:
        bedrock_region()
        writer, writer_failover = make_writer_clients()
    except BriefingLLMError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)  # noqa: F821 (see import below)
        return 1

    def briefing_for(req):
        res = generate_briefing(req, primary=writer, failover=writer_failover)
        return res.output, res.model

    results = run_regression(cases, briefing_for=briefing_for, judge_model=judge.make_judge(),
                             judge_failover=judge.make_judge_failover(), pace_seconds=args.pace)
    s = summarize(results, strict=args.strict)
    if s["errored"]:
        print(f"(⚠ {len(s['errored'])} case(s) errored after retry: {s['errored']})")
        for r in results:
            if r.validator_status == "error":
                print(f"    {r.case_id}: {r.verdict.get('error', '?')}")
        print()

    print(f"{'case':4} {'L1':9} {'model':20} c1 c3 c4 c5")
    for r in results:
        v = r.verdict
        flag = "  ⚠ below-3" if v.get("flag_below_3") else ""
        print(f"{r.case_id:4} {r.validator_status:9} {r.production_model:20} "
              f"{v.get('c1_quotability')}  {v.get('c3_tone')}  {v.get('c4_conditionals')}  {v.get('c5_length_format')}{flag}")
    print(f"\nmeans: {s['criterion_means']}  (over {s['measured']}/{s['n']} measured)")
    rule = "0 below-3" if s["strict"] else "<=10% below-3"
    print(f"below-3 violations: {s['below_3_violations']}/{s['measured']} (rule: {rule})"
          f"  ·  validator(C2/C6) pass: {s['validator_pass']}")
    if s["assertion_failures"]:
        print(f"fixture assertions FAILED: {s['assertion_failures']}")
    # A conflict says the MEASUREMENT is suspect, not the briefing — print it loudly so a judge
    # defect never gets filed as a writer defect (as T4 nearly was on 2026-08-03).
    if s["judge_conflicts_validator"]:
        print(f"⚠ judge/validator CONFLICT on {s['judge_conflicts_validator']}: "
              f"deterministic O4 passed but the judge scored c4<3 — suspect the JUDGE, "
              f"not the briefing. Check the `output` column in {args.log}.")
    print(f"SHIP GATE [{s['gate']}]: {VERDICT_BANNER[s['verdict']]}")
    if s["verdict"] == "INCONCLUSIVE":
        print(f"  ↳ no quality failure in the {s['measured']} case(s) that ran, but "
              f"{len(s['errored'])} could not be measured — the prompt is NOT certified.\n"
              f"  ↳ re-run on fresh quota (the Monday 12:00 UTC slot exists for this).")

    log_verdicts(results, args.log,
                 timestamp=datetime.now().isoformat(timespec="seconds"),
                 prompt_version=prompt.PROMPT_VERSION)
    print(f"logged {len(results)} verdicts -> {args.log}")

    # Dead-man switch for the gate itself. Runbook 6 makes this regression the thing that
    # blocks a prompt ship — but it is only as good as its last run, and it silently stopped
    # running for five weeks (eval_log's last entry was v1.4.1 while prod ran v1.4.2). Only a
    # PASS pings success; a run that never happens trips the check's own cron grace and emails
    # hello@modeo.io. Never raises (PRD §397).
    #
    # INCONCLUSIVE also pings /fail and also exits nonzero — an uncertified prompt must never
    # read as green. Only the WORDING differs, and that is the whole point: blaming the prompt
    # for a 429 is how an alert channel earns the right to be ignored.
    observability.ping_check("regression", "success" if s["verdict"] == "PASS" else "fail")
    if s["verdict"] == "FAIL":
        observability.send_alert(
            f"Weekly regression FAILED on prompt {prompt.PROMPT_VERSION}: "
            f"{s['below_3_violations']}/{s['measured']} below-3, validator_pass={s['validator_pass']}"
            + (f", invalid: {s['invalid']}" if s["invalid"] else "")
        )
    elif s["verdict"] == "INCONCLUSIVE":
        observability.send_alert(
            f"Weekly regression INCONCLUSIVE on prompt {prompt.PROMPT_VERSION}: only "
            f"{s['measured']}/{s['n']} cases measured (errored: {s['errored']}). No quality "
            f"failure in what ran — this is a measurement gap, not a regression. "
            f"Prompt NOT certified; re-run on fresh quota."
        )
    return 0 if s["verdict"] == "PASS" else 1


if __name__ == "__main__":
    import sys
    raise SystemExit(main())
