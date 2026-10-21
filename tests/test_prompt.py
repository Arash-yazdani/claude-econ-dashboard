"""B3 prompt assembly — static→dynamic XML, all 7 indicators, request passthrough."""

from __future__ import annotations

import json
from pathlib import Path

from macro_pipeline import prompt

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = json.loads((ROOT / "tests" / "fixtures" / "regression-cases-v1.0.json").read_text())
T1 = next(c for c in FIXTURES["cases"] if c["id"] == "T1")["input"]


def test_build_messages_has_system_and_user():
    m = prompt.build_messages(T1)
    assert "<role>" in m["system"]
    assert "data_unavailable" in m["system"]  # refusal envelope present
    assert m["user"]


def test_user_message_is_static_then_dynamic():
    user = prompt.build_messages(T1)["user"]
    assert user.index("<indicator_metadata>") < user.index("<regime_rules>") < user.index("<todays_data>")


def test_todays_data_carries_the_request_verbatim():
    user = prompt.build_messages(T1)["user"]
    assert '"regime_today": "soft_landing"' in user
    assert "2.9" in user  # a T1 indicator value rendered into <todays_data>


def test_indicator_metadata_lists_core_plus_context():
    from macro_pipeline.config import BRIEFING_CONTEXT_IDS
    block = prompt.indicator_metadata_block()
    for ind_id in ("cpi_yoy", "core_cpi_yoy", "unemployment", "real_gdp_yoy",
                   "treasury_10y", "fed_funds", "umich_sentiment"):
        assert f'id="{ind_id}"' in block
    # V3 context handful must mirror config.BRIEFING_CONTEXT_IDS — the LLM may only cite an
    # indicator that appears in the metadata, and O3 whitelists by the request's ids, so the
    # metadata and the request must agree on exactly this set.
    for cid in BRIEFING_CONTEXT_IDS:
        assert f'id="{cid}"' in block


def test_prompt_is_v15_locked_with_few_shot_examples():
    """Renamed from ..._is_v14_... at the v1.5 bump. The version assertion is deliberately not
    loosened to a prefix-free check: this test failing IS the signal that prompt.py changed and
    Runbook 6 (regression + Bruce ack + prompt_history row) is now owed."""
    assert prompt.PROMPT_VERSION.startswith("1.5")  # v1.5 = Bruce D+1 causal style
    sys = prompt.SYSTEM_INSTRUCTIONS
    assert "<examples>" in sys and "</examples>" in sys
    assert sys.count("OUTPUT:") + sys.count("OUTPUT (") == 3  # 3 paired few-shots
    assert "data_unavailable" in sys  # the refusal example


def test_v15_guards_that_make_the_new_style_safe():
    """The causal style invites two failures the old terse style could not reach."""
    sys = prompt.SYSTEM_INSTRUCTIONS
    # Without this the writer reaches for "the Fed's 2 percent target" -> 2.0 is not in
    # <todays_data> -> O5 rejects -> retry -> fallback. The style change pays for itself only
    # if the number never appears.
    assert "QUALITATIVELY" in sys and "NEVER state it as a number" in sys
    # Bruce's sample said "global uncertainty". Fixture A2 exists to prove the writer stays
    # domestic; a "global backdrop" clause is exactly the pressure that breaks it.
    assert "outside the united states" in sys.lower()
    # The echo instruction — the actual fix for 99/109 identical headlines.
    assert "NEVER REUSE AN EXAMPLE" in sys


def test_examples_do_not_teach_an_ungrounded_direction_verb():
    """EXAMPLE 1 carries no release and no delta, so a direction verb there would teach the
    writer to assert movement it cannot see. EXAMPLE 2 has a prior value and may."""
    sys = prompt.SYSTEM_INSTRUCTIONS
    ex1 = sys[sys.index("EXAMPLE 1"):sys.index("EXAMPLE 2")]
    for verb in ("eased", "rose", "fell", "climbed", "cooled", "jumped"):
        assert verb not in ex1.lower(), f"EXAMPLE 1 teaches {verb!r} with no delta in its input"
