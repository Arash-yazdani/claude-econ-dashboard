"""One scoring per card (2026-08-31 judge-daily page), and a judge outage is "cannot measure".

The eval step fires on every cron firing, including de-dupe skips, so each card was scored ~5x
against a threshold inside the judge's own noise. And an unreachable judge was counted as a
FAILED check — "cannot measure" reported as "failed", the 2026-08-04 lesson.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from run_daily_eval import already_scored  # noqa: E402

from macro_pipeline import quality  # noqa: E402


def _log(tmp_path, rows):
    p = tmp_path / "daily_eval.csv"
    with open(p, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["timestamp", "as_of_date", "run_label", "overall_score"])
        w.writeheader()
        w.writerows(rows)
    return str(p)


def test_already_scored_true_only_for_that_exact_session(tmp_path):
    p = _log(tmp_path, [{"timestamp": "t", "as_of_date": "2026-08-31", "run_label": "PM",
                         "overall_score": "1.0"}])
    assert already_scored("2026-08-31", "PM", p) is True
    assert already_scored("2026-08-31", "AM", p) is False   # other session, same day
    assert already_scored("2026-09-01", "PM", p) is False   # same label, other day


def test_already_scored_fails_open_without_a_log(tmp_path):
    assert already_scored("2026-08-31", "PM", str(tmp_path / "missing.csv")) is False


def test_judge_outage_is_advisory_not_a_failure():
    """An unreachable judge must not count against the briefing: it is observed, never scored."""
    r = quality.QualityReport()
    r.add(quality.Check("D1_judge_available", "D1", False, "judge failed: 429", advisory=True))
    r.add(quality.Check("R2_regime_narration", "D2", True, ""))
    assert r.failures() == []                                   # nothing to page on
    assert [c.id for c in r.advisories()] == ["D1_judge_available"]   # but still visible
    scored = r.scored()
    assert sum(c.passed for c in scored) / len(scored) == 1.0   # overall on the SCORED basis
