"""W2 — the regime id set must not drift across the ~12 files that hardcode it.

The five ids are duplicated in Python (models.REGIME_LABELS, classifier branches,
validator.REGIME_PLAIN, prompt.REGIME_RULES_BLOCK, judge/persona_judge, backtest) AND in four
non-Python files that no import can reach: the request schema, app.js, styles.css, and the
Scriptable widget. Adding a sixth regime means editing all of them, and the non-Python four fail
SILENTLY — a missing CSS rule is an unstyled card, a missing ACCENT key paints the wrong colour,
a missing schema enum entry rejects a valid request.

DELIBERATELY THE LAZY FORM: one test asserting presence, not a canonical registry with six
parsers. The set changes about once a year; a registry plus codegen would be more machinery than
the problem deserves. This catches the omission at the only moment it matters — the commit that
adds or renames a regime.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from macro_pipeline.models import REGIME_LABELS

ROOT = Path(__file__).resolve().parents[1]

# file -> how the id appears there. Checking the RAW id (not a prose label) on purpose: these are
# the lookup keys, and a typo'd key is exactly the silent failure this guards.
NON_PYTHON_SOURCES = {
    "schemas/briefing-request.schema.json": "request schema enum",
    "public/app.js": "REGIME_MEANING lookup",
    "public/styles.css": "html[data-regime=...] accent rule",
    "tools/macro-widget.js": "ACCENT lookup (widget)",
}


@pytest.mark.parametrize("relpath,what", sorted(NON_PYTHON_SOURCES.items()))
def test_every_regime_id_appears_in_each_non_python_source(relpath, what):
    text = (ROOT / relpath).read_text(encoding="utf-8")
    missing = [r for r in REGIME_LABELS if r not in text]
    assert not missing, f"{relpath} ({what}) is missing regime ids: {missing}"


def test_python_side_agrees_with_models():
    """validator.REGIME_PLAIN is what daily.build_card reads to produce regime_label; a missing
    entry yields regime_label="" and app.js hides the ENTIRE regime block — taking W1's basis
    line down with it. That failure mode is why this one is asserted rather than assumed."""
    from macro_pipeline.validator import REGIME_PLAIN
    missing = [r for r in REGIME_LABELS if r not in REGIME_PLAIN]
    assert not missing, f"validator.REGIME_PLAIN is missing: {missing}"


def test_the_prompt_describes_every_regime():
    """The writer is told the rule set; an unnamed regime would be narrated from the label alone."""
    from macro_pipeline import prompt
    block = getattr(prompt, "REGIME_RULES_BLOCK", "") or prompt.SYSTEM_PROMPT
    missing = [r for r in REGIME_LABELS if r not in block]
    assert not missing, f"the prompt does not describe: {missing}"


# ── W3: `indeterminate` is a STATUS, deliberately NOT in REGIME_LABELS ────────────
# It is the classifier declining to assert, not a sixth thing it can assert. Keeping it out of
# REGIME_LABELS is what keeps it out of the O3 threshold conversation — and out of the prompt,
# since the writer never narrates it (the refusal contract handles that day).
#
# But it still needs every DISPLAY entry the five have, and those fail silently. Hence this.

INDETERMINATE_DISPLAY_SOURCES = {
    "public/app.js": "REGIME_MEANING entry",
    "public/styles.css": "html[data-regime=indeterminate] accent",
    "tools/macro-widget.js": "ACCENT entry",
}


@pytest.mark.parametrize("relpath,what", sorted(INDETERMINATE_DISPLAY_SOURCES.items()))
def test_indeterminate_has_its_display_entry(relpath, what):
    assert "indeterminate" in (ROOT / relpath).read_text(encoding="utf-8"), \
        f"{relpath} ({what}) has no `indeterminate` entry — the card degrades silently"


def test_indeterminate_has_a_plain_label():
    """The nastiest omission of the four: build_card does REGIME_PLAIN.get(regime, ""), and an
    empty label makes app.js hide the ENTIRE regime block — taking W1's basis line with it."""
    from macro_pipeline.validator import REGIME_PLAIN
    assert REGIME_PLAIN.get("indeterminate")


def test_indeterminate_is_not_a_regime_label():
    assert "indeterminate" not in REGIME_LABELS


def test_no_display_default_falls_back_to_the_reassuring_label():
    """All three defaults used to be `soft_landing`, so an unknown or absent regime rendered as
    the most reassuring of five. They now default to indeterminate."""
    for relpath in ("public/index.html", "public/app.js", "tools/macro-widget.js"):
        text = (ROOT / relpath).read_text(encoding="utf-8")
        for bad in ('|| "soft_landing"', "|| ACCENT.soft_landing", 'data-regime="soft_landing"'):
            assert bad not in text, f"{relpath} still defaults to soft_landing via {bad!r}"
