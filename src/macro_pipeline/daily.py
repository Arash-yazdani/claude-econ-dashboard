"""B6 production orchestrator — the cron-driven daily briefing run (ADR-001 v1.1).

`orchestrate()` is the single seam the GitHub Actions cron drives (via scripts/run_daily.py):
FRED pull → classifier → LLM (+failover) → 17-check validator → fallback → briefing.json,
with one audited RunRecord per run (B8 observability). Collaborators (FRED source, the two
LLM clients) are injected so the whole flow runs OFFLINE and deterministically in tests; the
live FRED/LLM run on a real device is B9.

Design rules (frozen API):
  - NEVER raise into the workflow except a genuine can't-proceed (FRED down) — and even that
    is caught and returned as DailyOutcome(published=False, source="error") so the PWA keeps
    serving the last-good briefing.json (we do not overwrite it on a FRED failure).
  - Scheduling is robust to GitHub Actions' best-effort cron (which delays firings 1–2h and
    drops some): resolve_run_label classifies a firing into a wide AM/PM SESSION window (not an
    exact hour), the workflow fires several UTC crons per session, and run_daily.py de-dupes by
    (ET date, label) so exactly one run publishes per session — no manual DST edits, ever.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

from . import observability, pipeline, prompt
from .fred_client import FredError, FredSource
from .llm_client import LLMClient
from .models import PipelineResult
from .observability import RunRecord
from .pipeline import run_pipeline
from .validator import REGIME_PLAIN, FinalBriefing, fallback_template, produce_briefing

SCHEMA_VERSION = 1


def resolve_run_label(et_now) -> Optional[str]:
    """Map an ET firing time to its SESSION label, or None for an overnight firing to skip.

    A WIDE window, NOT an exact time. GitHub Actions cron is best-effort and routinely fires
    1–2h late (and the EDT/EST cron pair land an hour apart), so an exact-time guard would skip
    almost every real firing once GitHub's delay pushed it past the minute. Instead: AM session =
    ET 10:30 → 13:59, PM session = 16:30 → 22:59, 14:00-16:29 and overnight/pre-open -> None. The EDT/EST pair and
    any delayed/buffer firing resolve to the same session; run_daily.py de-dupes so exactly one run
    publishes per ET day per session.

    The 10:30 floor is a DATA floor, not the market open (it was 09:30 until 2026-08-12). BLS
    publishes CPI and the jobs report at 08:30 ET, but FRED — our only source for them — does not
    ingest until ~10:10 ET. The AM run targeted 09:45 ET and therefore read LAST MONTH's print on
    every release morning: on 2026-08-12 the card served June CPI (2.6%) while July (2.47%) had
    been public since 08:30 and landed on FRED at 10:10:19 ET, 25 minutes after we fetched.

    The floor does the work, not the cron times. It makes any firing that lands before FRED has
    the data self-skip, in BOTH tz halves, so the session is claimed by a run that can actually
    see a release rather than by whichever cron fires first. Raising it costs the reader ~1h of
    latency on ordinary days and buys the correct number on the ~2 days/month he most wants it.
    """
    m = et_now.hour * 60 + et_now.minute
    if 10 * 60 + 30 <= m < 14 * 60:  # 10:30 (after FRED's ~10:10 release ingest) .. 13:59 -> AM
        return "AM"
    # PM floor 16:30, raised from 14:00 on 2026-10-27: GitHub delayed the 12:15 AM-buffer cron
    # to ~14:40-15:40 ET most days, it resolved to PM, and the "evening" card published hours
    # before the 17:30 target on mostly AM data. A firing between 14:00 and 16:29 now skips.
    if 16 * 60 + 30 <= m < 23 * 60:   # 16:30 .. 22:59 -> PM
        return "PM"
    return None


def already_published(run_label: str, now_et: datetime,
                      runlog: str | Path = observability.RUN_LOG_PATH) -> bool:
    """True if a briefing for this ET day + session label was already published.

    Makes the cron IDEMPOTENT: the EDT/EST cron pair and any delayed/buffer firing all resolve to
    the same session (resolve_run_label), so without this a second firing would re-publish. The
    first firing of a session writes + commits its run; later firings check out that commit and
    self-skip here. A *failed* run (source=="error") never claims the session, so a later firing
    can retry. Best-effort: any read/parse error -> not-published (fail open — better to run).
    """
    today = now_et.date().isoformat()
    tz = now_et.tzinfo
    try:
        lines = Path(runlog).read_text(encoding="utf-8").splitlines()
    except OSError:
        return False
    for line in lines:
        if not line.strip():
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if r.get("run_label") != run_label or r.get("source") == "error" or r.get("error"):
            continue
        ts = r.get("timestamp", "")
        try:
            rdate = datetime.fromisoformat(ts).astimezone(tz).date().isoformat()
        except (ValueError, TypeError):
            rdate = ts[:10] if isinstance(ts, str) else ""
        if rdate == today:
            return True
    return False


def data_through(readings) -> str:
    """period_label of the reading with the latest non-null observation_date; "" if none.

    The PWA freshness footer ("data through …") shows the freshest indicator's period.
    """
    freshest: Optional[Any] = None
    for r in readings:
        if r.observation_date is None:
            continue
        if freshest is None or r.observation_date > freshest.observation_date:
            freshest = r
    return freshest.period_label if freshest is not None else ""


def build_card(result: PipelineResult, final: FinalBriefing, *, run_label: str, now_et) -> dict:
    """Build the briefing.json dict EXACTLY per the B5 PWA card contract.

    state/briefing/fallback_text come from final.source ("llm" -> default + final.output;
    else fallback + final.fallback_text). regime is result.regime_today; regime_label is the
    plain-English label (or "" when regime is unknown). now_et is a tz-aware ET datetime whose
    DST-correct offset is carried into generated_at.
    """
    is_llm = final.source == "llm"
    regime = result.regime_today
    return {
        "schema_version": SCHEMA_VERSION,
        "state": "default" if is_llm else "fallback",
        "generated_at": now_et.isoformat(timespec="seconds"),
        "run_label": run_label,
        "data_through": data_through(result.readings),
        "regime": regime,
        "regime_label": REGIME_PLAIN.get(regime, "") if regime is not None else "",
        # The ≤2 indicator ids the deterministic classifier used to pick the regime (display-only —
        # the PWA shows them as driver badges; never part of the LLM request).
        "drivers": list(result.drivers),
        # W1 (Bruce, D+1): the rule that fired and the clauses that satisfied it, so the
        # card can show WHY this label rather than asserting it. Optional in the schema —
        # public/demo/*.json predate it and must stay valid.
        "rule": result.rule,
        "basis": [list(b) for b in result.basis],
        "briefing": final.output if is_llm else None,
        "fallback_text": None if is_llm else final.fallback_text,
    }


@dataclass
class DailyOutcome:
    published: bool
    source: str  # "llm" | "fallback" | "refusal" | "skipped-offwindow" | "error"
    run_label: Optional[str]
    card: Optional[dict]
    record: Optional[RunRecord]
    reason: str = ""


def orchestrate(
    *,
    source: FredSource,
    primary: Optional[LLMClient],
    failover: Optional[LLMClient],
    as_of: Optional[date],
    run_label: str,
    now_et,
    out_dir: str | Path,
    cache_path: str | Path = pipeline.DEFAULT_CACHE_PATH,
) -> DailyOutcome:
    """Run the full daily pipeline and write public/{indicators,briefing}.json (G1 acceptance).

    primary/failover: the first non-None is the primary, the other the failover; if BOTH are
    None the LLM is skipped and the deterministic fallback card is rendered. A FRED failure is
    caught and surfaced as published=False/source="error" WITHOUT overwriting briefing.json.
    """
    out_dir = Path(out_dir)

    # Step 1 — FRED pull + classifier. A fetch failure means: do NOT touch briefing.json (keep
    # last-good); return early so the CLI fails the run with a fail-ping + nonzero exit.
    try:
        result = run_pipeline(
            source,
            as_of=as_of,
            cache_path=cache_path,
            write_indicators_to=out_dir / "indicators.json",
        )
    except FredError as exc:
        return DailyOutcome(
            published=False, source="error", run_label=run_label, card=None, record=None,
            reason=f"FRED unavailable: {exc}",
        )
    except Exception as exc:  # noqa: BLE001 — any fetch/parse failure ⇒ keep last-good, fail loud
        return DailyOutcome(
            published=False, source="error", run_label=run_label, card=None, record=None,
            reason=f"pipeline failed: {type(exc).__name__}: {exc}",
        )

    # Pick the LLM primary/failover from whichever clients were constructed.
    clients = [c for c in (primary, failover) if c is not None]
    llm_primary = clients[0] if clients else None
    llm_failover = clients[1] if len(clients) > 1 else None

    # Step 2 — build the request + produce the briefing (LLM → validate → retry → template).
    started = time.perf_counter()
    if result.ok:
        request = result.briefing_request()
        if llm_primary is not None:
            final = produce_briefing(request, primary=llm_primary, failover=llm_failover)
            # Best-effort: one produce_briefing invocation. NOT the exact provider HTTP-call count
            # (a retry+failover can issue up to ~4) — but at 2 runs/day vs the ~1500 RPD cap the
            # quota widget only needs the order of magnitude (see run-record.schema.json).
            llm_calls = 1
        else:
            final = FinalBriefing("fallback", None, fallback_text=fallback_template(request))
            llm_calls = 0
    else:
        # Implausible value: pipeline skipped the LLM; render the deterministic fallback over a
        # minimal request built from the (rejected) readings. validator_status stays "skipped".
        request = {
            "regime_today": None,
            "indicators": [r.to_request_indicator() for r in result.readings],
            "releases_last_24h": [],
        }
        final = FinalBriefing("fallback", None, fallback_text=fallback_template(request))
        llm_calls = 0
    latency_ms = round((time.perf_counter() - started) * 1000)

    # Step 3 — render + write the PWA card.
    card = build_card(result, final, run_label=run_label, now_et=now_et)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "briefing.json").write_text(
        json.dumps(card, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    # Step 4 — audit the run (PRD §339). any_stale is passed explicitly (not LLM-facing).
    record = observability.record_from(
        request, final,
        run_label=run_label,
        prompt_version=prompt.PROMPT_VERSION,
        latency_ms=latency_ms,
        llm_calls=llm_calls,
        timestamp=now_et.isoformat(timespec="seconds"),
        any_stale=result.any_stale,
        # ANY tier, unlike any_stale (core-only, UI banner). This is what pages now that a
        # transient source failure does not: a vendor outage shows up as its rows aging out.
        stale_indicators=[r.id for r in result.readings if getattr(r, "stale", False)],
    )
    observability.log_run(record)

    is_refusal = final.validation is not None and final.validation.status == "refusal"
    source = "refusal" if is_refusal else final.source
    return DailyOutcome(
        published=True, source=source, run_label=run_label, card=card, record=record,
    )
