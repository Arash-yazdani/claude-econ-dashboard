"""FRED REST client (free tier, no payment method — ADR-001).

`FredSource` is the seam the pipeline depends on, so tests inject an in-memory fake
and never touch the network. `FredClient` is the real HTTP implementation.

FRED budget: 2 API calls per indicator (observations + series metadata) × len(INDICATORS),
plus 2 extra calls per non-"lin" indicator for the level-only fetch. As of V2.1 (Charter
Rev 3): 25 indicators × 2 + 4 non-lin × 2 = 58 per run × 2 runs/day ≈ 116 calls/day — far
under FRED's limits. (The PRD's original ≤25/day described the 7-indicator V1; recount from
config.INDICATORS when the registry changes rather than trusting this comment.)
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Optional, Protocol

import requests

from . import observability
from .config import FRED_API_KEY_ENV, FRED_BASE_URL
from .models import Observation


class FredError(RuntimeError):
    """FRED API failure (network, auth, or unexpected payload shape)."""


@dataclass
class SeriesResult:
    observations: list[Observation]  # newest-first
    last_updated: str  # ISO date the series was last revised/published on FRED


class FredSource(Protocol):
    def fetch_series(self, series_id: str, units: str, limit: int) -> SeriesResult: ...


def _parse_value(raw: Any) -> Optional[float]:
    # FRED encodes missing observations as ".".
    if raw is None or raw == "." or raw == "":
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


class FredClient:
    """Real FRED HTTP client."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: str = FRED_BASE_URL,
        session: Optional[requests.Session] = None,
        timeout: float = 15.0,
    ) -> None:
        self.api_key = api_key or os.environ.get(FRED_API_KEY_ENV, "")
        if not self.api_key:
            raise FredError(
                f"No FRED API key. Set {FRED_API_KEY_ENV} (see .env.example). "
                "Get a free key at https://fredaccount.stlouisfed.org/apikeys"
            )
        self.base_url = base_url.rstrip("/")
        self.session = session or requests.Session()
        self.timeout = timeout

    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        params = {**params, "api_key": self.api_key, "file_type": "json"}
        try:
            resp = self.session.get(f"{self.base_url}/{path}", params=params, timeout=self.timeout)
        except requests.RequestException as exc:  # network/DNS/timeout
            raise FredError(f"FRED request failed for {path}: {exc}") from exc
        if resp.status_code != 200:
            raise FredError(f"FRED {path} returned HTTP {resp.status_code}: {resp.text[:200]}")
        try:
            return resp.json()
        except ValueError as exc:
            raise FredError(f"FRED {path} returned non-JSON body") from exc

    def fetch_series(
        self,
        series_id: str,
        units: str,
        limit: int,
        frequency: Optional[str] = None,
        aggregation_method: Optional[str] = None,
    ) -> SeriesResult:
        params: dict[str, Any] = {
            "series_id": series_id, "units": units, "sort_order": "desc", "limit": limit,
        }
        if frequency:  # e.g. "m" to aggregate a daily series to monthly (back-test)
            params["frequency"] = frequency
        if aggregation_method:
            params["aggregation_method"] = aggregation_method
        obs_payload = self._get("series/observations", params)
        rows = obs_payload.get("observations")
        if not isinstance(rows, list):
            raise FredError(f"FRED observations payload missing 'observations' for {series_id}")
        observations = [Observation(date=r["date"], value=_parse_value(r.get("value"))) for r in rows]

        meta = self._get("series", {"series_id": series_id})
        seriess = meta.get("seriess") or []
        if not seriess:
            raise FredError(f"FRED series metadata missing for {series_id}")
        last_updated = str(seriess[0].get("last_updated", ""))[:10]  # "YYYY-MM-DD ..." -> date
        observability.record_source("fred", ok=True)
        return SeriesResult(observations=observations, last_updated=last_updated)
