"""Daily quality checks across the three eval dimensions (D1 brief / D2 regime / D3 indicators).

Deliberately DETERMINISTIC-FIRST. Two of the three dimensions are better served by exact checks
than by an LLM, and the locked judge prompt refuses to score numbers on purpose ("double-judging
them would muddy the κ calibration"):

  D1 — brief quality      → judge.py's C1/C3/C4/C5. Genuinely subjective; the LLM is right here.
  D2 — regime accuracy    → the regime is RULES-ONLY (classifier v1.1, D3/D9-signed) and the LLM
                            never picks it, so "accuracy" means reproducibility (R1) and faithful
                            narration (R2), not "did the model choose well".
  D3 — indicator accuracy → O5 already grounds every number at 100%; what is genuinely missing is
                            reconciling what we PUBLISHED against what the source says now (I2).

Everything here is pure and offline except reconcile_indicators(), which re-fetches. Nothing here
may raise into a run: callers treat a check error as a failed check, never as a crash.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

from . import classifier
from .validator import REGIME_PLAIN


@dataclass
class Check:
    """One pass/fail quality check with a human-readable reason when it fails."""

    id: str
    dimension: str          # "D1" | "D2" | "D3"
    passed: bool
    detail: str = ""
    # W6 (Bruce, D+1): per-row evidence behind the check, for the accuracy report. `detail` is a
    # one-line human summary; this is the table underneath it. Additive — `passed` and `detail`
    # are unchanged, so the existing test_i2_* suite is the regression proof.
    rows: list = field(default_factory=list)
    # W5: recorded and visible, but excluded from every score. "Shadow first" has to be a real
    # mechanism or it is just a promise — a new check that silently moves the D-scores could trip
    # the judge-daily healthcheck on its first run and be indistinguishable from a real
    # regression. Promote by flipping this to False once a replay shows it holds.
    advisory: bool = False

    def to_json(self) -> dict[str, Any]:
        return {"id": self.id, "dimension": self.dimension,
                "passed": self.passed, "detail": self.detail}


@dataclass
class QualityReport:
    checks: list[Check] = field(default_factory=list)

    def add(self, check: Check) -> None:
        self.checks.append(check)

    def by_dimension(self, dim: str) -> list[Check]:
        return [c for c in self.checks if c.dimension == dim]

    def scored(self) -> list[Check]:
        """Checks that count. Advisory ones are observed, never scored (see Check.advisory)."""
        return [c for c in self.checks if not c.advisory]

    def dimension_score(self, dim: str) -> Optional[float]:
        """Fraction of that dimension's SCORED checks that passed (None when it has none)."""
        items = [c for c in self.by_dimension(dim) if not c.advisory]
        if not items:
            return None
        return sum(1 for c in items if c.passed) / len(items)

    def failures(self) -> list[Check]:
        """Scored failures only — an advisory failure is information, not a verdict."""
        return [c for c in self.scored() if not c.passed]

    def advisories(self) -> list[Check]:
        return [c for c in self.checks if c.advisory and not c.passed]

    def to_json(self) -> dict[str, Any]:
        return {
            "checks": [c.to_json() for c in self.checks],
            "scores": {d: self.dimension_score(d) for d in ("D1", "D2", "D3")},
            "failed": [c.id for c in self.failures()],
            "advisory_failed": [c.id for c in self.advisories()],
        }


# ── D2 · regime ────────────────────────────────────────────────────────────────────

def check_regime_reproducible(readings: list, published_regime: Optional[str],
                              prev_regime: Optional[str]) -> Check:
    """R1 — re-run the locked classifier on the same readings; it must reproduce the published
    label. Catches a corrupted cache or a stale regime_yesterday feeding the hysteresis."""
    try:
        recomputed = classifier.classify(readings, prev_regime=prev_regime).regime
    except Exception as exc:  # noqa: BLE001 — a check error is a failed check, never a crash
        return Check("R1_regime_reproducible", "D2", False, f"classifier raised: {exc}")
    ok = recomputed == published_regime
    return Check("R1_regime_reproducible", "D2", ok,
                 "" if ok else f"published {published_regime!r} but rules give {recomputed!r}")


def check_regime_narration(output: Any, published_regime: Optional[str]) -> Check:
    """R2 — the briefing must narrate the regime it was given, and must not name a DIFFERENT one.

    Nothing checked this before: the LLM is handed the label and told to narrate it, but no
    O-check ever compared the prose against it. Case T5 exists to exercise exactly this.
    """
    if not isinstance(output, dict):
        return Check("R2_regime_narration", "D2", False, "no briefing output to inspect")
    if output.get("error") == "data_unavailable" or output.get("headline") is None:
        return Check("R2_regime_narration", "D2", True, "refusal envelope — nothing to narrate")

    text = " ".join(str(v) for v in (
        output.get("headline"), output.get("whats_new"), output.get("regime_change"),
    ) if v)
    text += " " + " ".join(str(q.get("sentence", "")) for q in output.get("quotable_numbers", []))
    low = text.lower()

    mine = REGIME_PLAIN.get(published_regime or "", "")
    others = [plain for rid, plain in REGIME_PLAIN.items()
              if rid != published_regime and plain.lower() not in mine.lower()]
    intruders = [p for p in others if re.search(rf"\b{re.escape(p.lower())}\b", low)]
    if intruders:
        return Check("R2_regime_narration", "D2", False,
                     f"names a different regime: {', '.join(intruders)}")
    return Check("R2_regime_narration", "D2", True, "")


# W5 (Bruce D+1). Surface forms the writer legitimately uses for each regime. The prompt does not
# require the exact REGIME_PLAIN string, so matching only that would fail correct briefings —
# "Soft landing continues" and "the economy is slowing" are both faithful narrations.
REGIME_SURFACE_FORMS: dict[str, tuple[str, ...]] = {
    "soft_landing": ("soft landing", "soft-landing"),
    "late_cycle_expansion": ("late-cycle", "late cycle", "late-cycle expansion"),
    "stagflation_risk": ("stagflation", "stagflationary"),
    "recovery": ("recovery", "recovering", "rebound"),
    "slowdown": ("slowing growth", "slowdown", "slowing", "losing steam"),
}


def check_regime_named(output: Any, published_regime: Optional[str]) -> Check:
    """R2-positive — the briefing must actually NAME the regime it was handed.

    check_regime_narration above is negative-only: it fails a briefing that names a DIFFERENT
    regime, so a briefing naming NO regime passes it vacuously. That is most of the value missing.

    Kept as a separate check rather than folded in, because the two have different blast radii: a
    briefing naming the wrong regime is a contradiction, one naming none is an omission. Merging
    them would hide which occurred.
    """
    if not isinstance(output, dict):
        return Check("R2b_regime_named", "D2", False, "no briefing output to inspect")
    if output.get("error") == "data_unavailable" or output.get("headline") is None:
        return Check("R2b_regime_named", "D2", True, "refusal envelope — nothing to narrate")
    forms = REGIME_SURFACE_FORMS.get(published_regime or "", ())
    if not forms:
        return Check("R2b_regime_named", "D2", True,
                     f"no surface forms registered for {published_regime!r}")
    low = " ".join(str(v) for v in (
        output.get("headline"), output.get("whats_new"), output.get("regime_change")) if v).lower()
    hit = next((f for f in forms if f in low), None)
    return Check("R2b_regime_named", "D2", bool(hit),
                 "" if hit else f"headline never names the published regime ({published_regime})")


def check_rule_named(rule: Optional[str]) -> Check:
    """R4b — the published regime came from a NAMED rule, not the catch-all fallthrough.

    The check that would have caught the whole of Bruce's complaint. `soft_landing` is emitted by
    both a real rule and the `else`, so the label alone cannot tell you whether anything actually
    matched. Over 12 years, 13 of 53 soft_landing months (25%) are fallthrough.

    Advisory until W3 lands. Once the fallthrough becomes `indeterminate`, `rule="unmatched"` is a
    LEGITIMATE published state and this check must treat it as such rather than failing the days
    W3 makes correct — hence "unmatched" passing below.
    """
    if not rule:
        return Check("R4b_rule_named", "D2", True, "no rule recorded (card predates W1)",
                     advisory=True)
    if rule == "fallthrough":
        return Check("R4b_rule_named", "D2", False,
                     "regime came from the catch-all else — no rule matched these readings",
                     advisory=True)
    return Check("R4b_rule_named", "D2", True, f"rule: {rule}", advisory=True)


# ── D3 · indicators ────────────────────────────────────────────────────────────────

# Direction words the briefing may use, and the sign each one asserts about `delta`.
_UP_WORDS = ("higher", "rose", "rising", "climbed", "hotter", "firmer", "firming",
             "jumped", "up", "increased", "accelerated")
_DOWN_WORDS = ("lower", "fell", "falling", "cooled", "cooling", "eased", "easing",
               "slipped", "down", "decreased", "declined", "softened")


def check_direction_framing(output: Any, request: dict) -> Check:
    """I4 — a direction word must match the sign of that indicator's delta.

    O5 grounds the NUMBER but never the verb, so "unemployment fell to 4.3 percent" passes O5
    even when the delta is positive. This closes that gap for the two quotable sentences.
    """
    if not isinstance(output, dict) or not output.get("quotable_numbers"):
        return Check("I4_direction_framing", "D3", True, "no quotable numbers to check")
    deltas = {i["id"]: i.get("delta") for i in request.get("indicators", [])}
    problems: list[str] = []
    for q in output.get("quotable_numbers", []):
        ind_id, sentence = q.get("indicator_id"), str(q.get("sentence", "")).lower()
        delta = deltas.get(ind_id)
        if delta is None or delta == 0:
            continue  # no direction asserted by the data → nothing to contradict
        said_up = any(re.search(rf"\b{w}\b", sentence) for w in _UP_WORDS)
        said_down = any(re.search(rf"\b{w}\b", sentence) for w in _DOWN_WORDS)
        if said_up and said_down:
            problems.append(f"{ind_id}: says both up and down")
        elif said_up and delta < 0:
            problems.append(f"{ind_id}: says up but delta {delta}")
        elif said_down and delta > 0:
            problems.append(f"{ind_id}: says down but delta {delta}")
    ok = not problems
    return Check("I4_direction_framing", "D3", ok, "" if ok else "; ".join(problems))


def published_observation_date(row: dict) -> Optional[str]:
    """The ISO date of the observation a published row actually reports.

    Prefer the last history point (always ISO); fall back to period_label only when it is already
    ISO — for monthly/quarterly rows period_label is prose ("Jun 2026", "Q1 2026") and unusable.
    """
    hist = row.get("history") or []
    if hist and isinstance(hist[-1], (list, tuple)) and hist[-1]:
        return str(hist[-1][0])
    label = str(row.get("period_label") or "")
    return label if re.fullmatch(r"\d{4}-\d{2}-\d{2}", label) else None


def reconcile_indicators(published: list[dict], source, *, specs,
                         tolerance: float = 1e-6, live_tolerance_pct: float = 0.02,
                         limit: int = 10) -> Check:
    """I2 — re-fetch each indicator and compare against what we PUBLISHED, **at the same date**.

    This is what "accuracy of all the indices" actually requires, and nothing did it before: O5
    only proves the briefing quotes indicators.json faithfully, never that indicators.json itself
    matches FRED/Treasury/gold-api. Catches a stale cache, a wrong series id, unit drift, or a
    silent vendor revision of a value we already published.

    Matching is BY OBSERVATION DATE, not "newest value now". The source routinely gains newer
    observations between our publish and this check — that is freshness, not inaccuracy, and
    comparing against the newest value would flag every run as a false positive.

    A fetch failure or a date the source no longer carries marks that indicator *unverified*
    rather than failing: a vendor outage is a source-health problem (alerted separately), and a
    disappeared date is a revision to investigate, not proof we published a wrong number.

    Specs marked `live_tick` (gold spot) are compared with a RELATIVE band instead of exactly.
    Their value for "today" keeps moving all day, so date-matching alone is not enough — an exact
    compare flags ordinary market drift as an accuracy failure. Observed live: published 4022.70
    vs source 4024.30 minutes later, a 0.04% move that is simply the gold price ticking. The band
    still catches everything I2 exists for, because a wrong series id, a unit error, or a stale
    cache miss by orders of magnitude, not fractions of a percent.
    """
    by_id = {s.id: s for s in specs}
    mismatches: list[str] = []
    unverified: list[str] = []
    rows: list[dict] = []
    checked = 0

    def record(row_id, as_of, pub, src, verdict, note=""):
        """One entry per PUBLISHED indicator, always with an explicit verdict.

        The loop `continue`s on derived rows and on blanked values, so "one row per published
        indicator" and "one row per reconciled indicator" are different sets. Emitting only the
        latter would leave silent holes in Bruce's table — a row he can see on the dashboard but
        cannot find in the report reads as something being hidden.
        """
        rows.append({"id": row_id, "as_of": as_of, "published": pub,
                     "source": src, "verdict": verdict, "note": note})

    for row in published:
        spec = by_id.get(row.get("id"))
        if spec is None:
            record(row.get("id"), published_observation_date(row), row.get("value"), None,
                   "not_reconciled", "derived row — computed here, not downloaded")
            continue
        if row.get("value") is None:
            record(row["id"], published_observation_date(row), None, None,
                   "not_reconciled", "no value published (blanked or unavailable)")
            continue
        as_of = published_observation_date(row)
        if as_of is None:
            unverified.append(f"{row['id']} (no observation date)")
            record(row["id"], None, row.get("value"), None, "not_reconciled",
                   "no observation date to match on")
            continue
        try:
            fresh = source.fetch_series(spec.fred_series_id, spec.units, limit)
            live = next((o.value for o in fresh.observations
                         if o.date == as_of and o.value is not None), None)
        except Exception as exc:  # noqa: BLE001
            unverified.append(f"{row['id']} ({type(exc).__name__})")
            record(row["id"], as_of, row.get("value"), None, "not_reconciled",
                   f"source unreachable ({type(exc).__name__})")
            continue
        if live is None:
            unverified.append(f"{row['id']} (source has no value for {as_of})")
            record(row["id"], as_of, row.get("value"), None, "not_reconciled",
                   f"source carries no value for {as_of}")
            continue
        checked += 1
        pub_val, live_val = float(row["value"]), float(live)
        if getattr(spec, "live_tick", False):
            # Relative band — a live tick's "today" value moves continuously (see docstring).
            allowed = abs(live_val) * live_tolerance_pct
            drifted = abs(pub_val - live_val) > allowed
            note = f" (live tick, >{live_tolerance_pct:.0%} drift)" if drifted else ""
        else:
            drifted = abs(pub_val - live_val) > tolerance
            note = ""
        if drifted:
            mismatches.append(
                f"{row['id']} @{as_of}: published {row['value']} vs source {live}{note}")
        record(row["id"], as_of, pub_val, live_val,
               "mismatched" if drifted else "matched", note.strip())
    if mismatches:
        detail = "; ".join(mismatches)
    elif unverified:
        detail = f"{checked} verified, unverified: {', '.join(unverified)}"
    else:
        detail = f"{checked} indicators verified against source"
    return Check("I2_source_reconciliation", "D3", not mismatches, detail, rows=rows)


# ── D1 · brief quality (from the existing judge verdict) ───────────────────────────

def checks_from_verdict(verdict: dict, *, threshold: float = 4.0) -> list[Check]:
    """Turn one judge verdict into per-criterion D1 checks.

    Also recomputes `flag_below_3` from the scores instead of trusting the model's self-report —
    nothing cross-checked it before, so an inconsistent judge could hide a below-3.
    """
    out: list[Check] = []
    scores = {
        "c1_quotability": verdict.get("c1_quotability"),
        "c3_tone": verdict.get("c3_tone"),
        "c4_conditionals": verdict.get("c4_conditionals"),
        "c5_length_format": verdict.get("c5_length_format"),
    }
    for key, score in scores.items():
        if score is None:
            continue  # refusal envelope: c1/c3/c4 are null by design
        out.append(Check(f"D1_{key}", "D1", float(score) >= threshold,
                         "" if float(score) >= threshold else f"scored {score} (< {threshold})"))
    real = [float(s) for s in scores.values() if s is not None]
    below3 = any(s < 3 for s in real)
    out.append(Check("D1_no_below_3", "D1", not below3,
                     "" if not below3 else "a criterion scored below 3"))
    return out


def recomputed_flag_below_3(verdict: dict) -> bool:
    """The trustworthy version of verdict['flag_below_3'] — derived, not self-reported."""
    real = [v for k, v in verdict.items()
            if k in ("c1_quotability", "c3_tone", "c4_conditionals", "c5_length_format")
            and v is not None]
    try:
        return any(float(v) < 3 for v in real)
    except (TypeError, ValueError):
        return False
