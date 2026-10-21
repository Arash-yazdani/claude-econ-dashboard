import importlib.util
import pathlib

import anthropic
import httpx2 as httpx

spec = importlib.util.spec_from_file_location("probe_models", pathlib.Path(__file__).parents[1] / "scripts/probe_models.py")
probe_models = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe_models)


def _err(cls, code):
    req = httpx.Request("POST", "https://x")
    return cls("x", response=httpx.Response(code, request=req), body=None)


class FakeMessages:
    def __init__(self, errors):
        self.errors, self.calls = errors, []

    def create(self, **kw):
        self.calls.append(kw)
        if kw["model"] in self.errors:
            raise self.errors[kw["model"]]


class FakeClient:
    def __init__(self, errors):
        self.messages = FakeMessages(errors)


def test_probe_generates_and_flags_403_and_404(monkeypatch):
    for v in ("CLAUDE_PRIMARY_MODEL", "CLAUDE_FAILOVER_MODEL", "CLAUDE_JUDGE_MODEL"):
        monkeypatch.delenv(v, raising=False)
    m = probe_models.models()
    c = FakeClient({m["writer"]: _err(anthropic.PermissionDeniedError, 403),
                    m["failover"]: _err(anthropic.NotFoundError, 404)})
    dead = probe_models.probe(c)
    # judge shares the failover model (Sonnet), so it is dead too
    assert [d.split()[0] for d in dead] == ["writer", "failover", "judge"]
    assert "(403)" in dead[0] and "(404)" in dead[1]
    assert all(k["max_tokens"] == 1 for k in c.messages.calls)


def test_probe_other_errors_are_not_verdicts():
    c = FakeClient({probe_models.models()["writer"]: _err(anthropic.InternalServerError, 500)})
    assert probe_models.probe(c) == []
