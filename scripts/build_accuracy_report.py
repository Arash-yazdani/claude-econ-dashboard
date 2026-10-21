"""Bruce's data-accuracy report (W6b) — a controls-testing memo, not a dashboard.

    python scripts/build_accuracy_report.py                    # accuracy-report-<date>.html
    python scripts/build_accuracy_report.py --format email     # plain text for AOL

He asked one question: can he trust the numbers. He ran accounting-PROCESS engagements, so the
answer is shaped like the memos he wrote for thirty years — population, sample, exceptions,
scope limitations — not like an ops dashboard. (`dashboard.render_markdown` is deliberately NOT
reused here: O-check rates, p95 latency and κ are operator language, not his.)

Reads `logs/reconciliation.jsonl` (the per-row evidence W6a persists on every PM eval),
`public/indicators.json`, and `derived.DERIVED`. Never `daily_eval.csv`. The verdict comes
first, the caveats are verbatim and load-bearing, and the report never overstates: "verified"
counts what was reachable, and an empty exceptions table is the good outcome.
"""

from __future__ import annotations

import argparse
import html as html_mod
import json
from datetime import date
from pathlib import Path
from typing import Any, Optional

RECONCILIATION_LOG = Path("logs/reconciliation.jsonl")
INDICATORS_JSON = Path("public/indicators.json")

# The three caveats. VERBATIM AND TESTED — a report that quietly drops its scope limitations is
# the exact overstatement a controls reviewer is trained to distrust, so tests assert on these
# strings and deleting one fails the build.
CAVEATS = (
    '"Verified" counts only what we could reach. If every source were down, this report '
    'would say "0 verified" and still not say "failed." Read the count, not the word.',
    "Only the 10 most recent observations per source are re-checked on each pass.",
    "Three rows are computed, not downloaded — the 10Y–2Y spread, real fed funds, and the "
    "real 10Y. Each is arithmetic on two checked numbers, so they inherit that accuracy but "
    "are not themselves reconciled against a source.",
)

VERDICT_LABEL = {"matched": "matched", "mismatched": "MISMATCHED", "not_reconciled": "not re-checked"}


def load_latest(path: Path = RECONCILIATION_LOG) -> Optional[dict[str, Any]]:
    try:
        lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
        return json.loads(lines[-1]) if lines else None
    except (OSError, json.JSONDecodeError):
        return None


def history_days(path: Path = RECONCILIATION_LOG, days: int = 30) -> tuple[int, int, int]:
    """(distinct dates, total mismatches, total checked) over the last `days` calendar days.

    DISTINCT DATES, not lines: reconciliation runs only on the --reconcile (PM) path, and a
    re-run day would otherwise double-count itself into a longer 'clean streak' than happened.
    """
    try:
        lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    except OSError:
        return (0, 0, 0)
    entries = [json.loads(ln) for ln in lines]
    if not entries:
        return (0, 0, 0)
    newest = max(e["timestamp"][:10] for e in entries)
    # by-date, newest entry wins for a re-run day
    by_date: dict[str, dict] = {}
    for e in entries:
        d = e["timestamp"][:10]
        if (date.fromisoformat(newest) - date.fromisoformat(d)).days < days:
            by_date[d] = e
    mism = sum(sum(1 for r in e["rows"] if r.get("verdict") == "mismatched") for e in by_date.values())
    checked = sum(e.get("checked", 0) for e in by_date.values())
    return (len(by_date), mism, checked)


def freshness_example(indicators_path: Path = INDICATORS_JSON) -> str:
    """The freshness-vs-lag paragraph, built from the LIVE unemployment row rather than a
    hardcoded example that would itself go stale — in the accuracy report, of all places."""
    try:
        rows = json.loads(indicators_path.read_text(encoding="utf-8")).get("indicators", [])
        u = next(r for r in rows if r["id"] == "unemployment")
        return (f"Every number is the latest that exists — and \"latest\" is not \"today.\" "
                f"Unemployment reads {u['value']}% : that is the {u['period_label']} figure, "
                f"published {u['last_release_date']}. It is correct, current, and an observation "
                f"about a month that has already ended. Economic data works on a lag; the report "
                f"checks that we carry the newest published value, not that the world has no lag.")
    except Exception:  # noqa: BLE001
        return ("Every number is the latest that exists — and \"latest\" is not \"today.\" "
                "Economic data works on a lag; the report checks that we carry the newest "
                "published value, not that the world has no lag.")


def summarize_rows(entry: dict[str, Any]) -> dict[str, Any]:
    rows = entry.get("rows", [])
    return {
        "rows": rows,
        "checked": entry.get("checked", 0),
        "matched": [r for r in rows if r.get("verdict") == "matched"],
        "mismatched": [r for r in rows if r.get("verdict") == "mismatched"],
        "not_reconciled": [r for r in rows if r.get("verdict") == "not_reconciled"],
        "when": entry.get("timestamp", ""),
        "as_of": entry.get("as_of_date", ""),
    }


def verdict_line(s: dict[str, Any]) -> str:
    if s["mismatched"]:
        ids = ", ".join(r["id"] for r in s["mismatched"])
        return (f"{len(s['mismatched'])} of {s['checked']} checked numbers did NOT match the "
                f"source ({ids}). Details in the exceptions table.")
    return (f"All {s['checked']} numbers we could re-check matched the source exactly, "
            f"at the same observation date.")


# ── HTML (print-friendly; same Georgia/letter conventions as the review sheet) ──────


def render(s: dict[str, Any]) -> str:
    d30, mism30, checked30 = history_days()
    exc_rows = "".join(
        f"<tr><td>{html_mod.escape(r['id'])}</td><td>{r.get('as_of') or ''}</td>"
        f"<td>{r.get('published')}</td><td>{r.get('source')}</td>"
        f"<td>{html_mod.escape(r.get('note') or '')}</td></tr>"
        for r in s["mismatched"])
    all_rows = "".join(
        f"<tr><td>{html_mod.escape(r['id'])}</td><td>{r.get('as_of') or ''}</td>"
        f"<td>{r.get('published') if r.get('published') is not None else '—'}</td>"
        f"<td>{r.get('source') if r.get('source') is not None else '—'}</td>"
        f"<td>{VERDICT_LABEL.get(r.get('verdict'), r.get('verdict'))}</td>"
        f"<td>{html_mod.escape(r.get('note') or '')}</td></tr>"
        for r in s["rows"])
    caveats = "".join(f"<li>{html_mod.escape(c)}</li>" for c in CAVEATS)
    return f"""<!doctype html>
<meta charset="utf-8">
<title>Data accuracy report — {s['as_of']}</title>
<style>
  @page {{ size: letter; margin: 14mm; }}
  body {{ font: 15px/1.5 Georgia, "Times New Roman", serif; color: #111; margin: 0; }}
  h1 {{ font-size: 20px; margin: 0 0 2px; }}
  h2 {{ font-size: 15px; margin: 18px 0 6px; }}
  .sub {{ font-size: 13px; color: #555; margin: 0 0 14px; }}
  .verdict {{ font-size: 17px; font-weight: 600; border-left: 4px solid
              {'#b3261e' if s['mismatched'] else '#2e7d32'}; padding: 6px 10px;
              background: #f7f7f5; }}
  table {{ border-collapse: collapse; width: 100%; font-size: 13px; }}
  th, td {{ border: 1px solid #ccc; padding: 4px 7px; text-align: left; }}
  th {{ background: #f0efec; font-weight: 600; }}
  .cap {{ font-size: 12px; color: #555; font-style: italic; margin: 4px 0 0; }}
  ol li {{ margin-bottom: 6px; }}
  p {{ margin: 8px 0; }}
</style>
<h1>Data accuracy report</h1>
<p class="sub">Data through {s['as_of']} · verification run {s['when'][:16].replace('T', ' ')}
 · prepared for Bruce</p>

<p class="verdict">{html_mod.escape(verdict_line(s))}</p>

<h2>What "checked" means</h2>
<p>Each number the dashboard published is re-downloaded from its source (FRED, the U.S. Treasury,
or the gold price service) and compared <b>at the same observation date</b> — did the source still
say what we said, for the period we said it about. It does not compare against the source's newest
value: sources gain newer observations between our publish and this check, and that is freshness,
not inaccuracy.</p>

<h2>Exceptions</h2>
{"<table><tr><th>Indicator</th><th>As of</th><th>We published</th><th>Source says</th><th>Note</th></tr>" + exc_rows + "</table>" if s['mismatched'] else "<p><b>None.</b></p>"}
<p class="cap">An empty table is the good outcome — it means nothing we published disagreed with
its source.</p>

<h2>Every published indicator</h2>
<table>
<tr><th>Indicator</th><th>As of</th><th>Published</th><th>Source</th><th>Result</th><th>Note</th></tr>
{all_rows}
</table>

<h2>Scope limitations</h2>
<ol>{caveats}</ol>

<h2>Freshness vs. lag</h2>
<p>{html_mod.escape(freshness_example())}</p>

<h2>Cadence, and what happens on a disagreement</h2>
<p>The verification runs automatically every evening; this report is generated on request and
reviewed monthly. A mismatch pages the developer the same evening (the "Data Sources" alert),
and the affected number is corrected or marked stale before the next morning's briefing —
a disagreement is never silently carried forward.</p>

<p><b>Last {max(d30, 1)} day{'s' if d30 != 1 else ''} on record:</b> {mism30} mismatch{'es' if mism30 != 1 else ''}
across {checked30} checks.</p>
"""


# ── plain text (his channel) ────────────────────────────────────────────────────────


def render_email(s: dict[str, Any]) -> str:
    d30, mism30, checked30 = history_days()
    lines = [
        f"DATA ACCURACY REPORT — data through {s['as_of']}",
        "",
        verdict_line(s),
        "",
        "WHAT \"CHECKED\" MEANS",
        "Each number the dashboard published is re-downloaded from its source",
        "(FRED, the U.S. Treasury, or the gold price service) and compared AT THE",
        "SAME OBSERVATION DATE — did the source still say what we said, for the",
        "period we said it about. Not against the source's newest value: sources",
        "gain newer observations after we publish, and that is freshness, not",
        "inaccuracy.",
        "",
        "EXCEPTIONS",
    ]
    if s["mismatched"]:
        for r in s["mismatched"]:
            lines.append(f"  {r['id']}: we published {r.get('published')}, source says "
                         f"{r.get('source')} (as of {r.get('as_of')})")
    else:
        lines.append("  None. An empty list is the good outcome.")
    lines += ["", "EVERY PUBLISHED INDICATOR"]
    for r in s["rows"]:
        pub = r.get("published") if r.get("published") is not None else "—"
        lines.append(f"  {r['id']:22} {str(pub):>10}   {VERDICT_LABEL.get(r.get('verdict'), '')}"
                     + (f"  ({r['note']})" if r.get("note") else ""))
    lines += ["", "SCOPE LIMITATIONS"]
    for i, c in enumerate(CAVEATS, 1):
        lines.append(f"  {i}. {c}")
    lines += ["", "FRESHNESS VS. LAG", freshness_example(), "",
              f"LAST {max(d30, 1)} DAY(S) ON RECORD: {mism30} mismatches across {checked30} checks.",
              "",
              "The verification runs automatically every evening. A disagreement pages",
              "the developer the same evening and is corrected before the next morning's",
              "briefing — never silently carried forward.", ""]
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Build Bruce's data-accuracy report")
    ap.add_argument("--format", choices=("html", "email"), default="html")
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)

    entry = load_latest()
    if entry is None:
        print("ERROR: no reconciliation history yet — run "
              "`python scripts/run_daily_eval.py --reconcile` first")
        return 1
    s = summarize_rows(entry)
    ext = "txt" if args.format == "email" else "html"
    out = Path(args.out or f"accuracy-report-{s['as_of']}.{ext}")
    out.write_text(render_email(s) if args.format == "email" else render(s), encoding="utf-8")
    print(f"{verdict_line(s)}")
    print(f"-> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
