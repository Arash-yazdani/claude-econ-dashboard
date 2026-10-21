"""Back-test the LOCKED regime classifier (v1.2) against ~10y of real FRED history.

Phase-0 build carry-forward #1 (the #1 D9 validation task) — kept as the ongoing gate:
re-run it whenever the thresholds change (e.g. at an O3 recalibration) to confirm the
labels don't oscillate or mislabel obvious periods.

    FRED_API_KEY=... python scripts/backtest_thresholds.py --years 12

Reconstructs each month's indicator set (daily series aggregated to month-end, quarterly
GDP forward-filled), runs the classifier statefully (recovery hysteresis needs the prior
label), and prints the regime distribution, transition count, and driving values at each
transition. The v1.0→v1.1 before/after that motivated the current thresholds is frozen in
docs/classifier/regime-classifier-v1.1-proposal.md.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from dataclasses import dataclass
from typing import Optional

from macro_pipeline._env import load_dotenv
from macro_pipeline.classifier import classify
from macro_pipeline.config import INDICATORS
from macro_pipeline.fred_client import FredClient, FredError
from macro_pipeline.models import IndicatorReading

MONTHLY_LIMIT = 220  # ~18y of monthly points, trimmed to --years below
ALL_REGIMES = ("stagflation_risk", "slowdown", "recovery", "late_cycle_expansion", "soft_landing")


@dataclass
class Row:
    month: str
    readings: list[IndicatorReading]
    core: Optional[float]
    gdp: Optional[float]
    unemp: Optional[float]
    d3m: Optional[float]


def _month_key(iso: str) -> str:
    return iso[:7]  # "YYYY-MM"


def _is_fred_series(series_id: str) -> bool:
    """FRED ids are alphanumeric, <=25 chars. Non-FRED sources carry a SYNTHETIC id that only
    their own client recognises (gold_spot -> "XAU_SPOT_GOLDAPI", matched by GoldSpotSource).

    Asking FRED for one is a hard 400, and it took the whole back-test down with it: this script
    died on every invocation from 2026-07-21 — the day gold_spot was added — until 2026-08-06.
    Sixteen days in which the LOCKED classifier's own D9 validation gate could not run, and
    nothing noticed, because nothing watches the watcher.
    """
    return series_id.isalnum() and len(series_id) <= 25


def _build_monthly(client: FredClient) -> dict[str, dict[str, float | None]]:
    """series_id -> {"YYYY-MM": value}. Daily series aggregated to month-end (eop)."""
    out: dict[str, dict[str, float | None]] = {}
    for spec in INDICATORS:
        if not _is_fred_series(spec.fred_series_id):
            continue  # non-FRED source; the classifier reads none of them (all core-tier)
        freq = "m" if spec.frequency.value == "daily" else None
        agg = "eop" if freq else None
        res = client.fetch_series(spec.fred_series_id, spec.units, MONTHLY_LIMIT, freq, agg)
        out[spec.id] = {_month_key(o.date): o.value for o in res.observations}
    return out


def _value_asof(series: dict[str, float | None], months: list[str], i: int) -> float | None:
    """Forward-fill: latest non-null value at or before months[i] (covers quarterly GDP)."""
    for j in range(i, -1, -1):
        v = series.get(months[j])
        if v is not None:
            return v
    return None


def _build_rows(monthly: dict[str, dict[str, float | None]], years: int) -> list[Row]:
    all_months = sorted({m for s in monthly.values() for m in s})
    months = all_months[-(years * 12):]
    rows: list[Row] = []
    for i, m in enumerate(months):
        # .get(): a non-FRED spec has no history here, so it reads as None for every month —
        # which is what the classifier already tolerates for a missing row. Indexing directly
        # would just move the crash from _build_monthly to here.
        vals = {spec.id: _value_asof(monthly.get(spec.id, {}), months, i) for spec in INDICATORS}
        d3m = None
        if i >= 3 and vals["unemployment"] is not None:
            prior = _value_asof(monthly["unemployment"], months, i - 3)
            d3m = round(vals["unemployment"] - prior, 4) if prior is not None else None
        umich_d = None
        if i >= 1 and vals["umich_sentiment"] is not None:
            prior = _value_asof(monthly["umich_sentiment"], months, i - 1)
            umich_d = round(vals["umich_sentiment"] - prior, 4) if prior is not None else None
        readings = [
            IndicatorReading(
                id=spec.id, display_name=spec.display_name, value=vals[spec.id],
                unit=spec.unit, period_label=m, last_release_date=m + "-01",
                delta_3m=d3m if spec.id == "unemployment" else None,
                delta=umich_d if spec.id == "umich_sentiment" else None,
            )
            for spec in INDICATORS
        ]
        rows.append(Row(m, readings, vals["core_cpi_yoy"], vals["real_gdp_yoy"],
                        vals["unemployment"], d3m))
    return rows


def _fmt(v: Optional[float]) -> str:
    return "  na" if v is None else f"{v:5.1f}"


def run(years: int) -> int:
    load_dotenv()
    try:
        client = FredClient()
        monthly = _build_monthly(client)
    except FredError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    rows = _build_rows(monthly, years)
    labels: list[str] = []
    rules: list[str] = []
    prev: Optional[str] = None
    for r in rows:
        res = classify(r.readings, prev)
        prev = res.regime
        labels.append(prev)
        rules.append(res.rule)
    transitions = [k for k in range(1, len(labels)) if labels[k] != labels[k - 1]]

    print(f"Back-test {rows[0].month}..{rows[-1].month}  ({len(rows)} months, locked v1.2)\n")
    print("Regime distribution:")
    counts = Counter(labels)
    for label in ALL_REGIMES:
        n = counts.get(label, 0)
        flag = "  ⚠️ never fired — branch unexercised by history" if n == 0 else ""
        print(f"  {label:22} {n:3}  ({n / len(labels):.0%}){flag}")

    # W1 provenance. soft_landing is emitted by BOTH a real rule and the catch-all else, so the
    # distribution above cannot distinguish "the economy matched the soft-landing band" from "no
    # rule described this month and the classifier fell through to the most benign label". Bruce
    # asked exactly this question. Print the split so the answer is a number, not an assurance.
    rule_counts = Counter(rules)
    print("\nWhich branch fired (W1):")
    for rule, n in rule_counts.most_common():
        note = "  <-- CATCH-ALL: no rule matched; label is the default, not a finding" \
            if rule == "fallthrough" else ""
        print(f"  {rule:22} {n:3}  ({n / len(rules):.0%}){note}")
    sl = sum(1 for lab, ru in zip(labels, rules) if lab == "soft_landing")
    sl_fall = sum(1 for lab, ru in zip(labels, rules) if lab == "soft_landing" and ru == "fallthrough")
    if sl:
        print(f"\n  of {sl} soft_landing months, {sl_fall} ({sl_fall / sl:.0%}) are FALLTHROUGH, "
              f"not a matched rule")

    print(f"\nTransitions: {len(transitions)}  (~{len(transitions) / max(years, 1):.1f}/yr)"
          f"   [cols: core / gdpYoY / unemp / Δ3m-unemp]")
    for k in transitions:
        r = rows[k]
        print(f"  {r.month}: {labels[k - 1]:>20} -> {labels[k]:<20}  "
              f"core={_fmt(r.core)} gdp={_fmt(r.gdp)} unemp={_fmt(r.unemp)} d3m={_fmt(r.d3m)}")
    if len(transitions) > years * 4:
        print("\n⚠️  OSCILLATION SMELL: >4 transitions/yr — thresholds likely need damping.")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Back-test regime classifier thresholds (carry-forward #1)")
    p.add_argument("--years", type=int, default=12)
    return run(p.parse_args(argv).years)


if __name__ == "__main__":
    raise SystemExit(main())
