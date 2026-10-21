"""B8 monitoring dashboard — PURE aggregation: run roll-up, judge stability, RED gate, render."""

from __future__ import annotations

from macro_pipeline.dashboard import (
    CRITICAL_CHECKS,
    aggregate_judge,
    aggregate_runs,
    build_metrics,
    render_markdown,
)

NOW = "2026-06-17T10:15:00-04:00"


def _run(ts, source="llm", status="valid", failed=None, latency=None, llm_calls=1):
    """A minimal RunRecord.to_json()-shaped dict (only the fields the dashboard reads)."""
    return {
        "timestamp": ts, "source": source, "validator_status": status,
        "validator_failed": failed or [], "latency_ms": latency, "llm_calls": llm_calls,
    }


# --- aggregate_runs ----------------------------------------------------------------

def test_check_pass_rate_o5_failed_once_o8_clean():
    runs = [
        _run("2026-06-17T09:00:00-04:00", failed=["O5"]),  # O5 fail
        _run("2026-06-17T09:05:00-04:00"),
        _run("2026-06-17T09:10:00-04:00"),
        _run("2026-06-17T09:15:00-04:00"),
    ]
    agg = aggregate_runs(runs, window_days=30, now_iso=NOW)
    assert agg["n_runs"] == 4
    assert agg["check_pass_rate"]["O5"] == 0.75   # 3/4 passed
    assert agg["check_pass_rate"]["O8"] == 1.0     # clean
    assert agg["check_n"]["O5"] == 4 and agg["check_n"]["O8"] == 4


def test_refusal_excluded_from_fallback_numerator_kept_in_denominator():
    runs = [
        _run("2026-06-17T09:00:00-04:00", source="llm"),
        _run("2026-06-17T09:05:00-04:00", source="fallback", status="invalid", failed=["O5"]),
        _run("2026-06-17T09:10:00-04:00", source="refusal", status="refusal"),
        _run("2026-06-17T09:15:00-04:00", source="llm"),
    ]
    agg = aggregate_runs(runs, window_days=30, now_iso=NOW)
    # one fallback out of four runs; the refusal is NOT counted as a fallback.
    assert agg["fallback_rate"] == 0.25


def test_fallback_rate_none_when_no_runs():
    agg = aggregate_runs([], window_days=30, now_iso=NOW)
    assert agg["n_runs"] == 0
    assert agg["fallback_rate"] is None
    assert agg["check_pass_rate"]["O5"] is None
    assert agg["latency_p50_ms"] is None


def test_latency_p50_p95_nearest_rank():
    # 10 known latencies → p50 nearest-rank = 5th of sorted (=500), p95 = 10th (=1000).
    lat = [100, 200, 300, 400, 500, 600, 700, 800, 900, 1000]
    runs = [
        _run(f"2026-06-17T08:{i:02d}:00-04:00", latency=v)
        for i, v in enumerate(lat)
    ]
    agg = aggregate_runs(runs, window_days=30, now_iso=NOW)
    assert agg["latency_p50_ms"] == 500.0
    assert agg["latency_p95_ms"] == 1000.0


def test_window_filtering_drops_out_of_window_rows():
    runs = [
        _run("2026-06-17T09:00:00-04:00"),          # in window
        _run("2026-01-01T09:00:00-04:00", failed=["O8"]),  # ~5.5 months old → dropped
    ]
    agg = aggregate_runs(runs, window_days=30, now_iso=NOW)
    assert agg["n_runs"] == 1
    assert agg["check_pass_rate"]["O8"] == 1.0  # the old O8-failing row was excluded


def test_bare_date_now_includes_same_day_tz_aware_runs():
    # Integration seam: scripts/build_monitoring.py's default --now is a bare date
    # (date.today().isoformat()), which parses to UTC midnight, while real run timestamps
    # are tz-aware and sit hours past midnight. The window must still include the SAME-DAY
    # runs (and the subtraction must not raise on naive-vs-aware) — else today's runs, and a
    # same-day RED condition, get silently dropped.
    runs = [
        _run("2026-06-17T09:00:00-04:00", failed=["O5"]),  # 13:00Z on the as-of day
        _run("2026-06-17T17:30:00-04:00"),                 # 21:30Z on the as-of day
    ]
    agg = aggregate_runs(runs, window_days=30, now_iso="2026-06-17")  # bare date → naive midnight
    assert agg["n_runs"] == 2                       # both same-day runs counted, none dropped
    assert agg["check_pass_rate"]["O5"] == 0.5      # the O5 failure is visible, not masked


def test_bare_date_now_still_drops_out_of_window_rows():
    # The same-day grace must not turn the trailing window into "everything" — old rows still drop.
    runs = [
        _run("2026-06-17T09:00:00-04:00"),                 # in window
        _run("2026-01-01T09:00:00-04:00", failed=["O8"]),  # ~5.5 months old → still dropped
    ]
    agg = aggregate_runs(runs, window_days=30, now_iso="2026-06-17")
    assert agg["n_runs"] == 1
    assert agg["check_pass_rate"]["O8"] == 1.0


def test_tz_aware_now_includes_slightly_future_run():
    # A tz-aware --now computed at job start can PRECEDE a run logged moments later (or in a
    # different tz offset). The forward grace must still count it — else a same-day RED is dropped.
    runs = [_run("2026-06-17T17:30:00-04:00", failed=["O5"])]  # 21:30Z, AFTER the 14:15Z now
    agg = aggregate_runs(runs, window_days=30, now_iso="2026-06-17T10:15:00-04:00")
    assert agg["n_runs"] == 1
    assert agg["check_pass_rate"]["O5"] == 0.0  # the same-day O5 failure is visible, not masked


def test_quota_used_and_pct_on_latest_utc_day():
    runs = [
        _run("2026-06-17T09:00:00-04:00", llm_calls=2),
        _run("2026-06-17T09:30:00-04:00", llm_calls=3),
    ]
    # caps default 1500; 2026-06-17T09:00-04:00 == 13:00Z so both land on the 17th UTC.
    agg = aggregate_runs(runs, window_days=30, now_iso=NOW)
    assert agg["quota"]["llm"]["used"] == 5
    assert agg["quota"]["llm"]["cap"] == 1500
    assert abs(agg["quota"]["llm"]["pct"] - (5 / 1500 * 100)) < 1e-9


def test_check_pass_rate_only_over_reached_validation():
    # a "skipped" run never reached the validator → excluded from check denominators.
    runs = [
        _run("2026-06-17T09:00:00-04:00", status="valid"),
        _run("2026-06-17T09:05:00-04:00", status="skipped", source="fallback"),
    ]
    agg = aggregate_runs(runs, window_days=30, now_iso=NOW)
    assert agg["check_n"]["O5"] == 1  # only the validated run counts
    assert agg["check_pass_rate"]["O5"] == 1.0


# --- build_metrics RED logic -------------------------------------------------------

def test_critical_check_below_100_warns_but_does_NOT_pause_publishing():
    """A caught O5 is the validator WORKING: that run published the safe fallback card, so the
    reader is already protected. Pausing publish here would swap that safe card for a STALE one
    — which is what happened on 2026-08-25, freezing the live dashboard for two days on the
    strength of one caught failure. Alert, keep publishing."""
    runs = [
        _run("2026-06-17T09:00:00-04:00", failed=["O5"]),  # critical check fails
        _run("2026-06-17T09:05:00-04:00"),
    ]
    m = build_metrics(runs, [], now_iso=NOW)
    assert m.red is False                     # publishing continues
    assert m.red_reasons == []
    assert m.warn is True                     # but it is surfaced
    assert any("O5" in r for r in m.warn_reasons)
    assert CRITICAL_CHECKS == ("O5", "O8")


def test_not_red_when_all_clean():
    runs = [_run("2026-06-17T09:00:00-04:00"), _run("2026-06-17T09:05:00-04:00")]
    m = build_metrics(runs, [], now_iso=NOW)
    assert m.red is False
    assert m.red_reasons == []
    assert m.warn is False
    assert m.warn_reasons == []


def test_red_when_fallback_rate_over_half():
    runs = [
        _run("2026-06-17T09:00:00-04:00", source="fallback", status="invalid", failed=["length"]),
        _run("2026-06-17T09:05:00-04:00", source="fallback", status="invalid", failed=["length"]),
        _run("2026-06-17T09:10:00-04:00", source="llm"),
    ]
    m = build_metrics(runs, [], now_iso=NOW)  # 2/3 fallback > 50%
    assert m.red is True
    assert any("fallback" in r for r in m.red_reasons)


def test_noncritical_check_below_100_is_not_red():
    # "length" fails but is NOT a critical check → not red on its own.
    runs = [
        _run("2026-06-17T09:00:00-04:00", failed=["length"]),
        _run("2026-06-17T09:05:00-04:00"),
    ]
    m = build_metrics(runs, [], now_iso=NOW)
    assert m.red is False


# --- aggregate_judge ---------------------------------------------------------------

def _erow(ts, c1, c3, c4, c5, flag="False", model="gemini-2.5-flash", status="valid", case_id="T1"):
    return {
        "timestamp": ts, "case_id": case_id, "production_model": model, "validator_status": status,
        "c1": c1, "c3": c3, "c4": c4, "c5": c5, "flag_below_3": flag,
    }


def test_aggregate_judge_per_case_drift_is_the_variance_signal():
    # PRD §401: variance is run-to-run DRIFT on an UNCHANGED case, not between-case spread.
    rows = [
        # T1 scored in BOTH runs and drifts 5 -> 3 on c1 — the instability signal.
        _erow("2026-06-16T16:00:00", "5", "5", "5", "5", flag="False", case_id="T1"),
        _erow("2026-06-17T16:00:00", "3", "5", "5", "5", flag="True", case_id="T1"),
        # T2 scored once (run1 only) — no drift measurable.
        _erow("2026-06-16T16:00:00", "4", "4", "4", "4", flag="False", case_id="T2"),
        # an "error" row that must be skipped entirely.
        {"timestamp": "2026-06-16T16:00:00", "case_id": "T3", "production_model": "error",
         "validator_status": "error", "c1": "", "c3": "", "c4": "", "c5": "", "flag_below_3": ""},
    ]
    j = aggregate_judge(rows)
    assert j["n_verdicts"] == 3                  # error row skipped
    assert j["means"]["c1"] == 4.0               # pooled mean of [5, 3, 4]
    # variance = worst per-case across-run drift: only T1 repeats → var([5, 3]) = 1.0.
    assert j["variances"]["c1"] == 1.0
    # c3 never drifts within a repeated case → 0.0 (T1 [5,5]); single-run T2 is ignored.
    assert j["variances"]["c3"] == 0.0
    assert abs(j["means"]["c3"] - (14 / 3)) < 1e-9
    assert j["below3_rate"] == 1 / 3             # one flag_below_3 True of 3 verdicts
    assert j["kappa"] is None                    # needs a human-label column
    assert j["runs"] == 2                        # two distinct timestamps
    assert j["latest_run_means"]["c1"] == 3.0    # latest run (the 17th): T1 c1 = 3


def test_aggregate_judge_stable_case_has_zero_variance():
    # Same case, same score across two runs → no drift, variance 0.0 (NOT flagged unstable).
    rows = [
        _erow("2026-06-16T16:00:00", "5", "5", "5", "5", case_id="T1"),
        _erow("2026-06-17T16:00:00", "5", "5", "5", "5", case_id="T1"),
    ]
    assert aggregate_judge(rows)["variances"]["c1"] == 0.0


def test_aggregate_judge_single_run_per_case_variance_unmeasurable():
    # No case seen in ≥2 runs → drift not measurable → variance None (rendered as "—").
    rows = [_erow("2026-06-17T16:00:00", "5", "5", "4", "5", case_id="T1")]
    assert aggregate_judge(rows)["variances"]["c1"] is None


def test_aggregate_judge_empty():
    j = aggregate_judge([])
    assert j["n_verdicts"] == 0
    assert j["means"]["c1"] is None
    assert j["variances"]["c1"] is None
    assert j["below3_rate"] is None
    assert j["runs"] == 0
    assert j["kappa"] is None


# --- render_markdown ---------------------------------------------------------------

def test_render_red_contains_pause_marker():
    # RED = the product is degenerate (majority fallback), the one state where serving last-good
    # beats publishing. A caught critical check renders AMBER instead (test below).
    runs = [
        _run("2026-06-17T09:00:00-04:00", source="fallback", status="invalid", failed=["length"]),
        _run("2026-06-17T09:05:00-04:00", source="fallback", status="invalid", failed=["length"]),
        _run("2026-06-17T09:10:00-04:00", source="llm"),
    ]
    m = build_metrics(runs, [], now_iso=NOW)
    md = render_markdown(m, generated_at=NOW)
    assert "🔴" in md
    assert "PAUSE" in md
    assert "do not edit" in md.lower()


def test_render_amber_says_publishing_continues():
    runs = [
        _run("2026-06-17T09:00:00-04:00", failed=["O5"]),
        _run("2026-06-17T09:05:00-04:00"),
    ]
    md = render_markdown(build_metrics(runs, [], now_iso=NOW), generated_at=NOW)
    assert "🟡" in md and "AMBER" in md
    assert "PAUSE" not in md


def test_render_green_when_clean():
    runs = [_run("2026-06-17T09:00:00-04:00"), _run("2026-06-17T09:05:00-04:00")]
    m = build_metrics(runs, [], now_iso=NOW)
    md = render_markdown(m, generated_at=NOW)
    assert "🟢 GREEN" in md
    assert "PAUSE" not in md


def test_render_handles_zero_runs_honestly():
    m = build_metrics([], [], now_iso=NOW)
    md = render_markdown(m, generated_at=NOW)
    assert "no production runs logged yet" in md.lower()
    assert "🟢 GREEN" in md  # nothing failing → green


def test_render_marks_critical_check_with_red_emoji_inline():
    runs = [
        _run("2026-06-17T09:00:00-04:00", failed=["O8"]),  # banned-phrase critical check
        _run("2026-06-17T09:05:00-04:00"),
    ]
    m = build_metrics(runs, [], now_iso=NOW)
    md = render_markdown(m, generated_at=NOW)
    # the O8 row should carry a 🔴 marker and the table should still show ✅ on clean checks.
    assert "O8" in md and "🔴" in md
    assert "✅" in md
