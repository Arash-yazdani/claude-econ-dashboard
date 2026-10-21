"""V4 P1 origin grafts: NY Fed EFFR -> DFF, Cboe VIX -> VIXCLS, FiscalData daily debt -> GFDEBTN,
and the Treasury REAL yield curve -> DFII10. Same contract as every graft: newer-only, never
raise, pass everything else through, degrade to FRED on any failure."""

from __future__ import annotations

import json

from macro_pipeline.cboe_client import CboeSource
from macro_pipeline.fiscaldata_client import FiscalDataSource
from macro_pipeline.fred_client import SeriesResult
from macro_pipeline.models import Observation
from macro_pipeline.nyfed_client import NyFedSource
from macro_pipeline.treasury_client import TreasuryYieldSource


class _Resp:
    def __init__(self, body=None, text="", boom=False):
        self._body, self.text, self._boom = body, text, boom
        self.content = text.encode() if isinstance(text, str) else text

    def raise_for_status(self):
        if self._boom:
            raise RuntimeError("HTTP 500")

    def json(self):
        return self._body


class _Session:
    def __init__(self, body=None, text="", boom=False):
        self._r = _Resp(body, text, boom)
        self.calls = 0

    def get(self, url, timeout=None, **kwargs):  # kwargs: EiaSource passes params=
        self.calls += 1
        return self._r


class _Fred:
    """Backbone: every grafted series has newest = 2026-08-17; SP500 is the pass-through control."""

    def fetch_series(self, series_id, units, limit):
        data = {
            "DFF":     [Observation("2026-08-17", 3.63), Observation("2026-08-16", 3.63)],
            "VIXCLS":  [Observation("2026-08-17", 15.19), Observation("2026-08-14", 14.25)],
            "GFDEBTN": [Observation("2026-04-01", 39065421.0), Observation("2026-01-01", 38700000.0)],
            "DFII10":  [Observation("2026-08-17", 2.41), Observation("2026-08-14", 2.40)],
            "SP500":   [Observation("2026-08-17", 7745.06)],
        }[series_id]
        return SeriesResult(observations=data, last_updated="2026-08-17")


def _tip(o):
    return (o.date, o.value)


# ── NY Fed EFFR ──────────────────────────────────────────────────────────────────

_EFFR = {"refRates": [
    {"effectiveDate": "2026-08-18", "type": "EFFR", "percentRate": 3.63},
    {"effectiveDate": "2026-08-17", "type": "EFFR", "percentRate": 3.63},
]}


def test_effr_grafts_only_the_strictly_newer_day():
    r = NyFedSource(_Fred(), session=_Session(body=_EFFR)).fetch_series("DFF", "lin", 2600)
    assert _tip(r.observations[0]) == ("2026-08-18", 3.63)
    assert _tip(r.observations[1]) == ("2026-08-17", 3.63)   # FRED history intact, no duplicate
    assert len(r.observations) == 3
    assert r.last_updated == "2026-08-18"


def test_effr_equals_dff_on_overlapping_days():
    """fed_funds is CORE + a classifier input: the graft must never change the number, only the
    freshness. On every overlapping date the NY Fed value must equal FRED's."""
    fred = _Fred().fetch_series("DFF", "lin", 2600)
    by_date = {o.date: o.value for o in fred.observations}
    for row in _EFFR["refRates"]:
        if row["effectiveDate"] in by_date:
            assert row["percentRate"] == by_date[row["effectiveDate"]]


def test_effr_failure_leaves_fred_untouched():
    r = NyFedSource(_Fred(), session=_Session(boom=True)).fetch_series("DFF", "lin", 2600)
    assert _tip(r.observations[0]) == ("2026-08-17", 3.63)


def test_effr_non_dff_passes_through():
    r = NyFedSource(_Fred(), session=_Session(body=_EFFR)).fetch_series("SP500", "lin", 2600)
    assert len(r.observations) == 1


# ── Cboe VIX ─────────────────────────────────────────────────────────────────────

_VIX_CSV = "DATE,OPEN,HIGH,LOW,CLOSE\n08/14/2026,14.64,14.72,14.18,14.25\n" \
           "08/17/2026,14.98,15.47,14.89,15.19\n08/18/2026,15.81,16.09,15.60,15.84\n"


def test_vix_grafts_the_t0_close():
    r = CboeSource(_Fred(), session=_Session(text=_VIX_CSV)).fetch_series("VIXCLS", "lin", 2600)
    assert _tip(r.observations[0]) == ("2026-08-18", 15.84)   # Cboe's close, a day ahead of FRED
    assert _tip(r.observations[1]) == ("2026-08-17", 15.19)
    assert len(r.observations) == 3


def test_vix_failure_leaves_fred_untouched():
    r = CboeSource(_Fred(), session=_Session(boom=True)).fetch_series("VIXCLS", "lin", 2600)
    assert _tip(r.observations[0]) == ("2026-08-17", 15.19)


# ── FiscalData daily debt ────────────────────────────────────────────────────────

_DEBT = {"data": [
    {"record_date": "2026-08-18", "tot_pub_debt_out_amt": "40047425768420.22"},
    {"record_date": "2026-08-17", "tot_pub_debt_out_amt": "40031000000000.00"},
]}


def test_debt_grafts_daily_points_in_millions():
    r = FiscalDataSource(_Fred(), session=_Session(body=_DEBT)).fetch_series("GFDEBTN", "lin", 2600)
    assert r.observations[0].date == "2026-08-18"
    assert r.observations[0].value == round(40047425768420.22 / 1e6, 1)   # dollars -> MILLIONS
    # BOTH daily points graft, so compute_delta reads day-over-day, not day-vs-last-quarter.
    assert r.observations[1].date == "2026-08-17"
    assert r.observations[2].date == "2026-04-01"                          # quarterly backbone intact


def test_debt_value_is_plausible_for_the_gate():
    from macro_pipeline.config import INDICATORS_BY_ID
    spec = INDICATORS_BY_ID["federal_debt_gdp"]
    v = round(float(_DEBT["data"][0]["tot_pub_debt_out_amt"]) / 1e6, 1)
    assert spec.plausible_min <= v <= spec.plausible_max


def test_debt_failure_leaves_fred_untouched():
    r = FiscalDataSource(_Fred(), session=_Session(boom=True)).fetch_series("GFDEBTN", "lin", 2600)
    assert r.observations[0].date == "2026-04-01"


# ── Treasury REAL yield curve (DFII10) ───────────────────────────────────────────

_REAL_XML = """<?xml version="1.0"?>
<feed xmlns:d="http://schemas.microsoft.com/ado/2007/08/dataservices"
      xmlns:m="http://schemas.microsoft.com/ado/2007/08/dataservices/metadata">
  <entry><content><m:properties>
    <d:NEW_DATE>2026-08-18T00:00:00</d:NEW_DATE>
    <d:TC_10YEAR>2.45</d:TC_10YEAR>
  </m:properties></content></entry>
</feed>"""


class _MonthSession(_Session):
    def __init__(self, text):
        super().__init__(text=text)
        self.urls = []

    def get(self, url, timeout=None):
        self.urls.append(url)
        self.calls += 1
        return self._r


def test_real_curve_grafts_dfii10_via_tc_field():
    sess = _MonthSession(_REAL_XML)
    r = TreasuryYieldSource(_Fred(), session=sess, month="202608").fetch_series("DFII10", "lin", 2600)
    assert _tip(r.observations[0]) == ("2026-08-18", 2.45)
    assert "daily_treasury_real_yield_curve" in sess.urls[0]   # the REAL feed, not the nominal one


# ── EIA Brent spot (P2) ──────────────────────────────────────────────────────────

from macro_pipeline.eia_client import EiaSource       # noqa: E402
from macro_pipeline.pmms_client import PmmsSource     # noqa: E402

_EIA = {"response": {"data": [
    {"period": "2026-08-19", "value": 94.80},
    {"period": "2026-08-18", "value": 95.29},
]}}


class _Fred2(_Fred):
    def fetch_series(self, series_id, units, limit):
        extra = {
            "DCOILBRENTEU": SeriesResult([Observation("2026-08-18", 95.29),
                                          Observation("2026-08-17", 92.43)], "2026-08-18"),
            "MORTGAGE30US": SeriesResult([Observation("2026-08-06", 6.69),
                                          Observation("2026-07-30", 6.72)], "2026-08-06"),
        }
        if series_id in extra:
            return extra[series_id]
        return super().fetch_series(series_id, units, limit)


def test_eia_grafts_the_newer_spot_day():
    r = EiaSource(_Fred2(), api_key="K", session=_Session(body=_EIA)).fetch_series("DCOILBRENTEU", "lin", 2600)
    assert _tip(r.observations[0]) == ("2026-08-19", 94.80)
    assert _tip(r.observations[1]) == ("2026-08-18", 95.29)   # no duplicate of the overlap day
    assert len(r.observations) == 3


def test_eia_without_key_is_a_pure_noop():
    sess = _Session(body=_EIA)
    r = EiaSource(_Fred2(), api_key=None, session=sess).fetch_series("DCOILBRENTEU", "lin", 2600)
    assert _tip(r.observations[0]) == ("2026-08-18", 95.29)   # FRED stands
    assert sess.calls == 0                                     # no request was even built


def test_eia_failure_leaves_fred_untouched():
    r = EiaSource(_Fred2(), api_key="K", session=_Session(boom=True)).fetch_series("DCOILBRENTEU", "lin", 2600)
    assert _tip(r.observations[0]) == ("2026-08-18", 95.29)


# ── Freddie PMMS 30Y (P2) ────────────────────────────────────────────────────────

_PMMS_CSV = "date,pmms30,pmms30p,pmms15,pmms15p,pmms51,pmms51p,pmms51m,pmms51spread\n" \
            "8/6/2026,6.69,,6.01,,,,,\n8/13/2026,6.67,,5.96,,,,,\n"


def test_pmms_grafts_the_newer_week():
    r = PmmsSource(_Fred2(), session=_Session(text=_PMMS_CSV)).fetch_series("MORTGAGE30US", "lin", 2600)
    assert _tip(r.observations[0]) == ("2026-08-13", 6.67)    # Freddie's fresh Thursday print
    assert _tip(r.observations[1]) == ("2026-08-06", 6.69)    # FRED history intact, no duplicate
    assert len(r.observations) == 3


def test_pmms_failure_leaves_fred_untouched():
    r = PmmsSource(_Fred2(), session=_Session(boom=True)).fetch_series("MORTGAGE30US", "lin", 2600)
    assert _tip(r.observations[0]) == ("2026-08-06", 6.69)


# ── BEA real GDP (P3) ────────────────────────────────────────────────────────────

from macro_pipeline.bea_client import BeaSource       # noqa: E402
from macro_pipeline.census_client import CensusSource # noqa: E402

_BEA = {"BEAAPI": {"Results": {"Data": [
    {"SeriesCode": "A191RX", "LineNumber": "1", "TimePeriod": "2026Q2", "DataValue": "23,700.0"},
    {"SeriesCode": "A191RX", "LineNumber": "1", "TimePeriod": "2026Q1", "DataValue": "23,512.0"},
    {"SeriesCode": "A191RX", "LineNumber": "1", "TimePeriod": "2025Q2", "DataValue": "23,200.0"},
    {"SeriesCode": "DPCERX", "LineNumber": "2", "TimePeriod": "2026Q2", "DataValue": "16,000.0"},
]}}}


class _Fred3(_Fred2):
    def fetch_series(self, series_id, units, limit):
        if series_id == "GDPC1" and units == "pc1":
            return SeriesResult([Observation("2026-01-01", 2.10182),
                                 Observation("2025-10-01", 2.0)], "2026-05-29")
        if series_id == "GDPC1":  # the lin raw-level refetch
            return SeriesResult([Observation("2026-01-01", 23512.0)], "2026-05-29")
        if series_id == "HOUST":
            return SeriesResult([Observation("2026-06-01", 1239.0),
                                 Observation("2026-05-01", 1250.0)], "2026-07-17")
        return super().fetch_series(series_id, units, limit)


def test_bea_grafts_the_newer_quarter_as_yoy():
    r = BeaSource(_Fred3(), api_key="K", session=_Session(body=_BEA),
                  years="2024,2025,2026").fetch_series("GDPC1", "pc1", 40)
    assert r.observations[0].date == "2026-04-01"                     # Q2, newer than FRED's Q1
    assert abs(r.observations[0].value - (23700.0 / 23200.0 - 1) * 100) < 1e-4   # same-convention YoY
    assert r.observations[1].value == 2.10182                          # FRED history intact


def test_bea_lin_refetch_is_left_to_fred():
    sess = _Session(body=_BEA)
    r = BeaSource(_Fred3(), api_key="K", session=sess, years="x").fetch_series("GDPC1", "lin", 70)
    assert r.observations[0].value == 23512.0
    assert sess.calls == 0                                             # pc1 only; lin never fetches BEA


def test_bea_without_key_is_a_pure_noop():
    sess = _Session(body=_BEA)
    r = BeaSource(_Fred3(), api_key=None, session=sess).fetch_series("GDPC1", "pc1", 40)
    assert r.observations[0].value == 2.10182
    assert sess.calls == 0


def test_bea_failure_leaves_fred_untouched():
    r = BeaSource(_Fred3(), api_key="K", session=_Session(boom=True),
                  years="x").fetch_series("GDPC1", "pc1", 40)
    assert r.observations[0].value == 2.10182


# ── Census housing starts (P3) ───────────────────────────────────────────────────

_CENSUS = [["cell_value", "time_slot_id", "category_code", "data_type_code", "seasonally_adj", "time"],
           ["1228", "0", "ASTARTS", "TOTAL", "yes", "2026-07"],
           ["1239", "0", "ASTARTS", "TOTAL", "yes", "2026-06"]]


def test_census_grafts_the_newer_month():
    r = CensusSource(_Fred3(), api_key="K", session=_Session(body=_CENSUS),
                     year=2026).fetch_series("HOUST", "lin", 2600)
    assert _tip(r.observations[0]) == ("2026-07-01", 1228.0)
    assert _tip(r.observations[1]) == ("2026-06-01", 1239.0)          # FRED's own month, no duplicate
    assert len(r.observations) == 3


def test_census_without_key_is_a_pure_noop():
    sess = _Session(body=_CENSUS)
    r = CensusSource(_Fred3(), api_key=None, session=sess).fetch_series("HOUST", "lin", 2600)
    assert _tip(r.observations[0]) == ("2026-06-01", 1239.0)
    assert sess.calls == 0


def test_census_failure_leaves_fred_untouched():
    r = CensusSource(_Fred3(), api_key="K", session=_Session(boom=True),
                     year=2026).fetch_series("HOUST", "lin", 2600)
    assert _tip(r.observations[0]) == ("2026-06-01", 1239.0)
