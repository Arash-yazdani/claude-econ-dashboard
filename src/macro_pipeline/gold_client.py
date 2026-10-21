"""Gold spot price via gold-api.com — display-only, supplementary-grade (ADR-006).

Two calls, layered:
  - `/price/XAU` — the live tick. Free, keyless, no rate limit (per gold-api.com's own
    llms.txt). This is the "current value" and is ALWAYS attempted.
  - `/history?groupBy=day&aggregation=avg` — real daily average prices, oldest→newest across
    ~10 years (confirmed live 2026-07-21: 3,196 rows, 2016-07 to today). Requires an API key
    (`x-api-key` header) and is capped at 10 requests/hour on the free tier — fine for a
    twice-daily cron (2 calls/day, nowhere near the cap). Only attempted when GOLD_API_KEY is
    configured; the row still works perfectly without it (falls back to current-value-only,
    the original shipped behavior).

    Two adjacent endpoints were dead ends, kept here so they're never re-tried blind:
    `/history` with NO groupBy defaults to one row per YEAR holding only that year's peak
    price — not a time series, and actively misleading next to the live tick (2026's "high"
    ran ~$1,500 above the live price the same day). `/ohlc/{symbol}` collapses the entire
    requested range into ONE open/high/low/close object, not a per-day series either.

`GoldSpotSource` decorates a `FredSource`: it intercepts one synthetic series id (never a real
FRED series) and passes everything else through untouched. The live tick is grafted onto the
freshest slot of the history (same pattern as `TreasuryYieldSource`'s Treasury.gov graft on
FRED) — it stays the "current value" `_pick_value` picks, while yesterday-and-earlier still
comes from `/history`, so `deltas.compute_delta` gets a real day-over-day change instead of
always None. ANY fetch/parse failure (missing key, network, rate limit, malformed body) drops
that call's contribution and falls back toward the least-fresh working tier — the live tick
alone, or (if even that fails) a null-value observation — never a raise. gold-api.com is a
small, unproven vendor (ADR-006's own risk note), so a bad day here must degrade the row to
"—" or a flatter chart, never break the cron.
"""

from __future__ import annotations

import time
from datetime import date
from typing import Optional

import requests

from . import observability
from .fred_client import FredSource, SeriesResult
from .models import Observation

# config.py's IndicatorSpec.fred_series_id for the gold spec — never sent to FRED, only matched here.
GOLD_SERIES_ID = "XAU_SPOT_GOLDAPI"
_PRICE_URL = "https://api.gold-api.com/price/XAU"
_HISTORY_URL = "https://api.gold-api.com/history"
# ~10 years, matching every other row's chart depth. gold-api.com's /history takes a start/end
# timestamp range (not a row-count limit like FRED's), so this is independent of the `limit`
# fetch_series is called with.
_HISTORY_DAYS = 10 * 365


class GoldSpotSource:
    """FredSource decorator: serves GOLD_SERIES_ID from gold-api.com; passes the rest through."""

    def __init__(self, fred: FredSource, *, session: Optional[requests.Session] = None,
                 timeout: float = 10.0, api_key: Optional[str] = None) -> None:
        self.fred = fred
        self.session = session or requests.Session()
        self.timeout = timeout
        self.api_key = api_key or None  # falsy (None/"") -> history skipped, current-value-only

    def fetch_series(self, series_id: str, units: str, limit: int) -> SeriesResult:
        if series_id != GOLD_SERIES_ID:
            return self.fred.fetch_series(series_id, units, limit)
        today = date.today().isoformat()
        live = self._fetch_live()
        history = self._fetch_history() if self.api_key else []
        observations = self._merge(live, history)
        if not observations:  # both calls failed — never hand back an empty list (_pick_value raises)
            return SeriesResult(observations=[Observation(date=today, value=None)], last_updated=today)
        return SeriesResult(observations=observations, last_updated=observations[0].date)

    def _fetch_live(self) -> Optional[Observation]:
        today = date.today().isoformat()
        try:
            resp = self.session.get(_PRICE_URL, timeout=self.timeout)
            resp.raise_for_status()
            body = resp.json()
            price = float(body["price"])
            day = str(body.get("updatedAt", ""))[:10] or today
            observability.record_source("gold_api_price", ok=True)
            return Observation(date=day, value=price)
        except Exception as exc:  # network, parse, bad value — the history layer can still carry the row
            observability.record_source("gold_api_price", ok=False, degraded=True, error=exc)
            return None

    def _fetch_history(self) -> list[Observation]:
        try:
            now = int(time.time())
            resp = self.session.get(
                _HISTORY_URL,
                headers={"x-api-key": self.api_key},
                params={"symbol": "XAU", "startTimestamp": now - _HISTORY_DAYS * 86400,
                         "endTimestamp": now, "groupBy": "day", "aggregation": "avg",
                         "orderBy": "desc"},  # newest-first, matching every other client's convention
                timeout=self.timeout * 3,  # a ~10y daily payload is bigger than the live tick
            )
            resp.raise_for_status()
            out: list[Observation] = []
            for row in resp.json():
                try:
                    out.append(Observation(date=str(row["day"])[:10], value=float(row["avg_price"])))
                except (KeyError, TypeError, ValueError):
                    continue  # one bad row shouldn't sink the whole history
            # An empty history is a degradation even on a 200: the detail chart goes flat.
            observability.record_source("gold_api_history", ok=True, degraded=not out)
            return out
        except Exception as exc:  # network, 429 (10/hr cap), auth, parse — the live tick still stands alone
            observability.record_source("gold_api_history", ok=False, degraded=True, error=exc)
            return []

    def _merge(self, live: Optional[Observation], history: list[Observation]) -> list[Observation]:
        """Graft the precise live tick over history's same-day average (TreasuryYieldSource's
        same-day-tip pattern) — the live tick is more exact than an average of partial-day data,
        and grafting (rather than just appending) avoids a duplicate/conflicting "today" point."""
        if live is None:
            return history
        return [live, *[o for o in history if o.date != live.date]]
