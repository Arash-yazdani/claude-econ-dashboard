"""BLS Public Data API — graft the same-day 08:30 ET release tip onto FRED's BLS-origin series.

FRED re-publishes BLS releases (CPI, the jobs report) but ingests them ~10:10 ET, ~100 minutes
after BLS posts at 08:30 ET. On release mornings that lag is the difference between this month's
print and last month's (the 2026-08-12 incident). `BlsSource` wraps a `FredSource` and, for the
BLS-origin series, prepends BLS's own latest point when it is newer than FRED's — so CPI and the
jobs numbers read same-day, from the primary publisher.

FRED stays the source of record for history/backfill; we only add the freshest tip. The
registration key (BLS_API_KEY) is OPTIONAL — it raises the daily query cap (25 -> 500/day); the
graft works without it. ANY fetch/parse failure leaves the FRED result untouched, so the cron is
never worse off than FRED-only (same never-raise contract as TreasuryYieldSource / GoldSpotSource).

Seasonal adjustment is matched PER SERIES to the FRED backbone it grafts onto, or the seam would
splice two conventions into one series:
  - CPI / core CPI graft the NSA series (CUUR...), because the headline over-the-year change the
    news quotes — and the FRED CPIAUCNS / CPILFENS backbone chosen to match it — are NSA. This is
    the 3.4% (NSA) vs 3.3% (SA) discrepancy Bruce flagged.
  - unemployment, payrolls, participation and the employment counts graft the SA series
    (LNS.../CES...), because those headline figures — and FRED's UNRATE/PAYEMS/... — are SA.
"""

from __future__ import annotations

from datetime import date
from typing import Optional

import requests

from . import observability
from .config import BLS_SERIES as _BLS  # single source of truth (also drives the "View source" link)
from .fred_client import FredSource, SeriesResult
from .models import Observation

_API = "https://api.bls.gov/publicAPI/v2/timeseries/data/"

_MONTH = {f"M{i:02d}": i for i in range(1, 13)}  # BLS monthly period id -> month number (M13/Q.. skipped)


class BlsSource:
    """FredSource decorator: grafts BLS's same-day release tip onto the BLS-origin series."""

    def __init__(self, fred: FredSource, *, api_key: Optional[str] = None,
                 session: Optional[requests.Session] = None, timeout: float = 15.0,
                 today: Optional[date] = None) -> None:
        self.fred = fred
        self.api_key = api_key or None
        self.session = session or requests.Session()
        self.timeout = timeout
        self._today = today                       # injectable for tests; default date.today()
        self._levels: Optional[dict[str, list[tuple[str, float]]]] = None

    def _fetch_levels(self) -> dict[str, list[tuple[str, float]]]:
        """{bls_series_id: [(iso_date, level), ...] newest-first}; {} on any failure.

        One batched POST for all series (BLS multi-series signature). A 3-year window guarantees
        the >=13 monthly points a pc1 (YoY) tip needs. Fetched once per instance (per run)."""
        if self._levels is not None:
            return self._levels
        self._levels = {}
        try:
            end = (self._today or date.today()).year
            payload: dict[str, object] = {
                "seriesid": list(_BLS.values()),
                "startyear": str(end - 2),
                "endyear": str(end),
            }
            if self.api_key:
                payload["registrationkey"] = self.api_key
            resp = self.session.post(_API, json=payload, timeout=self.timeout)
            resp.raise_for_status()
            body = resp.json()
            if body.get("status") != "REQUEST_SUCCEEDED":
                raise RuntimeError(f"BLS status {body.get('status')}: {body.get('message')}")
            for s in body.get("Results", {}).get("series", []):
                rows: list[tuple[str, float]] = []
                for d in s.get("data", []):
                    mo = _MONTH.get(d.get("period", ""))
                    if mo is None:                # annual (M13) / quarterly — not a monthly print
                        continue
                    try:
                        rows.append((f"{int(d['year']):04d}-{mo:02d}-01", float(d["value"])))
                    except (KeyError, ValueError, TypeError):
                        continue
                rows.sort(key=lambda r: r[0], reverse=True)   # newest-first (BLS order is not guaranteed)
                self._levels[s.get("seriesID", "")] = rows
        except Exception as exc:  # noqa: BLE001 — network/parse/bad body: never break the cron over a fresher tip
            self._levels = {}
            observability.record_source("bls", ok=False, degraded=True, error=exc)
            return self._levels
        observability.record_source("bls", ok=True, degraded=not self._levels)
        return self._levels

    def _tip(self, fred_id: str, units: str) -> Optional[Observation]:
        """Latest BLS point in the SAME transform the pipeline asked FRED for, or None."""
        rows = self._fetch_levels().get(_BLS[fred_id])
        if not rows:
            return None
        d0, v0 = rows[0]
        if units == "lin":                                   # raw level
            return Observation(date=d0, value=v0)
        if units == "chg":                                   # 1-period change (e.g. net payrolls)
            if len(rows) < 2:
                return None
            return Observation(date=d0, value=round(v0 - rows[1][1], 4))
        if units == "pc1":                                   # percent change from a year ago (YoY)
            year_ago = f"{int(d0[:4]) - 1}{d0[4:]}"          # same month, prior year
            prior = next((v for dd, v in rows if dd == year_ago), None)
            if not prior:                                    # missing or zero -> can't compute
                return None
            return Observation(date=d0, value=round((v0 / prior - 1) * 100, 5))
        return None                                          # unknown transform -> no graft

    def fetch_series(self, series_id: str, units: str, limit: int) -> SeriesResult:
        result = self.fred.fetch_series(series_id, units, limit)
        if series_id not in _BLS:
            return result
        tip = self._tip(series_id, units)
        newest = result.observations[0].date if result.observations else ""
        if tip is not None and tip.value is not None and tip.date > newest:
            return SeriesResult(observations=[tip, *result.observations], last_updated=tip.date)
        return result
