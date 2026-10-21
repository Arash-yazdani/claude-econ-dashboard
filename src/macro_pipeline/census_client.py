"""Census New Residential Construction — graft the origin's monthly print onto FRED's HOUST (V4 P3).

FRED's HOUST is Census/HUD's housing-starts SAAR (thousands), republished. The Census EITS API
serves it with a free government key (CENSUS_API_KEY, no card); absent -> pure no-op. Insurance
graft like BEA: the AM run fires after FRED's usual ingest, so this wins only when FRED is late.

Facet codes VERIFIED LIVE 2026-08-20 (enumerated with a real key): the SAAR series FRED's HOUST
republishes is category_code=ASTARTS (the A- prefix means annual rate; plain STARTS exists only
NSA), data_type_code=TOTAL, seasonally_adj=yes. The first guess (STARTS/yes) matched nothing and
correctly surfaced as a DEGRADED census_starts row with FRED standing — which is the designed
failure mode if Census ever renames a code. Never raises.
"""

from __future__ import annotations

from typing import Optional

import requests

from . import observability
from .fred_client import FredSource, SeriesResult
from .models import Observation

_URL = "https://api.census.gov/data/timeseries/eits/resconst"
_SERIES = "HOUST"
_MONTH_FMT = 7  # "YYYY-MM" time values


class CensusSource:
    """FredSource decorator: grafts Census's housing-starts print onto HOUST; passes the rest through."""

    def __init__(self, fred: FredSource, *, api_key: Optional[str] = None,
                 session: Optional[requests.Session] = None, timeout: float = 20.0,
                 year: Optional[int] = None) -> None:
        self.fred = fred
        self.api_key = (api_key or "").strip() or None
        self.session = session or requests.Session()
        self.timeout = timeout
        self._year = year  # injectable for tests
        self._tips: Optional[list[Observation]] = None

    def _fetch(self) -> list[Observation]:
        """Newest-first monthly SAAR observations (thousands); [] on failure/no key."""
        if self._tips is not None:
            return self._tips
        self._tips = []
        if not self.api_key:
            return self._tips  # keyless -> pure no-op (the key is optional)
        try:
            from datetime import date
            year = self._year or date.today().year
            resp = self.session.get(_URL, params={
                "get": "cell_value,time_slot_id", "category_code": "ASTARTS",
                "data_type_code": "TOTAL", "seasonally_adj": "yes",
                "time": str(year), "key": self.api_key,
            }, timeout=self.timeout)
            resp.raise_for_status()
            rows = resp.json()  # [["cell_value","time_slot_id",...,"time"], [".."], ...]
            header = rows[0]
            iv, it = header.index("cell_value"), header.index("time")
            obs = []
            for r in rows[1:]:
                t = str(r[it])
                if len(t) == _MONTH_FMT:  # "YYYY-MM"
                    obs.append(Observation(date=f"{t}-01", value=float(r[iv])))
            self._tips = sorted(obs, key=lambda o: o.date, reverse=True)
        except Exception as exc:  # noqa: BLE001 — never break the cron over a fresher print
            self._tips = []
            observability.record_source("census_starts", ok=False, degraded=True, error=exc)
            return self._tips
        observability.record_source("census_starts", ok=True, degraded=not self._tips)
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
