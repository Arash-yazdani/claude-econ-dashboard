"""Cboe VIX — graft the origin's T+0 daily close onto FRED's VIXCLS (V4 P1).

Cboe publishes the full VIX daily-close history as a keyless CSV on its CDN; FRED's VIXCLS
republishes it a day later (verified 2026-08-19: Cboe had the 08/18 close 15.84 while VIXCLS
still read 08/17's 15.19). Display-only market row — same never-raise decorator contract.
The CSV is ~470KB and fetched at most once per run; only the tail is used.
"""

from __future__ import annotations

from typing import Optional

import requests

from . import observability
from .fred_client import FredSource, SeriesResult
from .models import Observation

_URL = "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv"
_SERIES = "VIXCLS"
_TAIL = 15  # trading days of tips to consider — more than any plausible FRED lag


class CboeSource:
    """FredSource decorator: grafts Cboe's same-day VIX close onto VIXCLS; passes the rest through."""

    def __init__(self, fred: FredSource, *, session: Optional[requests.Session] = None,
                 timeout: float = 20.0) -> None:
        self.fred = fred
        self.session = session or requests.Session()
        self.timeout = timeout
        self._tips: Optional[list[Observation]] = None

    def _fetch(self) -> list[Observation]:
        """Newest-first VIX closes from the CSV tail; [] on any failure."""
        if self._tips is not None:
            return self._tips
        self._tips = []
        try:
            resp = self.session.get(_URL, timeout=self.timeout)
            resp.raise_for_status()
            lines = resp.text.strip().splitlines()
            obs: list[Observation] = []
            for line in lines[-_TAIL:]:
                parts = line.split(",")
                if len(parts) < 5 or "/" not in parts[0]:
                    continue  # header or malformed
                m, d, y = parts[0].split("/")
                obs.append(Observation(date=f"{y}-{int(m):02d}-{int(d):02d}",
                                       value=float(parts[4])))  # CLOSE
            self._tips = sorted(obs, key=lambda o: o.date, reverse=True)
        except Exception as exc:  # noqa: BLE001 — never break the cron over a fresher tip
            self._tips = []
            observability.record_source("cboe_vix", ok=False, degraded=True, error=exc)
            return self._tips
        observability.record_source("cboe_vix", ok=True, degraded=not self._tips)
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
