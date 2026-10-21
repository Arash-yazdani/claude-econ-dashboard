"""B4 output validator (the O-checks) + retry-once-then-template fallback.

Layer-1 deterministic gate on a BriefingOutput, given its source BriefingRequest
(docs/schemas/briefing-io-v1.0.md, PRD q24). Two publishable shapes:
  - NORMAL  → must pass O2 shape, O3 id-whitelist, O4 conditional emptiness,
              O5 number grounding (±0.05), period grounding, O8 banned phrases, length cap.
  - REFUSAL → must be EXACTLY the refusal envelope (O16/O17).
Anything else is invalid → the orchestrator retries the LLM once, then renders the
deterministic template (never an error message to the user — PRD/wireframe).

Non-negotiables (PRD): O5 (number grounding), O8 (banned phrases), O16/O17 (refusal).

KNOWN LIMITS (flagged, faithful to the locked spec):
  - O8 uses the 10 D2-locked phrases (PRD line 330). It will NOT catch bare "buy"/"sell"/
    a ticker like "TQQQ" — PRD line 431 already calls for expanding this at the O3 retro.
  - Period grounding is heuristic (substring/prefix match against the input period labels).
  - The docs name the load-bearing checks but don't enumerate all of O1–O17; the checks
    implemented here are that named set.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

from .llm_client import BriefingLLMError, LLMClient, generate_briefing

# O8 — D2-locked banned phrases (PRD line 330). Lowercased substring match.
BANNED_PHRASES: tuple[str, ...] = (
    "should buy", "should sell", "recommend", "i think", "you should", "we should",
    "consider buying", "consider selling", "bullish on", "bearish on",
)

REFUSAL_ENVELOPE: dict[str, Any] = {
    "headline": None, "quotable_numbers": [], "whats_new": None,
    "regime_change": None, "error": "data_unavailable",
}

REGIME_PLAIN = {
    "soft_landing": "Soft landing", "late_cycle_expansion": "Late-cycle expansion",
    "stagflation_risk": "Stagflation territory", "recovery": "Recovery", "slowdown": "Slowing growth",
    # W3 — a STATUS, not a regime: the classifier declined rather than guessed. Mandatory here:
    # build_card does REGIME_PLAIN.get(regime, ""), and an empty label makes app.js hide the whole
    # regime block, silently taking W1's basis line down with it.
    "indeterminate": "No call today",
}

NUMBER_TOLERANCE = 0.05
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")
_YEAR = re.compile(r"\b(?:19|20)\d{2}\b")
_QUARTER = re.compile(r"Q[1-4]")
_WORD = re.compile(r"[A-Za-z]+")
# Whole-word month names (full + 3-letter), so "market"/"decimal"/"april" aren't mis-read.
_MONTHS = {
    "january", "february", "march", "april", "may", "june", "july", "august",
    "september", "october", "november", "december",
    "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "oct", "nov", "dec",
}


@dataclass
class ValidationResult:
    status: str  # "valid" | "refusal" | "invalid"
    failed: list[tuple[str, str]] = field(default_factory=list)  # (check_id, reason)
    is_refusal: bool = False


# --- text / number / period extraction --------------------------------------------

def _output_texts(output: dict[str, Any]) -> list[str]:
    texts: list[str] = []
    if isinstance(output.get("headline"), str):
        texts.append(output["headline"])
    for q in output.get("quotable_numbers") or []:
        if isinstance(q, dict) and isinstance(q.get("sentence"), str):
            texts.append(q["sentence"])
    for fld in ("whats_new", "regime_change"):
        if isinstance(output.get(fld), str):
            texts.append(output[fld])
    return texts


def _preceding_word(text: str, idx: int) -> str:
    words = re.findall(r"[A-Za-z0-9]+", text[:idx])
    return words[-1].lower() if words else ""


def _data_numbers(text: str, day_tokens: frozenset[str] = frozenset()) -> list[tuple[float, bool]]:
    """Numbers to ground (value, is_basis_points). Only years that sit INSIDE a period
    (month/quarter context), ISO dates, and a DAY-OF-MONTH that matches a real period in the
    request are excluded — every other number, including a year-shaped value with no period
    context, must be O5-grounded (else a fabricated '2026' would slip past both O5 and period
    grounding).

    day_tokens carries the request's real "mon d" pairs (see _source_day_tokens). A DAILY
    indicator's period_label is an ISO date, so when the writer describes that day in prose
    ("Brent rose to 96.02 on Sep 2") the 2 used to be read as a data value and rejected by O5 —
    while 'Sep' was rejected by the period check, because no month word appears in an ISO label.
    Together those two gaps failed a TRUE sentence, and did it only on days a daily series
    released (all 3 such fallbacks in 167 runs; 2026-08-25 and 2026-09-02 froze a session each).
    A day is skipped ONLY if it matches an actual period, so a wrong date still fails."""
    cleaned = _ISO_DATE.sub(" ", text)                 # ISO dates are periods, not data
    cleaned = re.sub(r"(?<=\d),(?=\d)", "", cleaned)   # drop thousands separators (71,000→71000)
    out: list[tuple[float, bool]] = []
    for m in _NUMBER.finditer(cleaned):
        tok = m.group()
        prev = cleaned[m.start() - 1] if m.start() > 0 else ""
        if prev in ("Q", "q"):  # quarter ordinal, e.g. the 1 in Q1
            continue
        val = float(tok)
        # day-of-month inside a real period ("Sep 2" when the data's period IS 2026-09-02)
        if "." not in tok and 1 <= val <= 31:
            pw = _preceding_word(cleaned, m.start())
            if pw in _MONTHS and f"{pw[:3]} {int(val)}" in day_tokens:
                continue
        if "." not in tok and len(tok.lstrip("-")) == 4 and 1900 <= val <= 2099:
            pw = _preceding_word(cleaned, m.start())
            if pw in _MONTHS or re.fullmatch(r"q[1-4]", pw):
                continue  # a real year inside a period ("May 2026" / "Q1 2026")
            # otherwise NOT a period-year → fall through so a fabricated year gets grounded
        after = cleaned[m.end():m.end() + 6].lower()
        if after.startswith(("-year", "-yr", "yr")) or (
            after.startswith("y") and (len(after) == 1 or not after[1].isalpha())
        ):
            continue  # tenor like "10-year" / "10Y" — part of the indicator name, not a value
        tail = cleaned[m.end():m.end() + 14].lower().lstrip()
        is_bps = tail.startswith("basis point") or tail.startswith("bps")
        out.append((val, is_bps))
    return out


_MONTH_KEYS = ("jan", "feb", "mar", "apr", "may", "jun",
               "jul", "aug", "sep", "oct", "nov", "dec")


def _source_day_tokens(request: dict[str, Any]) -> frozenset[str]:
    """The request's real calendar days as "mon d" (e.g. {"sep 2"}), from every ISO date it
    carries — so O5 can tell a day-of-month apart from a data value without accepting a wrong one."""
    toks: set[str] = set()
    for ind in request.get("indicators", []):
        for s in (str(ind.get("period_label", "")), str(ind.get("last_release_date", ""))):
            for iso in _ISO_DATE.findall(s):
                toks.add(f"{_MONTH_KEYS[int(iso[5:7]) - 1]} {int(iso[8:10])}")
    return frozenset(toks)


def _source_values(request: dict[str, Any]) -> set[float]:
    vals: set[float] = set()
    for ind in request.get("indicators", []):
        for key in ("value", "delta"):
            if ind.get(key) is not None:
                vals.add(round(float(ind[key]), 6))
    for rel in request.get("releases_last_24h", []):
        for key in ("prior_value", "new_value"):
            if rel.get(key) is not None:
                vals.add(round(float(rel[key]), 6))
    return vals


def _grounds(n: float, is_bps: bool, sources: set[float]) -> bool:
    eps = NUMBER_TOLERANCE + 1e-9
    candidates = [n, n / 100.0] if is_bps else [n]  # bps: "10 basis points" == 0.10%
    return any(any(abs(c - s) <= eps for s in sources) for c in candidates)


def _source_period_tokens(request: dict[str, Any]) -> set[str]:
    """The period tokens the input actually contains — years, ISO dates, quarters, 3-letter
    month keys — so grounding is whole-token membership, not raw substring."""
    toks: set[str] = set()
    for ind in request.get("indicators", []):
        for s in (str(ind.get("period_label", "")), str(ind.get("last_release_date", ""))):
            toks |= set(_YEAR.findall(s))
            toks |= set(_ISO_DATE.findall(s))
            toks |= {q.lower() for q in _QUARTER.findall(s)}
            toks |= {w.lower()[:3] for w in _WORD.findall(s) if w.lower() in _MONTHS}
            # An ISO date IS a period, so it licenses its own month word too. Without this a
            # daily row (period_label "2026-09-02") gave the writer no legal way to name the day.
            for iso in _ISO_DATE.findall(s):
                toks.add(_MONTH_KEYS[int(iso[5:7]) - 1])
    return toks


# --- individual checks (return a failure reason, or None to pass) ------------------

_ALLOWED_TOP_KEYS = {"headline", "quotable_numbers", "whats_new", "regime_change", "error"}


def _check_shape(o: dict[str, Any]) -> Optional[str]:
    fails = []
    if not isinstance(o.get("headline"), str) or not o["headline"]:
        fails.append("headline missing/empty")
    qn = o.get("quotable_numbers")
    if not isinstance(qn, list) or len(qn) != 2:
        fails.append("quotable_numbers must have exactly 2")
    else:
        for q in qn:
            if not (isinstance(q, dict) and isinstance(q.get("indicator_id"), str)
                    and isinstance(q.get("sentence"), str)):
                fails.append("malformed quotable_number")
            else:
                if set(q) - {"indicator_id", "sentence"}:  # schema additionalProperties:false
                    fails.append("quotable_number has unknown keys")
                if len(q["sentence"]) > 200:  # schema sentence maxLength
                    fails.append("quotable_number sentence > 200 chars")
    for fld in ("whats_new", "regime_change"):
        if o.get(fld) is not None and not isinstance(o.get(fld), str):
            fails.append(f"{fld} must be string or null")
    if o.get("error") is not None:
        fails.append("error must be absent on a normal briefing")
    extra = set(o) - _ALLOWED_TOP_KEYS  # schema additionalProperties:false
    if extra:
        fails.append(f"unknown keys {sorted(extra)}")
    return "; ".join(fails) or None


def _check_whitelist(o: dict[str, Any], request: dict[str, Any]) -> Optional[str]:
    ids = {ind["id"] for ind in request.get("indicators", [])}
    bad = [q.get("indicator_id") for q in o.get("quotable_numbers", [])
           if q.get("indicator_id") not in ids]
    return f"O3 indicator_id not in request: {bad}" if bad else None


def _check_conditional(o: dict[str, Any], request: dict[str, Any]) -> Optional[str]:
    fails = []
    has_release = bool(request.get("releases_last_24h"))
    if (o.get("whats_new") is not None) != has_release:
        fails.append("whats_new must be non-null iff a release dropped")
    yest = request.get("regime_yesterday")
    changed = yest is not None and request.get("regime_today") != yest
    if (o.get("regime_change") is not None) != changed:
        fails.append("regime_change must be non-null iff regime changed vs a known prior")
    return "; ".join(fails) or None


def _check_numbers(o: dict[str, Any], request: dict[str, Any]) -> Optional[str]:
    sources = _source_values(request)
    days = _source_day_tokens(request)
    bad = [n for text in _output_texts(o) for n, is_bps in _data_numbers(text, days)
           if not _grounds(n, is_bps, sources)]
    return f"O5 numbers not grounded (±{NUMBER_TOLERANCE}): {bad}" if bad else None


def _check_periods(o: dict[str, Any], request: dict[str, Any]) -> Optional[str]:
    toks = _source_period_tokens(request)
    bad: list[str] = []
    for text in _output_texts(o):
        bad += [y for y in _YEAR.findall(text) if y not in toks]
        bad += [d for d in _ISO_DATE.findall(text) if d not in toks]
        bad += [q for q in _QUARTER.findall(text) if q.lower() not in toks]
        bad += [w for w in _WORD.findall(text)
                if w.lower() in _MONTHS and w.lower()[:3] not in toks]
    return f"period(s) not in input: {bad}" if bad else None


def _check_banned(o: dict[str, Any]) -> Optional[str]:
    blob = " ".join(_output_texts(o)).lower()
    hits = [p for p in BANNED_PHRASES if re.search(r"\b" + re.escape(p) + r"\b", blob)]
    return f"O8 banned phrases: {hits}" if hits else None


def _check_length(o: dict[str, Any]) -> Optional[str]:
    fails = []
    words = sum(len(t.split()) for t in _output_texts(o))
    if words > 80:
        fails.append(f"total {words} words > 80")
    headline = o.get("headline") or ""
    if len(headline) > 140:
        fails.append("headline > 140 chars")
    if len(headline.split()) > 22:
        fails.append("headline > 22 words")
    return "; ".join(fails) or None


def validate(output: Any, request: dict[str, Any]) -> ValidationResult:
    if not isinstance(output, dict):
        return ValidationResult("invalid", [("O1", "not a JSON object")])

    # Refusal shape (O16/O17): headline null or error set → must be the exact envelope.
    if output.get("error") == "data_unavailable" or output.get("headline") is None:
        if output == REFUSAL_ENVELOPE:
            return ValidationResult("refusal", is_refusal=True)
        return ValidationResult("invalid", [("O16", "malformed refusal envelope")], is_refusal=True)

    shape = _check_shape(output)
    if shape:  # deeper checks assume a well-formed shape
        return ValidationResult("invalid", [("O2", shape)])

    checks = [
        ("O3", _check_whitelist(output, request)),
        ("O4", _check_conditional(output, request)),
        ("O5", _check_numbers(output, request)),
        ("period", _check_periods(output, request)),
        ("O8", _check_banned(output)),
        ("length", _check_length(output)),
    ]
    failed = [(cid, msg) for cid, msg in checks if msg]
    return ValidationResult("valid" if not failed else "invalid", failed)


# --- fallback template + orchestration ---------------------------------------------

def fallback_template(request: dict[str, Any]) -> str:
    """Deterministic card text (wireframe state 4): `Regime: X. CPI: Y%. Unemployment: Z%.`"""
    regime = REGIME_PLAIN.get(request.get("regime_today"), str(request.get("regime_today") or "—"))
    by_id = {ind["id"]: ind for ind in request.get("indicators", [])}

    def fmt(ind_id: str) -> str:
        v = by_id.get(ind_id, {}).get("value")
        if v is None:
            return "n/a"
        # Round like the PWA does. This text is the DEGRADED state — the one Bruce sees on the
        # day something went wrong — and "CPI: 3.46353%" reads as a machine leaking its internals
        # at exactly the moment the product most needs to look deliberate.
        try:
            return f"{float(v):.1f}%"
        except (TypeError, ValueError):
            return f"{v}%"

    return f"Regime: {regime}. CPI: {fmt('cpi_yoy')}. Unemployment: {fmt('unemployment')}."


@dataclass
class FinalBriefing:
    source: str  # "llm" | "fallback"
    validation: Optional[ValidationResult] = None
    output: Optional[dict[str, Any]] = None  # validated briefing (when source == "llm")
    model: Optional[str] = None
    fallback_text: Optional[str] = None      # template card text (when source == "fallback")
    # The drafts the validator REJECTED, in attempt order (invalid only; a refusal is a correct
    # output, not a rejected one). Empty on an llm run. Without these, two 2026 fallbacks
    # (08-25, 09-02) could only be diagnosed by reproducing the failure signature offline.
    rejected: list[dict[str, Any]] = field(default_factory=list)


def produce_briefing(
    request: dict[str, Any], *, primary: LLMClient, failover: Optional[LLMClient] = None,
) -> FinalBriefing:
    """LLM call → validate → retry once → deterministic template (PRD q24).

    A valid refusal (the LLM correctly bailing on bad data) is NOT retried — it renders
    the template directly. Two invalid outputs, or both providers down, → template.
    """
    last: Optional[ValidationResult] = None
    rejected: list[dict[str, Any]] = []
    for _attempt in range(2):  # original + one retry
        try:
            res = generate_briefing(request, primary=primary, failover=failover)
        except BriefingLLMError:
            break  # both providers down → fallback
        last = validate(res.output, request)
        if last.status == "valid":
            return FinalBriefing("llm", last, output=res.output, model=res.model)
        if last.status == "refusal":
            break  # correct refusal → no retry, render template
        rejected.append(res.output)  # keep the evidence: what was said, and why it failed, together
    return FinalBriefing("fallback", last, fallback_text=fallback_template(request),
                         rejected=rejected)
