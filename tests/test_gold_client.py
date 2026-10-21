"""GoldSpotSource serves gold_spot from gold-api.com; passes everything else through (ADR-006).

Two layers: the live tick (/price, always attempted, no key needed) and real daily history
(/history?groupBy=day, needs GOLD_API_KEY, ~10y confirmed live 2026-07-21). Either can fail
independently without ever raising — that's the property most worth locking down here.
"""

from __future__ import annotations

from macro_pipeline import config
from macro_pipeline.fred_client import SeriesResult
from macro_pipeline.gold_client import GOLD_SERIES_ID, GoldSpotSource
from macro_pipeline.models import Observation


def test_config_and_client_agree_on_the_synthetic_series_id():
    # The two files don't import each other (config -> gold_client -> fred_client -> config would
    # cycle), so the id is a literal in both places. If they ever drift, GoldSpotSource's match
    # fails silently and the id falls through to a REAL FRED call with a bogus series — which
    # raises FredError and would fail the whole cron, exactly what ADR-006 says must never happen.
    assert config.INDICATORS_BY_ID["gold_spot"].fred_series_id == GOLD_SERIES_ID


class _Resp:
    def __init__(self, body, status=200):
        self._body = body
        self.status = status

    def raise_for_status(self):
        if self.status >= 400:
            raise RuntimeError(f"HTTP {self.status}")

    def json(self):
        return self._body


_LIVE_BODY = {"price": 3350.5, "updatedAt": "2026-07-21T16:52:04Z"}
_HISTORY_BODY = [  # newest-first, as requested via orderBy=desc
    {"day": "2026-07-21 00:00:00", "avg_price": "3340.0"},   # same day as the live tick -> deduped
    {"day": "2026-07-20 00:00:00", "avg_price": "3300.25"},
    {"day": "2026-07-19 00:00:00", "avg_price": "3280.0"},
]


class _Session:
    """Serves canned bodies keyed by URL; a boom_on set of URLs raises instead."""

    def __init__(self, live=_LIVE_BODY, history=_HISTORY_BODY, boom_on=()):
        self.live, self.history, self.boom_on = live, history, boom_on
        self.calls = []

    def get(self, url, headers=None, params=None, timeout=None):
        self.calls.append((url, headers, params))
        if url in self.boom_on:
            raise RuntimeError("network down")
        if "history" in url:
            if self.history is None:
                return _Resp(None, status=429)  # rate limit / auth failure path
            return _Resp(self.history)
        return _Resp(self.live)


class _Fred:
    """Fake FredSource: DGS10 is the pass-through control."""

    def fetch_series(self, series_id, units, limit):
        assert series_id == "DGS10"
        return SeriesResult(observations=[Observation("2026-07-20", 4.5)], last_updated="2026-07-20")


def test_no_key_is_current_value_only_unchanged_behavior():
    src = GoldSpotSource(_Fred(), session=_Session())
    r = src.fetch_series(GOLD_SERIES_ID, "lin", 10)
    assert len(r.observations) == 1  # history never attempted without a key
    assert r.observations[0] == Observation("2026-07-21", 3350.5)


def test_with_key_merges_live_tick_over_history():
    src = GoldSpotSource(_Fred(), session=_Session(), api_key="k")
    r = src.fetch_series(GOLD_SERIES_ID, "lin", 10)
    dates = [o.date for o in r.observations]
    assert dates == ["2026-07-21", "2026-07-20", "2026-07-19"]  # today deduped, live tick wins
    assert r.observations[0].value == 3350.5   # the precise live tick, not history's same-day avg
    assert r.observations[1].value == 3300.25  # yesterday's real daily average, from /history
    assert r.last_updated == "2026-07-21"


def test_history_request_uses_the_api_key_header_and_daily_groupby():
    session = _Session()
    src = GoldSpotSource(_Fred(), session=session, api_key="secret-key")
    src.fetch_series(GOLD_SERIES_ID, "lin", 10)
    history_calls = [c for c in session.calls if "history" in c[0]]
    assert len(history_calls) == 1
    _, headers, params = history_calls[0]
    assert headers == {"x-api-key": "secret-key"}
    assert params["groupBy"] == "day" and params["aggregation"] == "avg" and params["orderBy"] == "desc"


def test_a_real_day_over_day_delta_is_now_computable():
    from macro_pipeline import deltas
    src = GoldSpotSource(_Fred(), session=_Session(), api_key="k")
    r = src.fetch_series(GOLD_SERIES_ID, "lin", 10)
    assert deltas.compute_delta(r.observations) == round(3350.5 - 3300.25, 4)


def test_history_failure_falls_back_to_the_live_tick_alone():
    src = GoldSpotSource(_Fred(), session=_Session(history=None), api_key="k")  # 429-style failure
    r = src.fetch_series(GOLD_SERIES_ID, "lin", 10)
    assert len(r.observations) == 1
    assert r.observations[0] == Observation("2026-07-21", 3350.5)


def test_live_failure_leaves_history_standing_alone():
    src = GoldSpotSource(_Fred(), session=_Session(boom_on=("https://api.gold-api.com/price/XAU",)), api_key="k")
    r = src.fetch_series(GOLD_SERIES_ID, "lin", 10)
    assert [o.date for o in r.observations] == ["2026-07-21", "2026-07-20", "2026-07-19"]
    assert r.observations[0].value == 3340.0  # history's own same-day average, no live tick to graft


def test_both_fail_degrades_to_a_null_observation_not_a_raise():
    session = _Session(boom_on=("https://api.gold-api.com/price/XAU", "https://api.gold-api.com/history"))
    src = GoldSpotSource(_Fred(), session=session, api_key="k")
    r = src.fetch_series(GOLD_SERIES_ID, "lin", 10)
    assert len(r.observations) == 1        # never empty — _pick_value would raise on []
    assert r.observations[0].value is None  # degrades to "—" in the PWA, never a crash


def test_one_malformed_history_row_does_not_sink_the_rest():
    bad_history = [{"day": "2026-07-20 00:00:00", "avg_price": "not-a-number"},
                    {"day": "2026-07-19 00:00:00", "avg_price": "3280.0"}]
    src = GoldSpotSource(_Fred(), session=_Session(history=bad_history), api_key="k")
    r = src.fetch_series(GOLD_SERIES_ID, "lin", 10)
    dates = [o.date for o in r.observations]
    assert "2026-07-20" not in dates    # the bad row was dropped, not crashed on
    assert "2026-07-19" in dates        # the good row after it still made it through


def test_non_gold_series_passes_through_untouched():
    src = GoldSpotSource(_Fred(), session=_Session(), api_key="k")
    r = src.fetch_series("DGS10", "lin", 10)
    assert r.observations[0].value == 4.5
