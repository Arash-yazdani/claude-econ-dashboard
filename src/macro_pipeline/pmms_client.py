"""Freddie Mac PMMS 30-year — graft the origin's weekly print onto FRED's MORTGAGE30US (V4 P2).

FRED's MORTGAGE30US is Freddie Mac's Primary Mortgage Market Survey, republished. Freddie
publishes the full history as a keyless CSV (weekly, Thursdays, since 1971 — probed live
2026-08-20, tail current). Origin-first with a small freshness gain on release day; same
never-raise decorator contract. Only the CSV tail is parsed.
"""

from __future__ import annotations

from typing import Optional

import requests

from . import observability
from .fred_client import FredSource, SeriesResult
from .models import Observation

_URL = "https://www.freddiemac.com/pmms/docs/PMMS_history.csv"
_SERIES = "MORTGAGE30US"
_TAIL = 8  # weeks of tips to consider — more than any plausible FRED lag


class PmmsSource:
    """FredSource decorator: grafts Freddie's weekly PMMS 30Y onto MORTGAGE30US; passes the rest through."""

    def __init__(self, fred: FredSource, *, session: Optional[requests.Session] = None,
                 timeout: float = 20.0) -> None:
        self.fred = fred
        self.session = session or requests.Session()
        self.timeout = timeout
        self._tips: Optional[list[Observation]] = None

    def _fetch(self) -> list[Observation]:
        """Newest-first weekly pmms30 observations; [] on any failure."""
        if self._tips is not None:
            return self._tips
        self._tips = []
        try:
            resp = self.session.get(_URL, timeout=self.timeout)
            resp.raise_for_status()
            obs: list[Observation] = []
            for line in resp.text.strip().splitlines()[-_TAIL:]:
                parts = line.split(",")
                if len(parts) < 2 or "/" not in parts[0] or not parts[1].strip():
                    continue  # header / malformed / missing pmms30
                m, d, y = parts[0].split("/")
                obs.append(Observation(date=f"{y}-{int(m):02d}-{int(d):02d}",
                                       value=float(parts[1])))
            self._tips = sorted(obs, key=lambda o: o.date, reverse=True)
        except Exception as exc:  # noqa: BLE001 — never break the cron over a fresher tip
            self._tips = []
            observability.record_source("pmms", ok=False, degraded=True, error=exc)
            return self._tips
        observability.record_source("pmms", ok=True, degraded=not self._tips)
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
