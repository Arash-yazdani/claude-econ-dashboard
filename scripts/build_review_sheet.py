"""Bruce's printable review sheet — the Layer-3 human annotation round (SCRUM-85).

    python scripts/build_review_sheet.py --round d1
    python scripts/build_review_sheet.py --round d7  --out review-d7.html
    python scripts/build_review_sheet.py --round d30

Reads the published briefings out of logs/runs.jsonl and renders ONE printable page. Bruce writes
two numbers and a sentence per briefing; Arash transcribes them into the human_c1 / human_c3 /
human_note columns already sitting empty in logs/daily_eval.csv.

WHY A SHEET AND NOT A LOGIN. The LangSmith Developer tier is one seat (SO9: no card, ever), so
Bruce cannot have an account. A direct-write web form would remove the transcription step but
needs a backend, and the whole architecture is a static PWA with no server. Paper wins.

WHY THE ASK IS THIS SMALL. Two numbers, 1-5, plus a free-text note — exactly PRD §269, and the
minimum that yields a kappa. Bruce is the customer, not an evaluator; every extra field is a
reason for the round not to come back. Arash scores the 12 CTQ criteria separately, nine of which
auto-fill from machine checks.

WHAT IS DELIBERATELY WITHHELD. The judge's own C1/C3 scores never appear on the sheet. Showing
them would anchor Bruce to the model's answer and quietly destroy the very agreement statistic
the round exists to produce. Same reason the regime label and the machine verdict are absent: he
should react to the briefing exactly as he does on his phone.

Sample sizes are front-loaded 2 / 4 / 6 (his call, 2026-08-04): a badly-calibrated judge surfaces
on day one when it is cheap to fix, not after a month of misaligned scores.
"""

from __future__ import annotations

import argparse
import html
import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Optional

# round -> (sample size, lookback days). The window matters as much as the size: a "D+1" round
# must ask about the briefings he could actually have read yesterday, not a spread across the
# whole log.
ROUNDS = {"d1": (2, 2), "d7": (4, 7), "d30": (6, 30)}
RUNS_LOG = Path("logs/runs.jsonl")


def load_published(path: Path) -> list[dict[str, Any]]:
    """Every audited run that actually published a live LLM briefing, oldest first.

    A fallback/refusal row is skipped: it is a correct behaviour under an outage, but it is not a
    briefing and asking Bruce to rate its quotability would be noise in the kappa.
    """
    out: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue  # a truncated tail line must not sink the sheet
        if r.get("source") == "llm" and isinstance(r.get("output"), dict) \
                and r["output"].get("headline"):
            out.append(r)
    return out


def within_window(runs: list[dict[str, Any]], days: int) -> list[dict[str, Any]]:
    """Runs from the last `days` calendar days of the log (by the log's own newest date, not the
    wall clock — so a sheet built from an archived log still makes sense)."""
    if not runs:
        return []
    newest = max(_fmt_when(r.get("timestamp", "")) for r in runs)
    try:
        cutoff = (date.fromisoformat(newest) - timedelta(days=days - 1)).isoformat()
    except ValueError:
        return runs
    return [r for r in runs if _fmt_when(r.get("timestamp", "")) >= cutoff]


def sample(runs: list[dict[str, Any]], n: int, days: int) -> list[dict[str, Any]]:
    """Pick n briefings from the round's window, preferring DISTINCT headlines.

    Variety is not cosmetic here. 94 of the first 104 published briefings carried the identical
    headline (the regime never left soft_landing), so a naive "spread them out" sample hands Bruce
    the same sentence twice and the two ratings that come back are one rating. Twelve paired items
    is already thin for a kappa; twelve paired DUPLICATES would measure nothing at all.

    So: one representative per distinct headline first, newest of each, then fill from the rest.
    Deterministic — the same log must always produce the same sheet, or a reprint mid-round would
    quietly change what Bruce was asked to rate.
    """
    pool = within_window(runs, days) or runs
    if len(pool) <= n:
        return pool

    seen: set[str] = set()
    distinct: list[dict[str, Any]] = []
    for r in reversed(pool):  # newest first, so each headline is represented by its latest use
        h = str(r["output"].get("headline") or "")
        if h not in seen:
            seen.add(h)
            distinct.append(r)
    distinct.reverse()

    chosen = _spread(distinct, n)
    if len(chosen) < n:  # not enough distinct headlines — top up with the newest remainder
        ids = {id(r) for r in chosen}
        chosen += [r for r in reversed(pool) if id(r) not in ids][: n - len(chosen)]
    return sorted(chosen, key=lambda r: r.get("timestamp", ""))


def _spread(runs: list[dict[str, Any]], n: int) -> list[dict[str, Any]]:
    """n items spaced evenly across the list (keeps a round from clustering on one day)."""
    if len(runs) <= n:
        return list(runs)
    step = (len(runs) - 1) / max(n - 1, 1)
    return [runs[round(i * step)] for i in range(n)]


def _fmt_when(ts: str) -> str:
    return ts[:10] if len(ts) >= 10 else ts


def render(runs: list[dict[str, Any]], round_name: str) -> str:
    cards = []
    for i, r in enumerate(runs, 1):
        o = r["output"]
        lines = "".join(
            f'<li>{html.escape(q.get("sentence", ""))}</li>'
            for q in (o.get("quotable_numbers") or [])
        )
        extra = o.get("regime_change") or o.get("whats_new")
        extra_html = f'<p class="extra">{html.escape(str(extra))}</p>' if extra else ""
        cards.append(f"""
    <section class="card">
      <div class="meta">#{i} &middot; {html.escape(_fmt_when(r.get("timestamp", "")))}
        &middot; {html.escape(str(r.get("run_label", "")))}</div>
      <p class="headline">{html.escape(str(o.get("headline") or ""))}</p>
      <ul class="numbers">{lines}</ul>
      {extra_html}
      <div class="rate">
        <div class="q">
          <span class="label">Could you say this out loud to a friend, as written?</span>
          <span class="scale">{_boxes()}</span>
        </div>
        <div class="q">
          <span class="label">Does it sound confident and plainspoken &mdash; not hedgy?</span>
          <span class="scale">{_boxes()}</span>
        </div>
        <div class="note"><span class="label">Anything you'd change:</span>
          <span class="rule"></span></div>
      </div>
    </section>""")

    return f"""<!doctype html>
<meta charset="utf-8">
<title>Daily Macro Briefing &mdash; review {round_name.upper()}</title>
<style>
  @page {{ size: letter; margin: 14mm; }}
  * {{ box-sizing: border-box; }}
  body {{ font: 16px/1.5 Georgia, "Times New Roman", serif; color: #111; margin: 0; }}
  h1 {{ font-size: 20px; margin: 0 0 2px; }}
  .sub {{ font-size: 13px; color: #555; margin: 0 0 16px; }}
  .card {{ border: 1px solid #bbb; border-radius: 6px; padding: 12px 14px; margin: 0 0 12px;
           page-break-inside: avoid; }}
  .meta {{ font: 12px/1.4 system-ui, sans-serif; color: #666; margin-bottom: 6px; }}
  .headline {{ font-size: 19px; font-weight: 600; margin: 0 0 8px; }}
  .numbers {{ margin: 0 0 8px; padding-left: 20px; font-size: 15px; }}
  .numbers li {{ margin-bottom: 2px; }}
  .extra {{ font-size: 14px; color: #444; margin: 0 0 8px; font-style: italic; }}
  .rate {{ border-top: 1px dashed #ccc; padding-top: 8px; margin-top: 4px; }}
  .q {{ display: flex; justify-content: space-between; align-items: center; gap: 12px;
        margin-bottom: 7px; }}
  .label {{ font: 14px/1.4 system-ui, sans-serif; color: #222; }}
  .scale {{ white-space: nowrap; }}
  .box {{ display: inline-block; width: 25px; height: 25px; border: 1px solid #444;
          border-radius: 3px; margin-left: 5px; text-align: center;
          font: 11px/25px system-ui, sans-serif; color: #999; }}
  .note {{ display: flex; align-items: baseline; gap: 10px; margin-top: 9px; }}
  .rule {{ flex: 1; border-bottom: 1px solid #999; height: 15px; }}
  .foot {{ font: 12px/1.5 system-ui, sans-serif; color: #666; margin-top: 14px;
           border-top: 1px solid #ddd; padding-top: 8px; }}
</style>
<h1>Daily Macro Briefing &mdash; review sheet ({round_name.upper()})</h1>
<p class="sub">{len(runs)} briefing{"s" if len(runs) != 1 else ""}.
  1 = no, 5 = yes. Circle a number. Skip anything you have no view on.</p>
{"".join(cards)}
<p class="foot">Only these two questions &mdash; nothing else is needed from you.
  There are no right answers; your reaction <em>is</em> the measurement.</p>
"""


def _boxes() -> str:
    return "".join(f'<span class="box">{i}</span>' for i in (1, 2, 3, 4, 5))


def render_email(runs: list[dict[str, Any]], round_name: str) -> str:
    """The same round as PLAIN TEXT, for a reviewer who is not in the room.

    Plain text, not HTML, on purpose. He reads it on AOL webmail, where a styled layout is a
    coin flip — and more to the point, you cannot circle a number in an email. He has to TYPE,
    so every answer is a blank he types into after hitting Reply. Anything that needs him to
    open an attachment, print it, or use a browser is a round that does not come back.

    Same three withholdings as the printed sheet: no judge scores, no regime label, no machine
    verdict. Same reason — an anchored answer is worse than no answer.
    """
    lines = [
        "Bruce,",
        "",
        f"Two questions each on the last {len(runs)} briefings. It should take about "
        "two minutes.",
        "",
        "Just hit Reply and type a number 1-5 on each blank line. 1 = no, 5 = yes.",
        "Skip anything you have no view on -- a blank is a real answer.",
        "",
        "There are no right answers here. Your reaction IS the measurement.",
        "",
        "=" * 62,
    ]
    for i, r in enumerate(runs, 1):
        o = r["output"]
        lines += ["", f"BRIEFING {i} of {len(runs)}  --  {_fmt_when(r.get('timestamp', ''))}"
                      f" {r.get('run_label', '')}", ""]
        lines.append(f'  "{o.get("headline") or ""}"')
        for q in (o.get("quotable_numbers") or []):
            lines.append(f'  - {q.get("sentence", "")}')
        extra = o.get("regime_change") or o.get("whats_new")
        if extra:
            lines.append(f"  - {extra}")
        lines += [
            "",
            "  Could you say this out loud to a friend, as written?",
            "     1-5: ____",
            "",
            "  Does it sound confident and plainspoken -- not hedgy?",
            "     1-5: ____",
            "",
            "  Anything you'd change?",
            "     ____________________________________________",
            "",
            "=" * 62,
        ]
    lines += [
        "",
        "That's everything. Thank you --",
        "Arash",
    ]
    return "\n".join(lines) + "\n"


SUBJECTS = {
    "d1": "Two minutes? Quick read on this week's briefings",
    "d7": "Two minutes? Quick read on this week's briefings",
    "d30": "Two minutes? Quick read on the last month of briefings",
}


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Build Bruce's printable review sheet")
    ap.add_argument("--round", choices=sorted(ROUNDS), default="d1")
    ap.add_argument("--runs-log", default=str(RUNS_LOG))
    ap.add_argument("--out", default=None, help="default: review-<round>.<ext>")
    ap.add_argument("--format", choices=("html", "email"), default="html",
                    help="html = printable sheet; email = plain-text body for a remote reviewer")
    ap.add_argument("--n", type=int, default=None, help="override the round's sample size")
    ap.add_argument("--days", type=int, default=None, help="override the round's lookback window")
    args = ap.parse_args(argv)

    path = Path(args.runs_log)
    if not path.exists():
        print(f"ERROR: {path} not found", flush=True)
        return 1

    runs = load_published(path)
    default_n, default_days = ROUNDS[args.round]
    n = args.n if args.n is not None else default_n
    days = args.days if args.days is not None else default_days
    picked = sample(runs, n, days)
    if not picked:
        print("ERROR: no published briefings in the log yet", flush=True)
        return 1

    if args.format == "email":
        out = Path(args.out or f"review-{args.round}-email.txt")
        out.write_text(render_email(picked, args.round), encoding="utf-8")
        print(f"SUBJECT: {SUBJECTS[args.round]}\n")
    else:
        out = Path(args.out or f"review-{args.round}.html")
        out.write_text(render(picked, args.round), encoding="utf-8")
    print(f"{len(picked)} briefing(s) from {len(runs)} published -> {out}")
    for r in picked:
        print(f"  {_fmt_when(r.get('timestamp', '')):10} {r.get('run_label', ''):3} "
              f"{r['output'].get('headline', '')[:62]}")
    print("\nPrint it, hand it over, then transcribe into logs/daily_eval.csv "
          "(human_c1 / human_c3 / human_note).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
