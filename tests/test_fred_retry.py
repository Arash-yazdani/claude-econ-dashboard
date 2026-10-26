from types import SimpleNamespace

import pytest

from macro_pipeline.fred_client import FredClient, FredError


class _Session:
    def __init__(self, codes):
        self.codes = list(codes)
        self.calls = 0

    def get(self, url, params, timeout):
        self.calls += 1
        code = self.codes.pop(0)
        return SimpleNamespace(status_code=code, headers={}, text="x",
                               json=lambda: {"ok": True})


def _client(codes):
    c = FredClient(api_key="k", session=_Session(codes))
    c._sleep = lambda s: None
    return c


def test_retries_429_then_succeeds():
    c = _client([429, 502, 200])
    assert c._get("series", {}) == {"ok": True}
    assert c.session.calls == 3


def test_gives_up_after_retries():
    c = _client([429] * 4)
    with pytest.raises(FredError, match="429"):
        c._get("series", {})
    assert c.session.calls == 4


def test_no_retry_on_400():
    c = _client([400])
    with pytest.raises(FredError, match="400"):
        c._get("series", {})
    assert c.session.calls == 1
