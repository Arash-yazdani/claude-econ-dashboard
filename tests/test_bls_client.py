"""BlsSource grafts BLS's same-day release tip onto FRED's BLS-origin series (CPI, jobs).

Mirrors the TreasuryYieldSource tests: a fake FRED backbone + a fake requests session serving a
canned BLS JSON body, so nothing touches the network. `today` is injected so the 3-year window is
deterministic.
"""

from __future__ import annotations

import json

from macro_pipeline.bls_client import BlsSource
from macro_pipeline.fred_client import SeriesResult
from macro_pipeline.models import Observation


def _bls_body(series: dict[str, list[tuple[str, str, str]]]) -> dict:
    """series: {bls_id: [(year, 'M07', value), ...]} -> a REQUEST_SUCCEEDED BLS payload."""
    return {
        "status": "REQUEST_SUCCEEDED",
        "Results": {"series": [
            {"seriesID": sid, "data": [
                {"year": y, "period": p, "periodName": "x", "value": v} for (y, p, v) in rows
            ]} for sid, rows in series.items()
        ]},
    }


class _Resp:
    def __init__(self, body, boom=False):
        self._body, self._boom = body, boom

    def raise_for_status(self):
        if self._boom:
            raise RuntimeError("HTTP 500")

    def json(self):
        return self._body


class _Session:
    def __init__(self, body, boom=False):
        self._body, self._boom = body, boom
        self.posts: list[dict] = []

    def post(self, url, json=None, timeout=None):  # noqa: A002 - mirror requests' signature
        self.posts.append(json)
        return _Resp(self._body, boom=self._boom)


class _Fred:
    """Fake FRED backbone. CPIAUCNS newest = Jun 2026 (pc1 3.1); UNRATE newest = Jun; SP500 control."""

    def __init__(self, cpi_newest="2026-06-01"):
        self._cpi_newest = cpi_newest

    def fetch_series(self, series_id, units, limit):
        data = {
            "CPIAUCNS": [Observation(self._cpi_newest, 3.1), Observation("2026-05-01", 3.0)],
            "UNRATE":   [Observation("2026-06-01", 4.3), Observation("2026-05-01", 4.2)],
            "PAYEMS":   [Observation("2026-06-01", 160.0), Observation("2026-05-01", 140.0)],
            "SP500":    [Observation("2026-06-23", 7365.46)],
        }[series_id]
        return SeriesResult(observations=data, last_updated=self._cpi_newest)


# 13 monthly index points so the YoY (pc1) graft can compute: Jul 2026 = 333.918 (the fresh print),
# Jul 2025 = 322.9 (the same-month prior-year YoY denominator). One clean row per month.
_CPI_ROWS = [
    ("2026", "M07", "333.918"),
    ("2026", "M06", "333.0"), ("2026", "M05", "332.0"), ("2026", "M04", "331.0"),
    ("2026", "M03", "330.0"), ("2026", "M02", "329.0"), ("2026", "M01", "328.0"),
    ("2025", "M12", "327.0"), ("2025", "M11", "326.0"), ("2025", "M10", "325.0"),
    ("2025", "M09", "324.0"), ("2025", "M08", "323.0"), ("2025", "M07", "322.9"),
]


def _body_with_cpi():
    return _bls_body({"CUUR0000SA0": _CPI_ROWS})


def test_grafts_fresher_nsa_cpi_yoy_tip():
    # FRED only has June; BLS has the fresh July NSA print -> graft a computed YoY tip for July.
    sess = _Session(_body_with_cpi())
    r = BlsSource(_Fred(), session=sess, today=__import__("datetime").date(2026, 8, 17)) \
        .fetch_series("CPIAUCNS", "pc1", 2600)
    assert r.observations[0].date == "2026-07-01"                 # July tip prepended
    assert abs(r.observations[0].value - (333.918 / 322.9 - 1) * 100) < 1e-4   # NSA YoY, computed
    assert r.observations[1].date == "2026-06-01"                 # FRED history intact below
    assert r.last_updated == "2026-07-01"


def test_lin_pass_grafts_the_raw_index_level():
    sess = _Session(_body_with_cpi())
    r = BlsSource(_Fred(), session=sess, today=__import__("datetime").date(2026, 8, 17)) \
        .fetch_series("CPIAUCNS", "lin", 70)
    assert r.observations[0].date == "2026-07-01"
    assert r.observations[0].value == 333.918                     # the level itself, not a %


def test_chg_pass_grafts_one_month_net_change():
    body = _bls_body({"CES0000000001": [("2026", "M07", "160500"), ("2026", "M06", "160350")]})
    r = BlsSource(_Fred(), session=_Session(body), today=__import__("datetime").date(2026, 8, 17)) \
        .fetch_series("PAYEMS", "chg", 2600)
    assert r.observations[0].date == "2026-07-01"
    assert r.observations[0].value == 150.0                       # 160500 - 160350 net change


def test_no_graft_when_bls_is_not_strictly_newer():
    # FRED already has July -> BLS July is not strictly newer -> no duplicate prepend.
    sess = _Session(_body_with_cpi())
    r = BlsSource(_Fred(cpi_newest="2026-07-01"), session=sess,
                  today=__import__("datetime").date(2026, 8, 17)).fetch_series("CPIAUCNS", "pc1", 2600)
    assert r.observations[0].date == "2026-07-01"
    assert r.observations[0].value == 3.1                         # FRED's own value, not the graft
    assert len(r.observations) == 2                               # nothing prepended


def test_non_bls_series_passes_through_untouched():
    r = BlsSource(_Fred(), session=_Session(_body_with_cpi())) .fetch_series("SP500", "lin", 2600)
    assert r.observations[0].date == "2026-06-23"
    assert len(r.observations) == 1


def test_bls_failure_leaves_fred_untouched():
    r = BlsSource(_Fred(), session=_Session({}, boom=True),
                  today=__import__("datetime").date(2026, 8, 17)).fetch_series("CPIAUCNS", "pc1", 2600)
    assert r.observations[0].date == "2026-06-01"                 # FRED stands; cron never breaks
    assert r.last_updated == "2026-06-01"


def test_bls_error_status_is_treated_as_failure():
    r = BlsSource(_Fred(), session=_Session({"status": "REQUEST_NOT_PROCESSED", "message": ["cap"]}),
                  today=__import__("datetime").date(2026, 8, 17)).fetch_series("CPIAUCNS", "pc1", 2600)
    assert r.observations[0].date == "2026-06-01"                 # bad status -> no graft, FRED stands


def test_registration_key_is_sent_only_when_present():
    with_key = _Session(_body_with_cpi())
    BlsSource(_Fred(), api_key="SECRET", session=with_key,
              today=__import__("datetime").date(2026, 8, 17)).fetch_series("CPIAUCNS", "lin", 70)
    assert with_key.posts[0]["registrationkey"] == "SECRET"
    without = _Session(_body_with_cpi())
    BlsSource(_Fred(), session=without,
              today=__import__("datetime").date(2026, 8, 17)).fetch_series("CPIAUCNS", "lin", 70)
    assert "registrationkey" not in without.posts[0]


def test_levels_fetched_once_across_calls():
    sess = _Session(_body_with_cpi())
    src = BlsSource(_Fred(), session=sess, today=__import__("datetime").date(2026, 8, 17))
    src.fetch_series("CPIAUCNS", "pc1", 2600)
    src.fetch_series("CPIAUCNS", "lin", 70)
    assert len(sess.posts) == 1                                   # one batched POST per run, cached
