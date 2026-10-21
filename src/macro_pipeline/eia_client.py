"""EIA Europe Brent spot (RBRTE) — graft the origin's daily print onto FRED's DCOILBRENTEU
(V4 P2, docs/v4-source-upgrades.md).

FRED's DCOILBRENTEU IS the EIA's Europe Brent spot FOB assessment, republished. Going to
api.eia.gov reads it at the origin, as fresh as EIA posts it. Free government key
(EIA_API_KEY); absent or invalid -> no request / degraded, FRED value stands — the row is
never worse than FRED-only, and this decorator never raises.

This does NOT close the spot-vs-futures gap Bruce noticed (news sites quote futures; no free
licensed futures feed exists — see the plan doc). The row is relabeled "Brent spot" in config
so the difference self-explains.
"""

from __future__ import annotations

from typing import Optional

import requests

from . import observability
from .fred_client import FredSource, SeriesResult
from .models import Observation

_URL = "https://api.eia.gov/v2/petroleum/pri/spt/data/"
_PARAMS = {"frequency": "daily", "data[0]": "value", "facets[series][]": "RBRTE",
           "sort[0][column]": "period", "sort[0][direction]": "desc", "length": 8}
_SERIES = "DCOILBRENTEU"


class EiaSource:
    """FredSource decorator: grafts EIA's Brent spot tip onto DCOILBRENTEU; passes the rest through."""

    def __init__(self, fred: FredSource, *, api_key: Optional[str] = None,
                 session: Optional[requests.Session] = None, timeout: float = 15.0) -> None:
        self.fred = fred
        self.api_key = (api_key or "").strip() or None
        self.session = session or requests.Session()
        self.timeout = timeout
        self._tips: Optional[list[Observation]] = None

    def _fetch(self) -> list[Observation]:
        """Newest-first daily Brent spot observations; [] on any failure or without a key."""
        if self._tips is not None:
            return self._tips
        self._tips = []
        if not self.api_key:
            return self._tips  # keyless -> pure no-op, not a degradation (the key is optional)
        try:
            resp = self.session.get(_URL, params={**_PARAMS, "api_key": self.api_key},
                                    timeout=self.timeout)
            resp.raise_for_status()
            rows = resp.json().get("response", {}).get("data", [])
            obs = [Observation(date=str(r["period"]), value=float(r["value"]))
                   for r in rows if r.get("period") and r.get("value") is not None]
            self._tips = sorted(obs, key=lambda o: o.date, reverse=True)
        except Exception as exc:  # noqa: BLE001 — never break the cron over a fresher tip
            self._tips = []
            observability.record_source("eia_brent", ok=False, degraded=True, error=exc)
            return self._tips
        observability.record_source("eia_brent", ok=True, degraded=not self._tips)
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
