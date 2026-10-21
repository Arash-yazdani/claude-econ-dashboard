"""W6b — the accuracy report must never overstate (offline)."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load():
    name = "build_accuracy_report"
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / "build_accuracy_report.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


ar = _load()


def _entry(rows):
    return {"timestamp": "2026-08-07T22:04:41", "as_of_date": "2026-08-07",
            "checked": sum(1 for r in rows if r["verdict"] in ("matched", "mismatched")),
            "rows": rows}


CLEAN = [
    {"id": "unemployment", "as_of": "2026-07-01", "published": 4.1, "source": 4.1,
     "verdict": "matched", "note": ""},
    {"id": "core_cpi_yoy", "as_of": "2026-06-01", "published": 2.57, "source": 2.57,
     "verdict": "matched", "note": ""},
    {"id": "yield_curve_2s10s", "as_of": "2026-08-06", "published": 0.45, "source": None,
     "verdict": "not_reconciled", "note": "derived row — computed here, not downloaded"},
]


def test_a_clean_report_states_the_count_and_carries_all_three_caveats():
    """The caveat assertion is the point: it is what stops someone quietly deleting the scope
    limitations, which is the exact overstatement a controls reviewer is trained to distrust."""
    s = ar.summarize_rows(_entry(CLEAN))
    for text in (ar.render(s), ar.render_email(s)):
        assert "All 2 numbers we could re-check matched" in text
        for caveat in ar.CAVEATS:
            probe = caveat[:60]
            assert probe in text.replace("&quot;", '"'), f"caveat missing: {probe!r}"
        assert "empty" in text.lower()          # "an empty table/list is the good outcome"


def test_a_mismatch_lands_in_the_exceptions_table_and_the_verdict_does_not_say_matched():
    bad = CLEAN[:1] + [{"id": "treasury_10y", "as_of": "2026-08-06", "published": 4.65,
                        "source": 4.75, "verdict": "mismatched", "note": ""}]
    s = ar.summarize_rows(_entry(bad))
    for text in (ar.render(s), ar.render_email(s)):
        assert "did NOT match" in text
        assert "All 2 numbers we could re-check matched" not in text
        assert "treasury_10y" in text
        assert "4.75" in text                   # the source's number is shown, not hidden


def test_history_counts_distinct_dates_not_lines(tmp_path):
    """Reconciliation runs on the PM path only; a re-run day must not double-count itself into
    a longer clean streak than actually happened."""
    p = tmp_path / "recon.jsonl"
    e1 = _entry(CLEAN); e1["timestamp"] = "2026-08-06T22:00:00"
    e2 = _entry(CLEAN); e2["timestamp"] = "2026-08-07T22:00:00"
    e3 = _entry(CLEAN); e3["timestamp"] = "2026-08-07T23:00:00"   # same-day re-run
    p.write_text("\n".join(json.dumps(e) for e in (e1, e2, e3)) + "\n")
    days, mism, checked = ar.history_days(p)
    assert days == 2                            # distinct dates, not 3 lines
    assert mism == 0 and checked == 4           # 2 checked per distinct day
