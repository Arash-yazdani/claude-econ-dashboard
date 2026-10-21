"""NY Fed EFFR — graft the origin's same-day tip onto FRED's DFF (V4 P1, docs/v4-source-upgrades.md).

DFF *is* the effective federal funds rate the New York Fed administers and publishes at ~09:00 ET
for the prior business day; FRED republishes it later. The API is keyless and free
(markets.newyorkfed.org). Verified 2026-08-19: EFFR 2026-08-18 = 3.63, byte-identical to DFF.

fed_funds is CORE and a classifier input, so this graft must never change the NUMBER, only its
freshness — EFFR and DFF are the same series by definition, and any fetch/parse failure leaves the
FRED result untouched (the TreasuryYieldSource contract: never worse than FRED-only, never raise).
"""

from __future__ import annotations

from typing import Optional

import requests

from . import observability
from .fred_client import FredSource, SeriesResult
from .models import Observation

_URL = "https://markets.newyorkfed.org/api/rates/unsecured/effr/last/5.json"
_SERIES = "DFF"


class NyFedSource:
    """FredSource decorator: grafts the NY Fed's EFFR tip onto DFF; passes the rest through."""

    def __init__(self, fred: FredSource, *, session: Optional[requests.Session] = None,
                 timeout: float = 15.0) -> None:
        self.fred = fred
        self.session = session or requests.Session()
        self.timeout = timeout
        self._tips: Optional[list[Observation]] = None

    def _fetch(self) -> list[Observation]:
        """Newest-first EFFR observations; [] on any failure."""
        if self._tips is not None:
            return self._tips
        self._tips = []
        try:
            resp = self.session.get(_URL, timeout=self.timeout)
            resp.raise_for_status()
            rows = resp.json().get("refRates", [])
            obs = [Observation(date=str(r["effectiveDate"]), value=float(r["percentRate"]))
                   for r in rows if r.get("effectiveDate") and r.get("percentRate") is not None]
            self._tips = sorted(obs, key=lambda o: o.date, reverse=True)
        except Exception as exc:  # noqa: BLE001 — never break the cron over a fresher tip
            self._tips = []
            observability.record_source("nyfed_effr", ok=False, degraded=True, error=exc)
            return self._tips
        observability.record_source("nyfed_effr", ok=True, degraded=not self._tips)
        return self._tips

    def fetch_series(self, series_id: str, units: str, limit: int) -> SeriesResult:
        result = self.fred.fetch_series(series_id, units, limit)
        if series_id != _SERIES:
            return result
        newest = result.observations[0].date if result.observations else ""
        fresh = [o for o in self._fetch() if o.date > newest]
        if fresh:
            return SeriesResult(observations=[*fresh, *result.observations],
                                last_updated=fresh[0].date)
        return result
