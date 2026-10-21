"""backtest_thresholds.py — the LOCKED classifier's D9 validation gate (offline, no network).

WHY THIS FILE EXISTS. The back-test died on every invocation from 2026-07-21 (the day gold_spot
was added) to 2026-08-06, and nobody noticed for sixteen days. Gold comes from gold-api.com, so
its spec carries a SYNTHETIC fred_series_id ("XAU_SPOT_GOLDAPI") that only GoldSpotSource
recognises; the script asked FRED for every spec indiscriminately and FRED answered 400.

Nothing caught it because the script is manual, unscheduled, and had no tests — the classifier's
own watcher, unwatched. These tests are the cheap guard: they pin the skip logic and the
missing-series tolerance without touching the network, so the next non-FRED indicator cannot
silently break the gate again.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from macro_pipeline.config import INDICATORS

ROOT = Path(__file__).resolve().parents[1]


def _load():
    """Import the script by path — scripts/ is not a package.

    Registered in sys.modules BEFORE exec_module: the script defines a @dataclass, and
    dataclasses resolves annotations via sys.modules[cls.__module__], which is None for a
    module that is still mid-import.
    """
    name = "backtest_thresholds"
    spec = importlib.util.spec_from_file_location(
        name, ROOT / "scripts" / "backtest_thresholds.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


bt = _load()


# ── the skip that fixes the crash ────────────────────────────────────────────────

def test_synthetic_non_fred_ids_are_not_sent_to_fred():
    """The exact 400: FRED ids are alphanumeric and <=25 chars; an underscore is neither."""
    assert bt._is_fred_series("DGS10") is True
    assert bt._is_fred_series("GFDEBTN") is True
    assert bt._is_fred_series("XAU_SPOT_GOLDAPI") is False      # gold_spot, ADR-006
    assert bt._is_fred_series("A" * 26) is False


def test_every_current_indicator_is_classified_correctly():
    """Pinned against the REAL config, so adding a non-FRED source updates this deliberately
    rather than by surprise. gold_spot is the only one today."""
    non_fred = [s.id for s in INDICATORS if not bt._is_fred_series(s.fred_series_id)]
    assert non_fred == ["gold_spot"]


def test_the_classifier_reads_no_non_fred_indicator():
    """The skip is only safe because the classifier consumes core-tier FRED rows exclusively.
    If a future non-FRED indicator ever became a classifier input, skipping it would silently
    change the labels rather than crash — far worse than the bug this replaced."""
    non_fred = {s.id for s in INDICATORS if not bt._is_fred_series(s.fred_series_id)}
    classifier_inputs = {"core_cpi_yoy", "real_gdp_yoy", "unemployment",
                         "treasury_10y", "fed_funds", "umich_sentiment"}
    assert non_fred & classifier_inputs == set()


# ── the second crash, one layer down ─────────────────────────────────────────────

def _monthly(months: list[str]) -> dict[str, dict[str, float]]:
    """A FRED-shaped history for every spec EXCEPT the non-FRED ones — i.e. exactly what
    _build_monthly now returns."""
    base = {"core_cpi_yoy": 2.5, "real_gdp_yoy": 2.0, "unemployment": 4.2,
            "treasury_10y": 4.5, "fed_funds": 3.5, "umich_sentiment": 70.0}
    out = {}
    for s in INDICATORS:
        if not bt._is_fred_series(s.fred_series_id):
            continue
        out[s.id] = {m: base.get(s.id, 1.0) for m in months}
    return out


def test_build_rows_tolerates_a_spec_with_no_history():
    """Skipping gold_spot in _build_monthly would only MOVE the KeyError here, because
    _build_rows iterates all of INDICATORS. It must read as None, which is what the
    classifier already tolerates for any missing row."""
    months = [f"2026-{m:02d}" for m in range(1, 7)]
    rows = bt._build_rows(_monthly(months), years=1)
    assert len(rows) == len(months)
    last = {r.id: r.value for r in rows[-1].readings}
    assert last["gold_spot"] is None            # absent series -> None, no crash
    assert last["unemployment"] == 4.2          # present series unaffected


def test_build_rows_still_computes_the_classifier_inputs():
    """Guard against 'fixed the crash, broke the measurement' — d3m is what recovery keys on."""
    months = [f"2026-{m:02d}" for m in range(1, 7)]
    monthly = _monthly(months)
    monthly["unemployment"] = dict(zip(months, [4.8, 4.7, 4.6, 4.5, 4.4, 4.3]))
    rows = bt._build_rows(monthly, years=1)
    assert rows[-1].unemp == 4.3
    assert rows[-1].d3m == pytest.approx(4.3 - 4.6)   # vs three months prior
    assert rows[-1].core == 2.5 and rows[-1].gdp == 2.0
