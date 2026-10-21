"""Shared test fixtures: an in-memory FRED source so tests never hit the network."""

from __future__ import annotations

import copy
from datetime import date

import pytest

from macro_pipeline.fred_client import SeriesResult
from macro_pipeline.models import Observation

AS_OF = date(2026, 6, 19)


def obs(rows: list[tuple[str, float | None]]) -> list[Observation]:
    """Build observations (caller passes them newest-first, as FRED sort_order=desc)."""
    return [Observation(date=d, value=v) for d, v in rows]


# A plausible "steady state" dataset for all 7 series, keyed by FRED series_id.
# DGS10 has a trailing null (a weekend) to exercise the freshest-non-null pick.
def _base_series() -> dict[str, SeriesResult]:
    return {
        "CPIAUCNS": SeriesResult(obs([("2026-05-01", 2.9), ("2026-04-01", 2.9),
                                      ("2026-03-01", 2.8), ("2026-02-01", 2.7)]), "2026-06-11"),
        # core < 3.0 so the steady-state fixture is a genuine soft_landing under v1.1.
        "CPILFENS": SeriesResult(obs([("2026-05-01", 2.8), ("2026-04-01", 2.9),
                                      ("2026-03-01", 3.0), ("2026-02-01", 3.1)]), "2026-06-11"),
        "UNRATE":   SeriesResult(obs([("2026-05-01", 4.2), ("2026-04-01", 4.2),
                                      ("2026-03-01", 4.1), ("2026-02-01", 4.0)]), "2026-06-05"),
        "GDPC1":    SeriesResult(obs([("2026-01-01", 2.1), ("2025-10-01", 2.0),
                                      ("2025-07-01", 1.9), ("2025-04-01", 1.8)]), "2026-05-29"),
        "DGS10":    SeriesResult(obs([("2026-06-19", None), ("2026-06-18", 4.25),
                                      ("2026-06-17", 4.27), ("2026-06-16", 4.30)]), "2026-06-18"),
        "DFF":      SeriesResult(obs([("2026-06-18", 4.5), ("2026-06-17", 4.5),
                                      ("2026-06-16", 4.5), ("2026-06-15", 4.5)]), "2026-06-18"),
        "UMCSENT":  SeriesResult(obs([("2026-06-01", 67.5), ("2026-05-01", 66.8),
                                      ("2026-04-01", 66.0), ("2026-03-01", 65.0)]), "2026-06-13"),
        # V2 Tier-2 drawer series (display-only; values are post-FRED-transform, newest-first).
        "PAYEMS":   SeriesResult(obs([("2026-05-01", 150.0), ("2026-04-01", 165.0),
                                      ("2026-03-01", 142.0), ("2026-02-01", 180.0)]), "2026-06-06"),
        "DGS2":     SeriesResult(obs([("2026-06-18", 4.42), ("2026-06-17", 4.45),
                                      ("2026-06-16", 4.40), ("2026-06-15", 4.38)]), "2026-06-18"),
        "ICSA":     SeriesResult(obs([("2026-06-13", 221000.0), ("2026-06-06", 218000.0),
                                      ("2026-05-30", 225000.0), ("2026-05-23", 230000.0)]), "2026-06-18"),
        "CIVPART":  SeriesResult(obs([("2026-05-01", 62.6), ("2026-04-01", 62.5),
                                      ("2026-03-01", 62.5), ("2026-02-01", 62.4)]), "2026-06-06"),
        "MORTGAGE30US": SeriesResult(obs([("2026-06-12", 6.82), ("2026-06-05", 6.88),
                                          ("2026-05-29", 6.90), ("2026-05-22", 6.78)]), "2026-06-12"),
        "HOUST":    SeriesResult(obs([("2026-05-01", 1352.0), ("2026-04-01", 1361.0),
                                      ("2026-03-01", 1340.0), ("2026-02-01", 1378.0)]), "2026-06-18"),
        # Millions of dollars (GFDEBTN's native unit) — ~$35-36T, plausible current-era debt level.
        "GFDEBTN":  SeriesResult(obs([("2026-01-01", 36219500.0), ("2025-10-01", 35801200.0),
                                      ("2025-07-01", 35402100.0), ("2025-04-01", 34987600.0)]), "2026-05-29"),
        # V2 Tier-3 market series (display-only).
        "SP500":    SeriesResult(obs([("2026-06-18", 7500.58), ("2026-06-17", 7420.10),
                                      ("2026-06-16", 7460.0), ("2026-06-15", 7440.0)]), "2026-06-18"),
        "NASDAQCOM": SeriesResult(obs([("2026-06-18", 26517.93), ("2026-06-17", 26021.65),
                                       ("2026-06-16", 26200.0), ("2026-06-15", 26100.0)]), "2026-06-18"),
        "NCBEILQ027S": SeriesResult(obs([("2026-01-01", 69511628.0), ("2025-10-01", 68200000.0),
                                         ("2025-07-01", 66800000.0), ("2025-04-01", 65400000.0)]), "2026-03-12"),
        "BAMLH0A0HYM2": SeriesResult(obs([("2026-06-18", 3.42), ("2026-06-17", 3.50),
                                          ("2026-06-16", 3.45), ("2026-06-15", 3.48)]), "2026-06-18"),
        # V2 chip "raw level" overrides: a rate/change series fetched with units="lin" returns the
        # raw level behind it (keyed "<series>|lin"; the FakeFredSource prefers a units-specific key).
        "CPIAUCNS|lin":  SeriesResult(obs([("2026-05-01", 322.0), ("2026-04-01", 321.3)]), "2026-06-11"),
        "CPILFENS|lin":  SeriesResult(obs([("2026-05-01", 328.4), ("2026-04-01", 327.6)]), "2026-06-11"),
        "GDPC1|lin":     SeriesResult(obs([("2026-01-01", 23512.0), ("2025-10-01", 23390.0)]), "2026-05-29"),
        "PAYEMS|lin":    SeriesResult(obs([("2026-05-01", 159500.0), ("2026-04-01", 159350.0)]), "2026-06-06"),
        # V3 — sector + rates drawer + international/commodity market series (all "lin", display-only).
        "DFII10":   SeriesResult(obs([("2026-06-18", 2.23), ("2026-06-17", 2.20),
                                      ("2026-06-16", 2.18), ("2026-06-15", 2.21)]), "2026-06-18"),
        "TCU":      SeriesResult(obs([("2026-05-01", 76.17), ("2026-04-01", 76.0),
                                      ("2026-03-01", 75.9), ("2026-02-01", 75.8)]), "2026-06-15"),
        "MANEMP":   SeriesResult(obs([("2026-05-01", 12605.0), ("2026-04-01", 12600.0),
                                      ("2026-03-01", 12595.0), ("2026-02-01", 12590.0)]), "2026-06-05"),
        "USCONS":   SeriesResult(obs([("2026-05-01", 8337.0), ("2026-04-01", 8330.0),
                                      ("2026-03-01", 8325.0), ("2026-02-01", 8320.0)]), "2026-06-05"),
        "DTWEXBGS": SeriesResult(obs([("2026-06-12", 119.51), ("2026-06-11", 119.3),
                                      ("2026-06-10", 119.6), ("2026-06-09", 119.4)]), "2026-06-15"),
        "DCOILBRENTEU": SeriesResult(obs([("2026-06-15", 84.65), ("2026-06-12", 83.9),
                                          ("2026-06-11", 85.1), ("2026-06-10", 84.2)]), "2026-06-17"),
        # V2.1 — CBOE VIX (display-only market row, Charter Rev 3 / SCRUM-77).
        "VIXCLS":   SeriesResult(obs([("2026-06-18", 14.5), ("2026-06-17", 15.2),
                                      ("2026-06-16", 14.8), ("2026-06-15", 15.0)]), "2026-06-18"),
        # V2.2 — gold spot (ADR-006). Real gold-api.com responses carry exactly ONE observation
        # (no history endpoint on the free tier) — the fake mirrors that shape on purpose.
        "XAU_SPOT_GOLDAPI": SeriesResult(obs([("2026-06-18", 2350.5)]), "2026-06-18"),
    }


class FakeFredSource:
    """Implements the FredSource protocol from in-memory SeriesResults."""

    def __init__(self, series: dict[str, SeriesResult]):
        self._series = series
        self.calls: list[tuple[str, str, int]] = []

    def fetch_series(self, series_id: str, units: str, limit: int) -> SeriesResult:
        self.calls.append((series_id, units, limit))
        keyed = f"{series_id}|{units}"  # units-specific override (e.g. the "lin" raw level)
        if keyed in self._series:
            return self._series[keyed]
        if series_id not in self._series:
            raise KeyError(f"no fake series for {series_id}")
        return self._series[series_id]


@pytest.fixture
def base_series() -> dict[str, SeriesResult]:
    return copy.deepcopy(_base_series())


@pytest.fixture
def source(base_series) -> FakeFredSource:
    return FakeFredSource(base_series)
