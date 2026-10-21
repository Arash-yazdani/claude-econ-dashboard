"""CLI entrypoint:  python -m macro_pipeline [--as-of YYYY-MM-DD] [--out ...] ...

Runs B1 against the live FRED API (needs FRED_API_KEY in the environment or .env) and
writes public/indicators.json plus, optionally, the BriefingRequest data-half that B3
will hand to the LLM. Exit codes: 0 = ok, 2 = plausibility fallback, 1 = error.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from ._env import load_dotenv
from .fred_client import FredError
from .pipeline import DEFAULT_CACHE_PATH, DEFAULT_INDICATORS_PATH, run_pipeline
from .sources import build_source


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="macro-pipeline", description="B1 FRED data pipeline")
    parser.add_argument("--as-of", type=date.fromisoformat, default=None,
                        help="Run date (YYYY-MM-DD); defaults to today.")
    parser.add_argument("--cache", default=DEFAULT_CACHE_PATH)
    parser.add_argument("--out", default=DEFAULT_INDICATORS_PATH,
                        help="indicators.json output path (PWA feed).")
    parser.add_argument("--briefing-out", default=None,
                        help="Optional: write the BriefingRequest data-half JSON (B3 handoff).")
    args = parser.parse_args(argv)

    load_dotenv()
    try:
        # The shared source stack, not a bare FredClient — a bare client sends gold_spot's synthetic
        # id to FRED and 400s the whole run (this tool has 400'd since gold_spot shipped 2026-07-20).
        source = build_source()
        result = run_pipeline(
            source, as_of=args.as_of, cache_path=args.cache, write_indicators_to=args.out
        )
    except FredError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if not result.ok:
        print("PLAUSIBILITY FALLBACK — implausible values, LLM skipped:", file=sys.stderr)
        for f in result.rejected:
            print(f"  {f['id']}={f['value']} outside {f['valid_range']}", file=sys.stderr)
        return 2

    if args.briefing_out:
        Path(args.briefing_out).write_text(
            json.dumps(result.briefing_request(), indent=2), encoding="utf-8"
        )

    stale_note = " (some indicators STALE)" if result.any_stale else ""
    change = "" if result.regime_today == result.regime_yesterday else \
        f" (was {result.regime_yesterday})"
    print(f"OK {result.as_of_date}: regime={result.regime_today}{change}, "
          f"drivers={result.drivers}, {len(result.readings)} indicators, "
          f"{len(result.releases)} release(s){stale_note} -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
