"""Treasury.gov daily par yield — graft the same-day 2Y/10Y tip onto FRED's DGS2/DGS10.

FRED's DGS10/DGS2 ARE the U.S. Treasury CMT par yields, but FRED republishes them a day late.
`TreasuryYieldSource` wraps a `FredSource` and, for those two series, prepends Treasury.gov's own
latest point when it is newer than FRED's — so the 10Y/2Y rows and the derived 2s10s read same-day.

FRED stays the source of record for history/backfill; we only add the freshest tip. Treasury.gov is
public + key-less (ADR-001 free/no-card), and ANY fetch/parse failure leaves the FRED result
untouched — the cron is never worse off than FRED-only.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import date
from typing import Optional

import requests

from . import observability
from .fred_client import FredSource, SeriesResult
from .models import Observation

# Daily par yield curves, ONE month per request (small + fast: ~28KB). Every tenor is on each date.
# Two feeds, same XML schema but DIFFERENT field prefixes (probed live 2026-08-20): the nominal
# curve uses BC_*, the REAL (TIPS) curve uses TC_*. Guessing BC_ for the real curve fails silently.
_BASE = ("https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
         "pages/xml?data={feed}&field_tdr_date_value_month={month}")
BROWSER_UA = "Mozilla/5.0"  # the minimal UA treasury.gov's edge serves at full speed (see __init__)
_D = {"d": "http://schemas.microsoft.com/ado/2007/08/dataservices"}
_PROPS = "{http://schemas.microsoft.com/ado/2007/08/dataservices/metadata}properties"
# feed key -> (Treasury feed name, {FRED series id -> Treasury XML field})
_FEEDS: dict[str, tuple[str, dict[str, str]]] = {
    "nominal": ("daily_treasury_yield_curve", {"DGS10": "BC_10YEAR", "DGS2": "BC_2YEAR"}),
    "real": ("daily_treasury_real_yield_curve", {"DFII10": "TC_10YEAR"}),  # V4 P1: TIPS 10Y
}
_SERIES_FEED = {sid: key for key, (_, tenors) in _FEEDS.items() for sid in tenors}


def _text(props: ET.Element, field: str) -> Optional[str]:
    el = props.find("d:" + field, _D)
    return el.text if el is not None else None


class TreasuryYieldSource:
    """FredSource decorator: grafts Treasury.gov's same-day tip onto DGS10/DGS2 (nominal curve)
    and DFII10 (real/TIPS curve); passes the rest through. Each feed is fetched lazily, at most
    once per run, and only if one of its series is actually requested."""

    def __init__(self, fred: FredSource, *, session: Optional[requests.Session] = None,
                 timeout: float = 15.0, month: Optional[str] = None) -> None:
        self.fred = fred
        if session is None:
            session = requests.Session()
            session.headers["User-Agent"] = BROWSER_UA   # only on a session WE built (see below)
        self.session = session
        # treasury.gov (Akamai) TARPITS non-browser User-Agents: the default "python-requests/x"
        # and any UA carrying a product/URL token are answered in ~18-19s, a browser UA in ~0.3s.
        # Against our 15s timeout that is a guaranteed read-timeout, which is exactly what
        # happened on all 22 runs from 2026-08-24 PM to 2026-09-03: the graft degraded silently
        # every run and the 10Y/2Y/TIPS rows fell back to FRED's day-late values for ten days,
        # unpaged because a degraded-but-delivered source does not page by design (d5243e4).
        # Measured 2026-09-03, same host, same file: default UA 18.4s / "Mozilla/5.0" 0.3s.
        # The other origins (Cboe, Freddie, NY Fed, FiscalData, BLS, gold-api) serve the default
        # UA fast, so this stays Treasury-only until the class recurs elsewhere. An INJECTED
        # session is the caller's (tests pass fakes with no .headers), so it is left untouched.
        self.timeout = timeout
        self._month = month  # "YYYYMM"; default = current month (lazy, fetched once per run)
        self._tip: dict[str, Optional[dict[str, Observation]]] = {k: None for k in _FEEDS}

    def _months(self) -> list[str]:
        """Months to try, newest first: the current one, then the previous one.

        The feed is filed BY CALENDAR MONTH, so at the start of a month the current file has no
        business day in it yet and the freshest par yield is still in the PREVIOUS month's file.
        Asking only for the current month there silently drops the tip and leaves FRED's day-late
        number on screen — the exact staleness this class exists to remove.

        It is not hypothetical: on Sat 2026-08-01 the 202608 feed was empty, so the dashboard
        served the 10Y at 4.68 (Jul 30) while Treasury had already published 4.75 for Jul 31 —
        a day stale and 7bp off, on the row the briefing quotes most.

        Costs one extra request on the ~1-2 days a month that need it; mid-month the first file
        always has rows, so the second is never fetched.
        """
        start = self._month or date.today().strftime("%Y%m")
        y, m = int(start[:4]), int(start[4:6])
        py, pm = (y - 1, 12) if m == 1 else (y, m - 1)
        return [start, f"{py}{pm:02d}"]

    def _tips(self, feed_key: str) -> dict[str, Observation]:
        """{series_id: freshest Observation} from one Treasury feed; {} on any failure."""
        cached = self._tip[feed_key]
        if cached is not None:
            return cached
        feed_name, tenors = _FEEDS[feed_key]
        source_name = "treasury" if feed_key == "nominal" else f"treasury_{feed_key}"
        tips: dict[str, Observation] = {}
        self._tip[feed_key] = tips
        try:
            for month in self._months():
                resp = self.session.get(_BASE.format(feed=feed_name, month=month),
                                        timeout=self.timeout)
                resp.raise_for_status()
                latest = max(ET.fromstring(resp.content).iter(_PROPS),
                             key=lambda p: _text(p, "NEW_DATE") or "", default=None)
                day = _text(latest, "NEW_DATE") if latest is not None else None
                if not day:
                    continue  # month has no business day yet — fall back to the previous file
                for sid, field in tenors.items():
                    raw = _text(latest, field)
                    if raw:
                        tips[sid] = Observation(date=day[:10], value=float(raw))
                if tips:
                    break
        except Exception as exc:  # network, parse, or a bad value — never break the cron over a fresher tip
            tips.clear()
            observability.record_source(source_name, ok=False, degraded=True, error=exc)
            return tips
        # Now that an empty current month falls back a file, an empty tip means BOTH months were
        # empty — a genuine anomaly, not a calendar fact. Before the fallback this flag fired every
        # time a month opened on a weekend and paged hello@modeo.io about a healthy feed (Aug 1-2
        # 2026), which is how an alert channel teaches its reader to ignore it.
        observability.record_source(source_name, ok=True, degraded=not tips)
        return tips

    def fetch_series(self, series_id: str, units: str, limit: int) -> SeriesResult:
        result = self.fred.fetch_series(series_id, units, limit)
        feed_key = _SERIES_FEED.get(series_id)
        if feed_key is None:
            return result
        tip = self._tips(feed_key).get(series_id)
        newest = result.observations[0].date if result.observations else ""
        if tip and tip.date > newest:  # ISO dates compare chronologically; only graft when newer
            return SeriesResult(observations=[tip, *result.observations], last_updated=tip.date)
        return result
