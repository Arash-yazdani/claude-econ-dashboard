"""TreasuryYieldSource grafts Treasury.gov's same-day 2Y/10Y tip onto FRED's DGS10/DGS2."""

from __future__ import annotations

from macro_pipeline.fred_client import SeriesResult
from macro_pipeline.models import Observation
from macro_pipeline.treasury_client import TreasuryYieldSource

# Two business days; the newest (06-25) is one day past the fake FRED's newest (06-24).
_XML = b"""<?xml version="1.0"?>
<feed xmlns:d="http://schemas.microsoft.com/ado/2007/08/dataservices"
      xmlns:m="http://schemas.microsoft.com/ado/2007/08/dataservices/metadata">
  <entry><content><m:properties>
    <d:NEW_DATE>2026-06-24T00:00:00</d:NEW_DATE>
    <d:BC_2YEAR>4.05</d:BC_2YEAR><d:BC_10YEAR>4.38</d:BC_10YEAR>
  </m:properties></content></entry>
  <entry><content><m:properties>
    <d:NEW_DATE>2026-06-25T00:00:00</d:NEW_DATE>
    <d:BC_2YEAR>4.09</d:BC_2YEAR><d:BC_10YEAR>4.40</d:BC_10YEAR>
  </m:properties></content></entry>
</feed>"""


class _Resp:
    def __init__(self, content):
        self.content = content

    def raise_for_status(self):
        pass


class _Session:
    def __init__(self, content=_XML, boom=False):
        self.content, self.boom = content, boom
        self.months: list[str] = []          # every month actually requested, in order

    def get(self, url, timeout=None):
        self.months.append(url.split("field_tdr_date_value_month=")[-1])
        if self.boom:
            raise RuntimeError("network down")
        return _Resp(self.content)


# A month that has opened but has no business day in it yet — 200 OK, zero rows. This is what
# treasury.gov actually returns for 202608 on Sat 2026-08-01.
_EMPTY_XML = b"""<?xml version="1.0"?>
<feed xmlns:d="http://schemas.microsoft.com/ado/2007/08/dataservices"
      xmlns:m="http://schemas.microsoft.com/ado/2007/08/dataservices/metadata">
</feed>"""


class _MonthSession(_Session):
    """Serves different XML per month, like the real per-month feed files."""

    def __init__(self, by_month):
        super().__init__()
        self.by_month = by_month

    def get(self, url, timeout=None):
        month = url.split("field_tdr_date_value_month=")[-1]
        self.months.append(month)
        return _Resp(self.by_month.get(month, _EMPTY_XML))


class _Fred:
    """Fake FredSource: DGS10/DGS2 newest = 06-24; SP500 is the pass-through control."""

    def fetch_series(self, series_id, units, limit):
        data = {
            "DGS10": [Observation("2026-06-24", 4.50), Observation("2026-06-23", 4.52)],
            "DGS2": [Observation("2026-06-24", 4.07), Observation("2026-06-23", 4.06)],
            "SP500": [Observation("2026-06-23", 7365.46), Observation("2026-06-22", 7472.79)],
        }[series_id]
        return SeriesResult(observations=data, last_updated="2026-06-24")


def _tip(obs):
    return (obs.date, obs.value)


def test_grafts_fresher_treasury_tip_onto_dgs10():
    r = TreasuryYieldSource(_Fred(), session=_Session(), month="202606").fetch_series("DGS10", "lin", 2600)
    assert _tip(r.observations[0]) == ("2026-06-25", 4.40)   # Treasury same-day tip prepended
    assert _tip(r.observations[1]) == ("2026-06-24", 4.50)   # FRED history intact below it
    assert r.last_updated == "2026-06-25"


def test_grafts_dgs2_from_the_same_feed():
    r = TreasuryYieldSource(_Fred(), session=_Session(), month="202606").fetch_series("DGS2", "lin", 2600)
    assert _tip(r.observations[0]) == ("2026-06-25", 4.09)


def test_non_treasury_series_passes_through_untouched():
    r = TreasuryYieldSource(_Fred(), session=_Session(), month="202606").fetch_series("SP500", "lin", 2600)
    assert _tip(r.observations[0]) == ("2026-06-23", 7365.46)
    assert len(r.observations) == 2


def test_no_graft_when_treasury_is_not_strictly_newer():
    class _Caught(_Fred):
        def fetch_series(self, series_id, units, limit):  # FRED already has 06-25
            return SeriesResult([Observation("2026-06-25", 4.41), Observation("2026-06-24", 4.50)],
                                last_updated="2026-06-25")
    r = TreasuryYieldSource(_Caught(), session=_Session(), month="202606").fetch_series("DGS10", "lin", 2600)
    assert _tip(r.observations[0]) == ("2026-06-25", 4.41)   # FRED's own value, not the graft
    assert len(r.observations) == 2                          # nothing prepended (no duplicate day)


def test_treasury_failure_leaves_fred_untouched():
    r = TreasuryYieldSource(_Fred(), session=_Session(boom=True), month="202606").fetch_series("DGS10", "lin", 2600)
    assert _tip(r.observations[0]) == ("2026-06-24", 4.50)   # FRED stands; cron never breaks on a tip
    assert r.last_updated == "2026-06-24"


# ── month boundary ───────────────────────────────────────────────────────────────
# The feed is filed per calendar month. On the 1st (esp. a weekend 1st) the current file is an
# empty 200 and the freshest yield is still in LAST month's file. Querying only the current month
# dropped the tip silently and let FRED's day-late number stand. Live on Sat 2026-08-01: the app
# served the 10Y at 4.68 (Jul 30) while Treasury had 4.75 for Jul 31.

def test_falls_back_to_previous_month_when_this_month_has_no_business_day():
    sess = _MonthSession({"202607": _XML})           # 202608 -> empty, 202607 -> has rows
    r = TreasuryYieldSource(_Fred(), session=sess, month="202608").fetch_series("DGS10", "lin", 2600)
    assert sess.months == ["202608", "202607"]       # tried this month, then fell back
    assert _tip(r.observations[0]) == ("2026-06-25", 4.40)   # the tip survives the boundary


def test_previous_month_is_not_fetched_when_this_month_has_rows():
    """Mid-month must stay a single request — the fallback is not a permanent doubling."""
    sess = _MonthSession({"202606": _XML})
    TreasuryYieldSource(_Fred(), session=sess, month="202606").fetch_series("DGS10", "lin", 2600)
    assert sess.months == ["202606"]


def test_january_rolls_back_to_december_of_the_prior_year():
    sess = _MonthSession({"202512": _XML})
    r = TreasuryYieldSource(_Fred(), session=sess, month="202601").fetch_series("DGS10", "lin", 2600)
    assert sess.months == ["202601", "202512"]
    assert _tip(r.observations[0]) == ("2026-06-25", 4.40)


def test_empty_month_alone_is_no_longer_reported_as_degraded(monkeypatch):
    """The false alarm itself. An empty CURRENT month is a calendar fact, not a degradation —
    flagging it paged hello@modeo.io about a perfectly healthy feed on 2026-08-01 and 08-02."""
    from macro_pipeline import observability
    observability.reset_run_signals()
    sess = _MonthSession({"202607": _XML})
    TreasuryYieldSource(_Fred(), session=sess, month="202608").fetch_series("DGS10", "lin", 2600)
    signals = observability.drain_run_signals()
    assert signals["sources"]["treasury"]["degraded"] is False
    assert signals["sources"]["treasury"]["failures"] == 0


def test_both_months_empty_is_still_a_real_degradation():
    """The flag must keep its teeth — two empty months in a row is not a calendar fact."""
    from macro_pipeline import observability
    observability.reset_run_signals()
    sess = _MonthSession({})                          # every month empty
    r = TreasuryYieldSource(_Fred(), session=sess, month="202608").fetch_series("DGS10", "lin", 2600)
    signals = observability.drain_run_signals()
    assert signals["sources"]["treasury"]["degraded"] is True
    assert _tip(r.observations[0]) == ("2026-06-24", 4.50)   # and FRED still stands


def test_default_session_sends_a_browser_user_agent():
    """treasury.gov tarpits non-browser UAs to ~18s (> our 15s timeout): the graft silently died
    for 22 straight runs, 2026-08-24 -> 09-03. A default-built source must carry the fast UA."""
    from macro_pipeline.treasury_client import BROWSER_UA
    src = TreasuryYieldSource(_Fred(), month="202606")   # no injected session -> real one
    assert src.session.headers.get("User-Agent") == BROWSER_UA
    assert "python-requests" not in src.session.headers.get("User-Agent", "")
