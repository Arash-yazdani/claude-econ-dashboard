"""BEA NIPA real GDP — graft the origin's quarterly print onto FRED's GDPC1-derived YoY (V4 P3).

FRED's GDPC1 is BEA's chained-dollar real GDP (NIPA T10106 line 1, series A191RX), republished.
BEA posts at 08:30 ET on release mornings; FRED ingests later. Our AM run fires 10:45 ET, after
FRED's usual ingest, so this graft is INSURANCE for the mornings FRED runs late — same rationale
as the BLS graft. BEA_API_KEY is a free government key (no card); absent -> pure no-op.

real_gdp_yoy is CORE + a classifier input. The graft computes the SAME convention FRED's pc1
transform does — YoY percent change of the quarterly level — and grafts only a strictly newer
quarter, so the number can differ from FRED only by being one release fresher. The plausibility
gate (−10..10) still guards it. Never raises; any failure or parse miss degrades to FRED.
"""

from __future__ import annotations

from typing import Optional

import requests

from . import observability
from .fred_client import FredSource, SeriesResult
from .models import Observation

_URL = "https://apps.bea.gov/api/data/"
_SERIES = "GDPC1"
_Q_MONTH = {"Q1": "01", "Q2": "04", "Q3": "07", "Q4": "10"}


class BeaSource:
    """FredSource decorator: grafts BEA's newest real-GDP quarter onto GDPC1; passes the rest through."""

    def __init__(self, fred: FredSource, *, api_key: Optional[str] = None,
                 session: Optional[requests.Session] = None, timeout: float = 20.0,
                 years: Optional[str] = None) -> None:
        self.fred = fred
        self.api_key = (api_key or "").strip() or None
        self.session = session or requests.Session()
        self.timeout = timeout
        self._years = years  # "YYYY,YYYY,YYYY"; injectable for tests
        self._levels: Optional[list[Observation]] = None

    def _fetch_levels(self) -> list[Observation]:
        """Newest-first quarterly real-GDP LEVELS (billions, SAAR); [] on failure/no key."""
        if self._levels is not None:
            return self._levels
        self._levels = []
        if not self.api_key:
            return self._levels  # keyless -> pure no-op (the key is optional)
        try:
            from datetime import date
            end = date.today().year
            years = self._years or f"{end - 2},{end - 1},{end}"
            resp = self.session.get(_URL, params={
                "UserID": self.api_key, "method": "GetData", "datasetname": "NIPA",
                "TableName": "T10106", "Frequency": "Q", "Year": years, "ResultFormat": "JSON",
            }, timeout=self.timeout)
            resp.raise_for_status()
            rows = resp.json().get("BEAAPI", {}).get("Results", {}).get("Data", [])
            obs = []
            for r in rows:
                if r.get("SeriesCode") != "A191RX" or str(r.get("LineNumber")) != "1":
                    continue
                tp = str(r.get("TimePeriod", ""))  # e.g. "2026Q2"
                if len(tp) != 6 or tp[4:] not in _Q_MONTH:
                    continue
                val = float(str(r.get("DataValue", "")).replace(",", ""))
                obs.append(Observation(date=f"{tp[:4]}-{_Q_MONTH[tp[4:]]}-01", value=val))
            self._levels = sorted(obs, key=lambda o: o.date, reverse=True)
        except Exception as exc:  # noqa: BLE001 — never break the cron over a fresher print
            self._levels = []
            observability.record_source("bea_gdp", ok=False, degraded=True, error=exc)
            return self._levels
        observability.record_source("bea_gdp", ok=True, degraded=not self._levels)
        return self._levels

    def _yoy_tip(self) -> Optional[Observation]:
        """YoY % for BEA's newest quarter (same convention as FRED's pc1), or None."""
        lv = self._fetch_levels()
        if not lv:
            return None
        d0, v0 = lv[0].date, lv[0].value
        year_ago = f"{int(d0[:4]) - 1}{d0[4:]}"
        prior = next((o.value for o in lv if o.date == year_ago), None)
        if not prior:
            return None
        return Observation(date=d0, value=round((v0 / prior - 1) * 100, 5))

    def fetch_series(self, series_id: str, units: str, limit: int) -> SeriesResult:
        result = self.fred.fetch_series(series_id, units, limit)
        if series_id != _SERIES or units != "pc1":
            return result  # the raw-level (lin) refetch stays FRED's
        tip = self._yoy_tip()
        newest = result.observations[0].date if result.observations else ""
        if tip is not None and tip.date > newest:
            return SeriesResult(observations=[tip, *result.observations], last_updated=tip.date)
        return result
