"""B8 monitoring generator — turn the data files into docs/monitoring.md (PRD §397).

THIS SCRIPT OWNS docs/monitoring.md; no other agent writes that file. It reads the
file-based stack — logs/runs.jsonl (RunRecord lines) + eval_log.csv (judge verdicts) —
tolerantly, rolls them up via macro_pipeline.dashboard (pure aggregation, no I/O there),
and renders the live dashboard section ABOVE the static human runbook the docs agent owns.

    python scripts/build_monitoring.py --window-days 30 --now 2026-06-17

Exit code is the cron gate (PRD §397, "🔴 red widget => cron pauses"): 0 when GREEN,
3 when RED so a future cron step can `|| exit` and refuse to publish a fresh briefing.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import date, datetime
from pathlib import Path

from macro_pipeline.dashboard import build_metrics, render_markdown


def read_runs(path: Path) -> list[dict]:
    """Read logs/runs.jsonl tolerantly: missing file -> []; blank/corrupt lines skipped.

    Monitoring must never break on a half-written or truncated log line — a single bad
    record is dropped, not fatal, so a partial run still yields a dashboard (PRD §397).
    """
    if not path.exists():
        return []
    records: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            print(f"warning: skipping unparseable runs line in {path}", file=sys.stderr)
            continue
        if isinstance(obj, dict):
            records.append(obj)
    return records


def read_eval(path: Path) -> list[dict]:
    """Read eval_log.csv via csv.DictReader (PRD §401 judge panel); missing file -> []."""
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def build_document(metrics, runbook: Path, *, generated_at: str) -> str:
    """Compose the output file: header + live dashboard + the static runbook appendix.

    The runbook (docs/monitoring-runbook.md) is the docs agent's; we append it verbatim
    under the generated section so operators read one page. A missing runbook is tolerated
    with an inline placeholder rather than a crash.
    """
    section = render_markdown(metrics, generated_at=generated_at)
    parts = [
        "# Monitoring dashboard",
        "",
        section,
        "",
        "---",
        "",
    ]
    if runbook.exists():
        parts.append(runbook.read_text(encoding="utf-8").rstrip("\n"))
    else:
        parts.append(f"<!-- runbook not found at {runbook} — run the docs phase first. -->")
    return "\n".join(parts) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="B8 monitoring dashboard generator")
    parser.add_argument("--runs", default="logs/runs.jsonl")
    parser.add_argument("--eval", dest="eval_path", default="eval_log.csv")
    parser.add_argument("--runbook", default="docs/monitoring-runbook.md")
    parser.add_argument("--out", default="docs/monitoring.md")
    parser.add_argument("--window-days", type=int, default=30)
    parser.add_argument("--now", default=date.today().isoformat())
    args = parser.parse_args(argv)

    run_records = read_runs(Path(args.runs))
    eval_rows = read_eval(Path(args.eval_path))

    metrics = build_metrics(
        run_records, eval_rows, window_days=args.window_days, now_iso=args.now,
    )

    generated_at = datetime.now().isoformat(timespec="seconds")
    document = build_document(metrics, Path(args.runbook), generated_at=generated_at)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(document, encoding="utf-8")

    # Banner mirrors the dashboard STATUS line; exit 3 on RED is the cron pause gate (PRD §397).
    # Report the SCORED verdict count (aggregate_judge drops error rows) so stdout matches the doc.
    n_verdicts = metrics.judge.get("n_verdicts", 0)
    summary = f"wrote {out} ({len(run_records)} runs, {n_verdicts} verdicts)"
    if metrics.red:
        print(f"STATUS: RED — {'; '.join(metrics.red_reasons)}")
        print(summary)
        return 3
    if metrics.warn:
        # AMBER: worth a human look, but NOT a publish pause — the validator already contained it
        # and pausing would serve a stale card instead of the safe one (2026-08-25 incident).
        print(f"::warning::AMBER — {'; '.join(metrics.warn_reasons)}")
        print(f"STATUS: AMBER — {'; '.join(metrics.warn_reasons)}")
        print(summary)
        return 0
    print("STATUS: GREEN")
    print(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
