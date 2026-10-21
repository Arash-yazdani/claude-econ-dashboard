"""Replay the saved briefing requests through both LLM stacks and write docs/comparison.md.

Baseline: the original Economy-Dashboard stack (Gemini 2.5 Flash primary, Groq gpt-oss-120b
failover), frozen in macro_pipeline.legacy_llm_client. Candidate: Claude on Bedrock (Opus 5.5
primary, Haiku 5.5 failover). Corpus: the 12 locked regression requests in
tests/fixtures/regression-cases-v1.0.json, the same BriefingRequests the original repo's ship gate
replays (production rows in logs/runs.jsonl keep only an input hash, so they cannot be replayed).

Each output is scored by the deterministic B4 validator and by ONE judge (Claude Sonnet 5.5,
judge.make_judge()) for both stacks, through judge.run_regression, so both sides get identical
scoring. Caveat recorded in the report: the judge shares a model family with the candidate.

Default is --dry-run: prints the request count and an estimated token count, no API calls, no
env needed. --live spends money on Bedrock and free-tier Gemini/Groq quota, and refuses to start
unless AWS_REGION, GEMINI_API_KEY and GROQ_API_KEY are set. Never run --live near a briefing
window of the original deployment: the Gemini/Groq quota is shared with production.

    python scripts/compare_models.py              # dry run
    python scripts/compare_models.py --live       # real calls, writes docs/comparison.md
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from macro_pipeline import judge, llm_client, observability, prompt
from macro_pipeline._env import load_dotenv

CASES = Path("tests/fixtures/regression-cases-v1.0.json")
OUT = Path("docs/comparison.md")
REQUIRED_ENV = ("AWS_REGION", "GEMINI_API_KEY", "GROQ_API_KEY")
# Assumed output per writer call incl. thinking at effort low; per judge call at effort medium.
# Assumptions, not measurements: --live records the real numbers from response.usage.
ASSUMED_WRITER_OUTPUT_TOKENS = 600
ASSUMED_JUDGE_OUTPUT_TOKENS = 800


def approx_tokens(text: str) -> int:
    """Local approximation, ~4 characters per token. No API call. Check against count_tokens."""
    return max(1, round(len(text) / 4))


def estimate(cases: list[dict]) -> dict:
    writer_in = judge_in = 0
    for c in cases:
        m = prompt.build_messages(c["input"])
        writer_in += approx_tokens(m["system"]) + approx_tokens(m["user"])
        # Judge input ~ rubric + request + a briefing; the request's own user prompt stands in.
        judge_in += approx_tokens(judge.JUDGE_SYSTEM) + approx_tokens(m["user"])
    n = len(cases)
    return {
        "requests": n,
        "claude_writer_input": writer_in,
        "claude_writer_output_assumed": n * ASSUMED_WRITER_OUTPUT_TOKENS,
        "judge_calls": 2 * n,  # one per stack per case
        "judge_input": 2 * judge_in,
        "judge_output_assumed": 2 * n * ASSUMED_JUDGE_OUTPUT_TOKENS,
        "legacy_writer_input": writer_in,
    }


def missing_env() -> list[str]:
    return [k for k in REQUIRED_ENV if not os.environ.get(k, "").strip()]


def _row(stack: str, r) -> str:
    v = r.verdict
    scores = " | ".join(str(v.get(k, "")) for k in ("c1_quotability", "c3_tone",
                                                    "c4_conditionals", "c5_length_format"))
    return f"| {r.case_id} | {stack} | {r.production_model} | {r.validator_status} | {scores} |"


def write_report(results: dict, usage: list[dict], path: Path = OUT) -> None:
    lines = [
        "# Model comparison: original stack vs Claude on Bedrock",
        "",
        f"Generated {datetime.now().astimezone().isoformat(timespec='seconds')} by "
        f"`scripts/compare_models.py --live`. Prompt v{prompt.PROMPT_VERSION}, "
        f"{len(next(iter(results.values())))} regression requests per stack.",
        "",
        "Judge for both stacks: " + judge.make_judge().model + ". It shares a model family with the "
        "candidate writer; read judge scores with that bias in mind. Validator results are "
        "deterministic and carry no such bias.",
        "",
        "## Gate summary",
        "",
        "| stack | verdict | valid | measured | below-3 |",
        "|---|---|---|---|---|",
    ]
    for stack, rs in results.items():
        s = judge.summarize(rs)
        lines.append(f"| {stack} | {s.get('verdict')} | {sum(r.validator_status == 'valid' for r in rs)}"
                     f"/{len(rs)} | {s.get('measured', '')} | {s.get('below_3_violations', '')} |")
    lines += ["", "## Per case", "",
              "| case | stack | model | validator | c1 | c3 | c4 | c5 |", "|---|---|---|---|---|---|---|---|"]
    for case_idx in range(len(next(iter(results.values())))):
        for stack, rs in results.items():
            lines.append(_row(stack, rs[case_idx]))
    tin = sum(u.get("input_tokens") or 0 for u in usage)
    tout = sum(u.get("output_tokens") or 0 for u in usage)
    lines += ["", "## Claude usage (from response.usage)", "",
              f"{len(usage)} Claude calls (writer, failover and judge), {tin} input tokens, "
              f"{tout} output tokens.", ""]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", default=True,
                      help="default: count requests and estimate tokens, no API calls")
    mode.add_argument("--live", action="store_true", help="make real calls on both stacks")
    ap.add_argument("--pace", type=float, default=10.0, help="seconds between cases")
    args = ap.parse_args(argv)

    cases = json.loads(CASES.read_text(encoding="utf-8"))["cases"]
    est = estimate(cases)
    print(json.dumps(est, indent=2))
    if not args.live:
        print("dry run: no API calls made. Re-run with --live to spend quota and write "
              f"{OUT}.")
        return 0

    load_dotenv()
    missing = missing_env()
    if missing:
        print(f"refusing to run --live: missing env {', '.join(missing)}", file=sys.stderr)
        return 2

    from macro_pipeline import legacy_llm_client as legacy  # only importer of the legacy stack

    gemini, groq = legacy.GeminiClient(), legacy.GroqClient()
    opus, haiku = llm_client.make_writer_clients()
    judge_model, judge_failover = judge.make_judge(), judge.make_judge_failover()

    def legacy_for(req):
        res = legacy.generate_briefing(req, primary=gemini, failover=groq)
        return res.output, res.model

    def claude_for(req):
        res = llm_client.generate_briefing(req, primary=opus, failover=haiku)
        return res.output, res.model

    observability.reset_run_signals()
    results = {
        "original (Gemini/Groq)": judge.run_regression(
            cases, briefing_for=legacy_for, judge_model=judge_model,
            judge_failover=judge_failover, pace_seconds=args.pace),
        "Claude on Bedrock": judge.run_regression(
            cases, briefing_for=claude_for, judge_model=judge_model,
            judge_failover=judge_failover, pace_seconds=args.pace),
    }
    write_report(results, observability.drain_run_signals()["llm_usage"])
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
