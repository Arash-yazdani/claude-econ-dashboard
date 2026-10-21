"""Guard against silent drift between the two hand-duplicated JS formatting layers.

public/app.js and tools/macro-widget.js each define DEC2 (2-decimal ids) and SIGNED (leading-sign
ids) by hand, and neither has a JS test suite — which is how cpi_yoy read 3.3% on the widget and
3.30% in the app for weeks with nobody noticing. These tests parse both files and pin the exact
relationship, including the ONE deliberate difference:

  core_cpi_yoy is app-DEC2 only: the app shows 2.48% to stay level with the WHY-block basis line
  (which prints the raw 2dp classifier input), while the widget has no WHY block and shows the
  1dp headline 2.5%.

Changing either set now fails here until the other file — or this allowlist — is updated with it.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# The one sanctioned DEC2 difference (app-only), with the reason above.
DEC2_APP_ONLY = {"core_cpi_yoy"}


def _js_set(path: Path, name: str) -> set[str]:
    src = path.read_text(encoding="utf-8")
    m = re.search(rf"const {name} = new Set\(\[(.*?)\]\)", src, re.S)
    assert m, f"{name} set not found in {path.name}"
    return set(re.findall(r'"([a-z0-9_]+)"', m.group(1)))


def test_dec2_sets_differ_only_by_the_sanctioned_id():
    app = _js_set(ROOT / "public" / "app.js", "DEC2")
    widget = _js_set(ROOT / "tools" / "macro-widget.js", "DEC2")
    assert app - widget == DEC2_APP_ONLY, (
        f"app-only DEC2 ids {sorted(app - widget)} != sanctioned {sorted(DEC2_APP_ONLY)}")
    assert widget - app == set(), (
        f"widget DEC2 has ids the app lacks: {sorted(widget - app)}")


def test_signed_sets_are_identical():
    app = _js_set(ROOT / "public" / "app.js", "SIGNED")
    widget = _js_set(ROOT / "tools" / "macro-widget.js", "SIGNED")
    assert app == widget, (
        f"SIGNED drift — app-only: {sorted(app - widget)}, widget-only: {sorted(widget - app)}")
