"""B8 monitoring — PURE aggregation for the hand-rolled /docs/monitoring.md dashboard.

PRD §397 (q56): the most consequential widget is the last-30-days validator pass rate per
O-check with C2 (== validator O5, number grounding) and O8 (banned phrases) highlighted RED if
<100% — a red widget pauses the cron. This module also rolls up p50/p95 latency, fallback rate,
quota utilization, and the judge-stability panel (PRD §401: criterion mean + score variance on
the regression cases; κ is pending a human-label column).

No file or network I/O lives here: callers parse logs/runs.jsonl and eval_log.csv and hand this
module plain dicts (scripts/build_monitoring.py is the only writer of docs/monitoring.md).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

# The eight O-checks surfaced per the validator (validator.validate .failed ids — PRD §397).
O_CHECKS: tuple[str, ...] = ("O2", "O3", "O4", "O5", "period", "O8", "length", "O16")
# C2 number grounding == validator O5; O8 == banned phrases. RED if either is <100% (PRD §397).
CRITICAL_CHECKS: tuple[str, ...] = ("O5", "O8")
# Sanity ceiling carried over from the Gemini free-tier RPD (1,500). Bedrock quotas are per-account
# and set by AWS (Service Quotas); this is config for the quota widget, not a hard fact.
DEFAULT_CAPS: dict[str, int] = {"llm_calls_per_day": 1500}
# Judge criteria as logged to eval_log.csv (c2/c6 are validator-owned, not judged — PRD §401).
JUDGE_CRITERIA: tuple[str, ...] = ("c1", "c3", "c4", "c5")

# validator_status values that mean the run reached the O-check layer (so a pass/fail is meaningful).
_REACHED_VALIDATION = ("valid", "invalid", "refusal")


@dataclass
class DashboardMetrics:
    window_days: int
    n_runs: int
    fallback_rate: Optional[float]
    check_pass_rate: dict[str, Optional[float]]
    check_n: dict[str, int]
    latency_p50_ms: Optional[float]
    latency_p95_ms: Optional[float]
    quota: dict[str, dict]
    judge: dict
    red: bool                 # pause publishing (exit 3)
    red_reasons: list[str]
    warn: bool                # quality warning — alert, but KEEP PUBLISHING
    warn_reasons: list[str]


# --- helpers ----------------------------------------------------------------------

def _parse_iso(ts: str) -> Optional[datetime]:
    """Parse an ISO-8601 timestamp tolerantly; return None on anything unparseable."""
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return None


def _as_utc(dt: datetime) -> datetime:
    """Normalise to a tz-aware UTC datetime so naive and aware stamps are comparable.

    The window filter mixes operands: run timestamps are tz-aware (e.g. "-04:00"), but the
    script's default --now is a bare date (date.today().isoformat()) → naive. Subtracting the
    two raised TypeError. We treat a naive stamp as UTC (the bare-date day boundary) and convert
    aware stamps to UTC, then subtract on a single basis. Tz-aware inputs are unchanged in value.
    """
    from datetime import timezone
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _utc_day(dt: datetime) -> str:
    """The calendar day in UTC, normalising tz-aware and naive stamps to one comparable key."""
    return _as_utc(dt).date().isoformat()


def _percentile(sorted_vals: list[float], pct: float) -> Optional[float]:
    """Nearest-rank percentile on an already-sorted list (PRD §397 p50/p95). None if empty."""
    if not sorted_vals:
        return None
    if len(sorted_vals) == 1:
        return float(sorted_vals[0])
    # nearest-rank: rank = ceil(pct/100 * n), 1-indexed, clamped to [1, n].
    import math
    rank = max(1, min(len(sorted_vals), math.ceil(pct / 100.0 * len(sorted_vals))))
    return float(sorted_vals[rank - 1])


def _mean(vals: list[float]) -> Optional[float]:
    return sum(vals) / len(vals) if vals else None


def _variance(vals: list[float]) -> Optional[float]:
    """Population variance (PRD §401 score variance on unchanged regression cases). None if empty."""
    if not vals:
        return None
    m = sum(vals) / len(vals)
    return sum((v - m) ** 2 for v in vals) / len(vals)


def _coerce_int(raw: Any) -> Optional[int]:
    """eval_log cell -> int or None ("" / None -> None; non-numeric -> None)."""
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    try:
        return int(float(s))
    except (ValueError, TypeError):
        return None


def _truthy(raw: Any) -> bool:
    """CSV-stringified booleans: "True"/"true"/"1" are truthy, everything else falsey."""
    return str(raw).strip().lower() in ("true", "1", "yes")


# --- run aggregation --------------------------------------------------------------

def aggregate_runs(
    records: list[dict], *, window_days: int, now_iso: str, caps: dict = DEFAULT_CAPS,
) -> dict:
    """Roll up logs/runs.jsonl RunRecord dicts over a trailing window (PRD §397).

    - n_runs: records whose timestamp is within `window_days` of `now_iso` (inclusive).
    - fallback_rate: #(source=="fallback") / n_runs. A refusal is a CORRECT outcome, NOT a
      fallback, so it is excluded from the numerator but kept in the denominator. None if n_runs==0.
    - check_pass_rate[c]: over runs that REACHED validation, pass == c not in validator_failed;
      rate = passes/reached (None if reached==0). check_n[c] = reached count.
    - latency p50/p95: nearest-rank on the non-null latency_ms values.
    - quota["llm"]: used = sum(llm_calls) on the most recent UTC day present, vs caps cap & pct.
    """
    now = _parse_iso(now_iso)
    # Always allow a 24h forward grace so SAME-DAY runs are counted regardless of --now's form:
    #  - a bare-date --now (date.today().isoformat(), the script default) parses to UTC midnight,
    #    while real run stamps sit hours past midnight on that day;
    #  - a tz-aware --now computed at job start can precede a run logged moments later, or sit in a
    #    different tz offset than the run stamps.
    # 24h absorbs both; the trailing window (window_days) still bounds the past, so a same-day RED
    # condition is never silently dropped (PRD §397).
    forward_grace = 86400.0
    in_window: list[dict] = []
    for rec in records:
        dt = _parse_iso(rec.get("timestamp", ""))
        if dt is None or now is None:
            continue
        # Normalise both to UTC so a bare-date (naive) --now and tz-aware run stamps subtract cleanly.
        delta = (_as_utc(now) - _as_utc(dt)).total_seconds()
        if -forward_grace <= delta <= window_days * 86400:
            in_window.append(rec)

    n_runs = len(in_window)

    fallbacks = sum(1 for r in in_window if r.get("source") == "fallback")
    fallback_rate = (fallbacks / n_runs) if n_runs else None

    check_pass_rate: dict[str, Optional[float]] = {}
    check_n: dict[str, int] = {}
    reached = [r for r in in_window if r.get("validator_status") in _REACHED_VALIDATION]
    for c in O_CHECKS:
        passes = sum(1 for r in reached if c not in (r.get("validator_failed") or []))
        check_n[c] = len(reached)
        check_pass_rate[c] = (passes / len(reached)) if reached else None

    latencies = sorted(
        float(r["latency_ms"]) for r in in_window
        if r.get("latency_ms") is not None
    )
    p50 = _percentile(latencies, 50.0)
    p95 = _percentile(latencies, 95.0)

    cap = caps.get("llm_calls_per_day", DEFAULT_CAPS["llm_calls_per_day"])
    used = 0
    if in_window:
        by_day: dict[str, int] = {}
        for r in in_window:
            dt = _parse_iso(r.get("timestamp", ""))
            if dt is None:
                continue
            by_day[_utc_day(dt)] = by_day.get(_utc_day(dt), 0) + int(r.get("llm_calls") or 0)
        if by_day:
            latest_day = max(by_day)  # most recent UTC day present in the window
            used = by_day[latest_day]
    quota = {"llm": {"used": used, "cap": cap,
                     "pct": (used / cap * 100.0) if cap else None}}

    return {
        "n_runs": n_runs,
        "fallback_rate": fallback_rate,
        "check_pass_rate": check_pass_rate,
        "check_n": check_n,
        "latency_p50_ms": p50,
        "latency_p95_ms": p95,
        "quota": quota,
    }


# --- judge aggregation (PRD §401) -------------------------------------------------

def aggregate_judge(eval_rows: list[dict]) -> dict:
    """Roll up eval_log.csv DictReader rows into the judge-stability panel (PRD §401).

    Per criterion (c1,c3,c4,c5):
      - mean: pooled population mean across all non-null scores (a summary level).
      - variance: the INSTABILITY signal PRD §401 names — "score variance on unchanged
        regression cases". We group by case_id and take the worst (max) per-case population
        variance of that criterion ACROSS runs, so a case that scores differently run-to-run
        shows up, while the (expected) spread BETWEEN different cases does not. A case seen in
        <2 runs has no measurable drift; variance is None when no case has yet repeated.
      - latest_run_means: the most-recent-timestamp-group means (current level).
    "error" rows are skipped; "" cells coerce to null. below3_rate = #flag_below_3 / n_verdicts.
    κ is None — Cohen's kappa needs a human-label column the eval log does not yet carry.
    """
    scored = [r for r in eval_rows if r.get("production_model") != "error"
              and r.get("validator_status") != "error"]

    coerced: list[dict[str, Any]] = []
    for r in scored:
        coerced.append({
            "case_id": r.get("case_id", ""),
            "timestamp": r.get("timestamp", ""),
            "flag_below_3": _truthy(r.get("flag_below_3")),
            **{c: _coerce_int(r.get(c)) for c in JUDGE_CRITERIA},
        })

    n_verdicts = len(coerced)
    # Group by case so variance measures run-to-run DRIFT on an unchanged case, not between-case spread.
    by_case: dict[str, list[dict[str, Any]]] = {}
    for row in coerced:
        by_case.setdefault(row["case_id"], []).append(row)

    means: dict[str, Optional[float]] = {}
    variances: dict[str, Optional[float]] = {}
    for c in JUDGE_CRITERIA:
        allvals = [float(row[c]) for row in coerced if row[c] is not None]
        means[c] = _mean(allvals)
        per_case_vars: list[float] = []
        for rows in by_case.values():
            cvals = [float(row[c]) for row in rows if row[c] is not None]
            if len(cvals) >= 2:  # need the SAME case in ≥2 runs to observe drift
                v = _variance(cvals)
                if v is not None:
                    per_case_vars.append(v)
        # Worst-drifting case is the §401 instability signal; None when no case has repeated yet.
        variances[c] = max(per_case_vars) if per_case_vars else None

    below3 = sum(1 for row in coerced if row["flag_below_3"])
    below3_rate = (below3 / n_verdicts) if n_verdicts else None

    timestamps = {row["timestamp"] for row in coerced if row["timestamp"]}
    latest_run_means: dict[str, Optional[float]] = {c: None for c in JUDGE_CRITERIA}
    if timestamps:
        latest = max(timestamps)
        latest_rows = [row for row in coerced if row["timestamp"] == latest]
        for c in JUDGE_CRITERIA:
            vals = [float(row[c]) for row in latest_rows if row[c] is not None]
            latest_run_means[c] = _mean(vals)

    return {
        "n_verdicts": n_verdicts,
        "means": means,
        "variances": variances,
        "below3_rate": below3_rate,
        "latest_run_means": latest_run_means,
        "runs": len(timestamps),
        "kappa": None,  # needs a human-label column not yet present (PRD §401)
    }


# --- composition + render ---------------------------------------------------------

def build_metrics(
    run_records: list[dict], eval_rows: list[dict], *,
    window_days: int = 30, now_iso: str, caps: dict = DEFAULT_CAPS,
) -> DashboardMetrics:
    """Compose run + judge aggregations and decide the RED gate (PRD §397).

    TWO severities, because they call for opposite actions:

    - red (PAUSE publishing, exit 3): fallback rate > 0.5. The product is degenerate — half the
      cards are templates — so serving last-good beats serving more of the same.
    - warn (ALERT, keep publishing): a critical check (O5/O8) below 100%. This means the
      validator CAUGHT a bad number and published the deterministic fallback: the safety system
      working, with the reader already protected.

    Critical checks used to be a red trigger, and that inverted the gate's purpose. On
    2026-08-25 a single caught O5 put the 30-day rate at 98%, which paused publishing, which
    froze the live dashboard on a two-day-old card — and would have kept it frozen for the ~28
    days it took that one row to age out of the window. Pausing on a CAUGHT failure trades a
    safe current card for a stale one, i.e. it makes the reader worse off, and "silent staleness
    is the trust failure" (PRD). O5/O8 remain non-negotiable where it counts: validator.py gates
    every single publish, so a bad number cannot reach the card either way.
    """
    runs = aggregate_runs(run_records, window_days=window_days, now_iso=now_iso, caps=caps)
    judge = aggregate_judge(eval_rows)

    warn_reasons: list[str] = []
    for c in CRITICAL_CHECKS:
        rate = runs["check_pass_rate"].get(c)
        if rate is not None and rate < 1.0:
            warn_reasons.append(
                f"{c} pass rate {rate * 100:.0f}% < 100% (critical check) — validator caught it; "
                f"the affected run published the fallback card")
    red_reasons: list[str] = []
    fb = runs["fallback_rate"]
    if fb is not None and fb > 0.5:
        red_reasons.append(f"fallback rate {fb * 100:.0f}% > 50%")

    return DashboardMetrics(
        window_days=window_days,
        n_runs=runs["n_runs"],
        fallback_rate=runs["fallback_rate"],
        check_pass_rate=runs["check_pass_rate"],
        check_n=runs["check_n"],
        latency_p50_ms=runs["latency_p50_ms"],
        latency_p95_ms=runs["latency_p95_ms"],
        quota=runs["quota"],
        judge=judge,
        red=bool(red_reasons),
        red_reasons=red_reasons,
        warn=bool(warn_reasons),
        warn_reasons=warn_reasons,
    )


def _fmt_rate(rate: Optional[float]) -> str:
    return "—" if rate is None else f"{rate * 100:.0f}%"


def _fmt_num(val: Optional[float], unit: str = "") -> str:
    if val is None:
        return "—"
    txt = f"{val:.0f}" if float(val).is_integer() else f"{val:.2f}"
    return f"{txt}{unit}"


def render_markdown(m: DashboardMetrics, *, generated_at: str) -> str:
    """Render the dashboard markdown SECTION (no file writes — the script appends the runbook).

    Includes the do-not-edit banner, the per-O-check table (🔴 on any critical check <100%, ✅
    otherwise), p50/p95 latency, fallback rate, quota, the judge table + below-3 rate + κ-pending
    note, and the prominent STATUS banner whose RED state tells the cron to PAUSE (PRD §397).
    """
    if m.red:
        status = f"STATUS: 🔴 RED — cron should PAUSE ({'; '.join(m.red_reasons)})"
    elif m.warn:
        status = f"STATUS: 🟡 AMBER — publishing continues ({'; '.join(m.warn_reasons)})"
    else:
        status = "STATUS: 🟢 GREEN"

    lines: list[str] = [
        "<!-- AUTO-GENERATED by scripts/build_monitoring.py — do not edit by hand. -->",
        f"_Generated at {generated_at} over a {m.window_days}-day window._",
        "",
        status,
        "",
    ]

    if m.n_runs == 0:
        lines += [
            "No production runs logged yet — `logs/runs.jsonl` is empty for this window.",
            "",
        ]
    else:
        lines.append(f"**Runs in window:** {m.n_runs}")
        lines.append("")

    # Per-O-check validator pass-rate table — the most consequential widget (PRD §397).
    lines += [
        "### Validator pass rate per O-check",
        "",
        "| check | pass rate | n | status |",
        "| --- | --- | --- | --- |",
    ]
    for c in O_CHECKS:
        rate = m.check_pass_rate.get(c)
        n = m.check_n.get(c, 0)
        critical = c in CRITICAL_CHECKS
        if critical and rate is not None and rate < 1.0:
            mark = "🔴"
        elif rate is None:
            mark = "—"
        else:
            mark = "✅"
        label = f"{c} (critical)" if critical else c
        lines.append(f"| {label} | {_fmt_rate(rate)} | {n} | {mark} |")
    lines.append("")

    # Latency, fallback, quota.
    quota_llm = m.quota.get("llm", {})
    q_used = quota_llm.get("used", 0)
    q_cap = quota_llm.get("cap", "—")
    q_pct = quota_llm.get("pct")
    lines += [
        "### Latency, fallback & quota",
        "",
        f"- **Latency p50 / p95:** {_fmt_num(m.latency_p50_ms, ' ms')} / "
        f"{_fmt_num(m.latency_p95_ms, ' ms')}",
        f"- **Fallback rate:** {_fmt_rate(m.fallback_rate)} "
        "(refusals excluded — a refusal is a correct outcome)",
        f"- **LLM quota (latest UTC day):** {q_used} / {q_cap} calls"
        + ("" if q_pct is None else f" ({q_pct:.1f}%)"),
        "",
    ]

    # Judge stability panel (PRD §401).
    j = m.judge
    lines += [
        "### Judge stability (PRD §401)",
        "",
        "| criterion | mean | variance | latest-run mean |",
        "| --- | --- | --- | --- |",
    ]
    for c in JUDGE_CRITERIA:
        mean = j.get("means", {}).get(c)
        var = j.get("variances", {}).get(c)
        latest = j.get("latest_run_means", {}).get(c)
        lines.append(
            f"| {c} | {_fmt_num(mean)} | {_fmt_num(var)} | {_fmt_num(latest)} |"
        )
    below3 = j.get("below3_rate")
    lines += [
        "",
        f"- **Below-3 rate:** {_fmt_rate(below3)} over {j.get('n_verdicts', 0)} verdicts "
        f"across {j.get('runs', 0)} runs",
        "- **Variance** = worst per-case score drift across runs (PRD §401: an *unchanged* case "
        "scored differently run-to-run); `—` until a case repeats. Rising variance ⇒ judge re-prompt.",
        "- **Cohen's κ:** pending — needs a human-label column not yet in `eval_log.csv`.",
        "",
    ]

    return "\n".join(lines)
