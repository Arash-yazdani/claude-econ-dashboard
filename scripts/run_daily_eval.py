"""Daily 3-dimension eval of the briefing we just published (Phase 3).

Runs AFTER run_daily.py in the same workflow, against the freshly published artifacts. Scores
three dimensions, pushes them to LangSmith as feedback, appends to eval_log.csv, and fails the
`judge-daily` healthcheck when quality drops — which is what turns a silent quality regression
into an email.

    python scripts/run_daily_eval.py                 # score today's published briefing
    python scripts/run_daily_eval.py --reconcile     # + re-fetch every indicator (I2, slow)
    python scripts/run_daily_eval.py --dry-run       # print only; no log/feedback/ping

Why the judge runs HERE and not as a LangSmith online evaluator: online evaluators auto-upgrade
every trace they touch to extended (billable) retention, which would breach SO9 ("no payment
method, ever"). Running it ourselves on Claude (Bedrock) judge calls and pushing the scores as feedback
is free and keeps the account inside the 5k/month no-card cap.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from macro_pipeline import judge, observability, persona_judge, prompt, quality, tracing
from macro_pipeline._env import load_dotenv
from macro_pipeline.config import INDICATORS
from macro_pipeline.llm_client import BriefingLLMError
from macro_pipeline.quality import QualityReport

DAILY_EVAL_LOG = "logs/daily_eval.csv"
# W6 (Bruce, D+1): the per-row evidence behind I2, kept separately from daily_eval.csv
# because it is one record per INDICATOR per run, not one per run. Feeds the accuracy
# report he asked for; nothing reads it back into the eval.
RECONCILIATION_LOG = "logs/reconciliation.jsonl"
DATASET_NAME = "macro-daily-briefings"

EVAL_FIELDS = [
    "timestamp", "as_of_date", "run_label", "prompt_version", "judge_version",
    "production_model", "validator_status", "d1_score", "d2_score", "d3_score",
    "overall_score", "failed_checks", "c1", "c3", "c4", "c5",
    "flag_below_3_recomputed", "langsmith_run_id",
    # Layer-3 PROXY (persona_judge) — reported ALONGSIDE D1/D2/D3, never inside them. Those gate
    # on correctness, which is objective; this is a preference read, which is not, and a
    # subjective judge must never be able to block a publish.
    "bruce_version", "bruce_b1", "bruce_b2", "bruce_b3", "bruce_b4",
    "bruce_usefulness", "bruce_advice_flag", "bruce_clamped",
    # Real Bruce's ratings land here — the human-label column whose absence blocks Cohen's κ.
    # With the proxy beside them we can also compute κ(persona, Bruce): the persona validating
    # itself against the man, exactly as the craft judge must.
    "human_c1", "human_c3", "human_note",
]


def already_scored(as_of_date: str, run_label: str, path: str = DAILY_EVAL_LOG) -> bool:
    """True when this session's card already has a daily_eval row.

    The eval step runs on EVERY cron firing, including the ones where run_daily de-dupe-skips
    (which write nothing, so _latest_run() returns the earlier publish again). Result: each card
    was scored ~5x (332 rows / 67 cards), burning 5x the judge's shared Gemini quota for zero
    new information, and rolling a noisy judge five times against a 0.9 threshold that sits
    inside its own variance — 10 of 65 cards scored both <0.9 and >=0.9 on IDENTICAL input, and
    judge-daily paged on that noise (2026-08-31). One scoring per card.

    (as_of_date, run_label) is the card identity the kappa join and the review sheet already use.
    Known edge: a --force re-publish inside an already-scored session is not re-scored; a human
    is present for those. Fails OPEN (score again) if the log is unreadable.
    """
    try:
        with open(path, newline="", encoding="utf-8") as f:
            return any(r.get("as_of_date") == as_of_date and r.get("run_label") == run_label
                       for r in csv.DictReader(f))
    except FileNotFoundError:
        return False
    except Exception as exc:  # noqa: BLE001
        print(f"could not read {path}: {exc} — scoring anyway", file=sys.stderr)
        return False


def _latest_run(path: str = observability.RUN_LOG_PATH) -> Optional[dict[str, Any]]:
    """The most recent audit row — the briefing we are scoring."""
    try:
        lines = [ln for ln in Path(path).read_text(encoding="utf-8").splitlines() if ln.strip()]
        return json.loads(lines[-1]) if lines else None
    except Exception as exc:  # noqa: BLE001
        print(f"could not read {path}: {exc}", file=sys.stderr)
        return None


def _rebuild_request(record: dict, indicators_path: str = "public/indicators.json") -> dict:
    """Reconstruct the BriefingRequest the LLM saw, from the audit row + published indicators.

    The audit row stores the input HASH, not the request itself, so we rebuild the briefing-facing
    projection from indicators.json. Good enough for judging: the judge only reads
    {id, value, period_label, last_release_date} plus the regime fields.
    """
    try:
        published = json.loads(Path(indicators_path).read_text(encoding="utf-8"))
        rows = published.get("indicators", [])
    except Exception:  # noqa: BLE001
        rows = []
    return {
        "as_of_date": record.get("as_of_date"),
        "regime_today": record.get("regime_today"),
        "regime_yesterday": record.get("regime_yesterday"),
        "drivers": record.get("drivers", []),
        "indicators": [
            {k: r.get(k) for k in ("id", "display_name", "value", "unit",
                                   "period_label", "last_release_date", "delta")
             if r.get(k) is not None}
            for r in rows
        ],
        # Taken from the audit row, NOT hardcoded empty. C4 asks "did whats_new fire IFF a
        # release did" — telling the judge "no releases" while the briefing correctly carries a
        # whats_new scored a right answer as a C4 failure on every release day. Older rows
        # predating this field degrade to [], which is what they effectively asserted anyway.
        "releases_last_24h": record.get("releases_last_24h", []),
    }


def _card_rule(card_path: str = "public/briefing.json") -> Optional[str]:
    """The classifier branch recorded on the published card (W1). None for cards predating it."""
    try:
        return json.loads(Path(card_path).read_text(encoding="utf-8")).get("rule")
    except Exception:  # noqa: BLE001
        return None


def _published_rows(indicators_path: str = "public/indicators.json") -> list[dict]:
    """The raw published indicator rows — richer than the LLM-facing request projection."""
    try:
        return json.loads(Path(indicators_path).read_text(encoding="utf-8")).get("indicators", [])
    except Exception:  # noqa: BLE001
        return []


def _readings_for_classifier(rows: list[dict]):
    """Minimal IndicatorReadings for R1, rebuilt from the published feed.

    The classifier reads only `value` (+ `delta_3m` for unemployment) across five ids, so this is
    a faithful reconstruction, not an approximation. Returns None when the feed carries no
    delta_3m at all — an older artifact predating that field, where reproducibility genuinely
    cannot be checked and the caller falls back to the weaker label assertion.
    """
    from macro_pipeline.models import IndicatorReading
    if not any("delta_3m" in r for r in rows):
        return None
    return [
        IndicatorReading(
            id=r.get("id"), display_name=r.get("display_name", ""), value=r.get("value"),
            unit=r.get("unit", ""), period_label=r.get("period_label", ""),
            last_release_date=r.get("last_release_date", ""),
            delta=r.get("delta"), delta_3m=r.get("delta_3m"),
        )
        for r in rows
    ]


def evaluate(record: dict, request: dict, *, judge_model=None, judge_failover=None,
             reconcile_source=None, indicators=None) -> tuple[QualityReport, dict]:
    """Score all three dimensions. Returns (report, judge_verdict)."""
    report = QualityReport()
    output = record.get("output")
    verdict: dict[str, Any] = {}
    indicators = _published_rows() if indicators is None else indicators

    # ── D1 · brief quality (the genuinely subjective dimension → LLM judge) ──
    if judge_model is not None and isinstance(output, dict):
        try:
            verdict = judge.score(request, output, model=judge_model, failover=judge_failover)
            # Same doctrine as the gate: where the deterministic O4 already passed this briefing,
            # a judge C4 failure is a measurement error (proven twice on fixture T4) — discard the
            # score, keep the conflict on record.
            if judge.resolve_c4_conflict(record.get("validator_status", ""), verdict):
                report.add(quality.Check("D1_judge_conflict_c4", "D1", True,
                                         "judge failed C4 where O4 passed — c4 discarded, "
                                         "deterministic result stands", advisory=True))
            for c in quality.checks_from_verdict(verdict):
                report.add(c)
        except Exception as exc:  # noqa: BLE001 — a judge outage must not fail the eval run
            # ADVISORY: the judge being unreachable (free-tier 429) is "cannot measure", not
            # "the briefing failed". Counting it dragged overall below 0.9 and paged judge-daily
            # about the JUDGE's availability — the 2026-08-04 lesson, "cannot measure" reported
            # as "failed" — and it was the single most frequent failing check (16 rows).
            report.add(quality.Check("D1_judge_available", "D1", False, f"judge failed: {exc}",
                                     advisory=True))
    elif not isinstance(output, dict):
        report.add(quality.Check("D1_briefing_present", "D1", False,
                                 f"no LLM briefing to judge (source={record.get('source')})"))

    # ── D2 · regime (deterministic; the LLM never picks the regime) ──
    report.add(quality.check_regime_narration(output, record.get("regime_today")))
    # W5 R2-positive: the negative check above passes a briefing that names NO regime at all.
    # Shipped scored — replayed over all 109 recorded briefings at 0 failures.
    report.add(quality.check_regime_named(output, record.get("regime_today")))
    # W5 R4b: did a NAMED rule fire, or the catch-all? ADVISORY until W3 turns the fallthrough
    # into `indeterminate` — today the fallthrough is a real published state (25% of soft_landing
    # months over 12 years), so gating on it would fail correct runs.
    report.add(quality.check_rule_named(_card_rule()))
    # R1 — re-run the LOCKED classifier against the same published inputs and require it to
    # reproduce the published label. Rebuilding readings from indicators.json is enough because
    # the classifier reads only value + delta_3m (both published) for five indicator ids.
    # Falls back to the weaker "is it a known label" assertion only when the feed predates
    # delta_3m, so an older artifact degrades the check instead of erroring the whole eval.
    from macro_pipeline.models import REGIME_LABELS
    regime = record.get("regime_today")
    readings = _readings_for_classifier(indicators)
    if readings is None:
        report.add(quality.Check(
            "R1_regime_valid", "D2", regime in REGIME_LABELS,
            "delta_3m absent from feed — reproducibility unavailable"
            if regime in REGIME_LABELS else f"unknown regime {regime!r}",
        ))
    else:
        report.add(quality.check_regime_reproducible(
            readings, regime, record.get("regime_yesterday")))

    # ── D3 · indicators (deterministic-first) ──
    failed = set(record.get("validator_failed", []))
    report.add(quality.Check("I1_o5_grounding", "D3", "O5" not in failed,
                             "" if "O5" not in failed else "O5 number grounding FAILED"))
    report.add(quality.Check("I3_no_banned_phrases", "D3", "O8" not in failed,
                             "" if "O8" not in failed else "O8 banned-phrase escape"))
    report.add(quality.check_direction_framing(output, request))
    if reconcile_source is not None:
        try:
            published = json.loads(Path("public/indicators.json").read_text(encoding="utf-8"))
            report.add(quality.reconcile_indicators(
                published.get("indicators", []), reconcile_source, specs=INDICATORS))
        except Exception as exc:  # noqa: BLE001
            report.add(quality.Check("I2_source_reconciliation", "D3", False, str(exc)))
    return report, verdict


def append_reconciliation(check, as_of_date: str, timestamp: str,
                          path: str = RECONCILIATION_LOG) -> bool:
    """One JSONL line per reconciliation run: every published indicator with its verdict.

    Best-effort like the rest of the observability layer — a failed write must never cost the
    eval. Returns False rather than raising (PRD §397).
    """
    try:
        rows = getattr(check, "rows", None) or []
        if not rows:
            return False
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps({
                "timestamp": timestamp,
                "as_of_date": as_of_date,
                "checked": sum(1 for r in rows if r.get("verdict") in ("matched", "mismatched")),
                "rows": rows,
            }, ensure_ascii=False) + "\n")
        return True
    except Exception as exc:  # noqa: BLE001 — evidence-keeping must not break the eval
        print(f"[eval] reconciliation log failed: {exc}", file=sys.stderr)
        return False


def _append_log(row: dict, path: str = DAILY_EVAL_LOG) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    new = not p.exists()
    if not new:
        # Without this, adding a field to EVAL_FIELDS writes N values under an N-k header and
        # NOTHING RAISES. It already happened: the 8 bruce_* fields shifted every row from index
        # 18, so bruce_version read as human_c1 and Bruce's annotation columns — the kappa's
        # inputs — carried persona-judge output for two weeks. Shared with judge.log_verdicts so
        # the fix cannot be applied to one writer and forgotten on the other again.
        judge.migrate_csv_header(p, EVAL_FIELDS)
    with p.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=EVAL_FIELDS)
        if new:
            w.writeheader()
        w.writerow({k: row.get(k, "") for k in EVAL_FIELDS})


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Daily 3-dimension eval of the published briefing")
    ap.add_argument("--reconcile", action="store_true",
                    help="also re-fetch every indicator and compare to what we published (I2)")
    ap.add_argument("--dry-run", action="store_true", help="print only; no log, feedback, or ping")
    ap.add_argument("--no-persona", action="store_true",
                    help="skip the Bruce persona judge (Layer-3 proxy; costs one extra LLM call)")
    ap.add_argument("--threshold", type=float, default=0.9,
                    help="overall pass rate below which judge-daily is failed (default 0.9). "
                         "Applies to D1/D2/D3 only — the persona score never gates.")
    args = ap.parse_args(argv)

    load_dotenv()
    record = _latest_run()
    if record is None:
        print("no run to evaluate", file=sys.stderr)
        return 1
    request = _rebuild_request(record)

    # One scoring per card. No ping on the skip: the first eval of the session already pinged
    # judge-daily, and a later success ping would mask a real fail from that first scoring.
    if not args.dry_run and already_scored(str(record.get("as_of_date") or ""),
                                           str(record.get("run_label") or "")):
        print(f"{record.get('as_of_date')} {record.get('run_label')} already scored — skipping "
              f"(no judge call, no log row, no ping)")
        return 0

    judge_model = judge_failover = None
    try:
        judge_model = judge.make_judge()
    except BriefingLLMError as exc:
        print(f"(judge unavailable: {exc})", file=sys.stderr)
    try:
        judge_failover = judge.make_judge_failover()
    except BriefingLLMError:
        pass

    reconcile_source = None
    if args.reconcile:
        try:
            from macro_pipeline.sources import build_source
            # Same stack production published with, so I2 compares like-for-like (incl. the BLS graft).
            reconcile_source = build_source()
        except Exception as exc:  # noqa: BLE001
            print(f"(reconciliation unavailable: {exc})", file=sys.stderr)

    report, verdict = evaluate(record, request, judge_model=judge_model,
                               judge_failover=judge_failover, reconcile_source=reconcile_source)

    scores = {d: report.dimension_score(d) for d in ("D1", "D2", "D3")}
    # Same basis as dimension_score(): advisory checks are observed, never scored. Before this,
    # `overall` counted every check while D1/D2/D3 excluded advisories — two denominators.
    real = report.scored()
    overall = (sum(1 for c in real if c.passed) / len(real)) if real else None

    print(f"{'dim':4} {'check':28} pass  detail")
    for c in report.checks:
        print(f"{c.dimension:4} {c.id:28} {'✅' if c.passed else '❌'}    {c.detail[:70]}")
    print(f"\nD1 brief={_pct(scores['D1'])}  D2 regime={_pct(scores['D2'])}  "
          f"D3 indicators={_pct(scores['D3'])}  overall={_pct(overall)}")
    if report.failures():
        print(f"FAILED: {', '.join(c.id for c in report.failures())}")

    # ── Layer-3 proxy: score the same briefing as Bruce would ──────────────────────
    # Reported ALONGSIDE D1/D2/D3 and deliberately excluded from `overall` — those gate on
    # correctness, which is objective; this is a preference read, which is not. Wrapped whole
    # because it is advisory: a persona outage must leave blank columns, never fail an eval
    # whose correctness dimensions passed.
    bruce: dict[str, Any] = {}
    if judge_model is not None and isinstance(record.get("output"), dict) and not args.no_persona:
        try:
            bruce = persona_judge.score(request, record["output"],
                                        model=persona_judge.make_bruce_judge(),
                                        # its own failover: the craft judge's failover is pinned to
                                        # the JudgeVerdict schema, which would force the wrong shape
                                        failover=persona_judge.make_bruce_judge_failover())
        except Exception as exc:  # noqa: BLE001
            print(f"(persona judge unavailable: {exc})", file=sys.stderr)
    if bruce:
        u = persona_judge.usefulness(bruce)
        print(f"\nBruce (Layer-3 proxy, advisory — never gates):  usefulness={_pct(u)}"
              f"{'  ⚠ ADVICE' if bruce.get('advice_flag') else ''}"
              f"{'  ⚠ CLAMPED' if bruce.get('clamped_by_b4') else ''}")
        for k in persona_judge.CRITERIA:
            print(f"  {k:24} {bruce.get(k)}   {bruce.get('rationale', {}).get(k[:2], '')[:62]}")

    if args.dry_run:
        return 0

    # W6: persist the per-row reconciliation evidence. Written BEFORE the LangSmith calls so a
    # tracing outage cannot cost the accuracy report its data.
    i2 = next((c for c in report.checks if c.id == "I2_source_reconciliation"), None)
    if i2 is not None:
        append_reconciliation(i2, record.get("as_of_date") or "",
                              datetime.now().isoformat(timespec="seconds"))

    # LangSmith: one root run for the briefing + the three dimension scores as feedback, and the
    # briefing promoted into a persistent Dataset so the D+30 review survives 14-day retention.
    run_id = tracing.trace_run(
        "daily-briefing",
        inputs={"as_of_date": record.get("as_of_date"), "regime_today": record.get("regime_today"),
                "drivers": record.get("drivers", [])},
        outputs=record.get("output") if isinstance(record.get("output"), dict) else
        {"text": record.get("output")},
        metadata={"run_label": record.get("run_label"), "model": record.get("model"),
                  "prompt_version": record.get("prompt_version"),
                  "validator_status": record.get("validator_status"),
                  "sources": record.get("sources", {}),
                  "llm_usage": record.get("llm_usage", [])},
        tags=["production", record.get("run_label", "")],
    )
    for dim, score in scores.items():
        if score is not None:
            tracing.log_feedback(run_id, dim, score=score)
    if overall is not None:
        tracing.log_feedback(run_id, "overall", score=overall,
                             comment="; ".join(c.id for c in report.failures()) or "all checks passed")
    tracing.upsert_example(
        DATASET_NAME,
        inputs={"as_of_date": record.get("as_of_date"), "regime_today": record.get("regime_today")},
        outputs=record.get("output") if isinstance(record.get("output"), dict) else {},
        metadata={"run_label": record.get("run_label"), "scores": scores,
                  "prompt_version": record.get("prompt_version")},
    )

    if bruce:
        tracing.log_feedback(run_id, "bruce_usefulness",
                             score=persona_judge.usefulness(bruce),
                             comment=bruce.get("rationale", {}).get("b4", ""))

    _append_log({
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "as_of_date": record.get("as_of_date"),
        "run_label": record.get("run_label"),
        "prompt_version": record.get("prompt_version") or prompt.PROMPT_VERSION,
        "judge_version": judge.JUDGE_VERSION,
        "production_model": record.get("model"),
        "validator_status": record.get("validator_status"),
        "d1_score": _num(scores["D1"]), "d2_score": _num(scores["D2"]),
        "d3_score": _num(scores["D3"]), "overall_score": _num(overall),
        "failed_checks": "|".join(c.id for c in report.failures()),
        "c1": verdict.get("c1_quotability", ""), "c3": verdict.get("c3_tone", ""),
        "c4": verdict.get("c4_conditionals", ""), "c5": verdict.get("c5_length_format", ""),
        "flag_below_3_recomputed": quality.recomputed_flag_below_3(verdict) if verdict else "",
        "langsmith_run_id": run_id or "",
        "bruce_version": persona_judge.PERSONA_VERSION if bruce else "",
        "bruce_b1": bruce.get("b1_read_he_can_repeat", "") if bruce else "",
        "bruce_b2": bruce.get("b2_right_two_numbers", "") if bruce else "",
        "bruce_b3": bruce.get("b3_movement", "") if bruce else "",
        "bruce_b4": bruce.get("b4_restraint", "") if bruce else "",
        "bruce_usefulness": _num(persona_judge.usefulness(bruce)) if bruce else "",
        "bruce_advice_flag": bruce.get("advice_flag", "") if bruce else "",
        "bruce_clamped": bruce.get("clamped_by_b4", "") if bruce else "",
        "human_c1": "", "human_c3": "", "human_note": "",
    })

    if overall is not None and overall < args.threshold:
        observability.ping_check("judge", "fail")
        observability.send_alert(
            f"Daily eval below threshold: {overall:.0%} < {args.threshold:.0%} — "
            f"failed: {', '.join(c.id for c in report.failures())}")
        return 1
    observability.ping_check("judge", "success")
    return 0


def _pct(x: Optional[float]) -> str:
    return "—" if x is None else f"{x:.0%}"


def _num(x: Optional[float]) -> str:
    return "" if x is None else f"{x:.4f}"


if __name__ == "__main__":
    raise SystemExit(main())
