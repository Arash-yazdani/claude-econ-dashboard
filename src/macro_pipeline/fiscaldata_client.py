"""Treasury FiscalData "Debt to the Penny" — graft DAILY total public debt onto FRED's quarterly
GFDEBTN (V4 P1, the plan's biggest visible win: on 2026-08-19 the daily figure was ~$40.05T while
the freshest quarterly print read ~$39.07T).

Keyless, free (api.fiscaldata.treasury.gov). Units matched to the FRED backbone: GFDEBTN is
MILLIONS of dollars, FiscalData returns dollars, so values are divided by 1e6. At least TWO daily
points are grafted so compute_delta reads day-over-day debt change instead of day-vs-last-quarter.
The row's spec stays QUARTERLY (period_label shows the quarter; the backbone/history stay FRED),
so a FiscalData outage degrades cleanly to the quarterly value without tripping staleness.
Same never-raise decorator contract as every other source.
"""

from __future__ import annotations

from typing import Optional

import requests

from . import observability
from .fred_client import FredSource, SeriesResult
from .models import Observation

_URL = ("https://api.fiscaldata.treasury.gov/services/api/fiscal_service"
        "/v2/accounting/od/debt_to_penny?sort=-record_date&page%5Bsize%5D=5")
_SERIES = "GFDEBTN"


class FiscalDataSource:
    """FredSource decorator: grafts the daily debt level onto GFDEBTN; passes the rest through."""

    def __init__(self, fred: FredSource, *, session: Optional[requests.Session] = None,
                 timeout: float = 15.0) -> None:
        self.fred = fred
        self.session = session or requests.Session()
        self.timeout = timeout
        self._tips: Optional[list[Observation]] = None

    def _fetch(self) -> list[Observation]:
        """Newest-first daily debt observations in MILLIONS of dollars; [] on any failure."""
        if self._tips is not None:
            return self._tips
        self._tips = []
        try:
            resp = self.session.get(_URL, timeout=self.timeout)
            resp.raise_for_status()
            rows = resp.json().get("data", [])
            obs = [Observation(date=str(r["record_date"]),
                               value=round(float(r["tot_pub_debt_out_amt"]) / 1e6, 1))
                   for r in rows if r.get("record_date") and r.get("tot_pub_debt_out_amt")]
            self._tips = sorted(obs, key=lambda o: o.date, reverse=True)
        except Exception as exc:  # noqa: BLE001 — never break the cron over a fresher tip
            self._tips = []
            observability.record_source("fiscaldata_debt", ok=False, degraded=True, error=exc)
            return self._tips
        observability.record_source("fiscaldata_debt", ok=True, degraded=not self._tips)
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
