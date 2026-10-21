"""Data models for the pipeline.

`IndicatorReading` is the pipeline's internal, richer view of one indicator (it
carries Δ3m and staleness, which the classifier and UI need). `to_request_indicator`
projects it down to the *exact* `BriefingRequest.indicator` shape locked in
docs/schemas/briefing-io-v1.0.md (additionalProperties:false — so internal-only
fields like delta_3m / stale must never leak into the LLM contract).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from .config import BRIEFING_CONTEXT_IDS

REGIME_LABELS = (
    "soft_landing",
    "late_cycle_expansion",
    "stagflation_risk",
    "recovery",
    "slowdown",
)


@dataclass(frozen=True)
class Observation:
    """One FRED observation after the `units` transform is applied."""

    date: str  # ISO period date, e.g. "2026-05-01" (period start, as FRED reports)
    value: Optional[float]  # None == FRED "." (missing)


@dataclass
class IndicatorReading:
    """Pipeline-internal view of one indicator for the current run."""

    id: str
    display_name: str
    value: Optional[float]
    unit: str
    period_label: str  # "May 2026" | "Q1 2026" | "2026-06-18"
    last_release_date: str  # ISO date the series was last updated on FRED
    observation_date: Optional[str] = None  # period date of `value` (internal; release detection)
    delta: Optional[float] = None  # 1-period change vs previous observation
    delta_3m: Optional[float] = None  # ~3-month change (classifier input only)
    stale: bool = False  # plausible but older than the spec's stale_after_days
    staleness_days: Optional[int] = None
    note: Optional[str] = None
    spark: list[float] = field(default_factory=list)  # recent values oldest→newest (PWA sparkline)
    tier: str = "core"  # V2: core (feeds the briefing/regime) | drawer | market (display-only)
    level: Optional[float] = None  # V2: raw underlying FRED level (e.g. CPI index ~322 behind 4.2% YoY).
    # Chip 3rd "raw numbers" mode. PWA-only; never enters to_request_indicator (LLM contract).
    history: list = field(default_factory=list)  # V2 detail chart: [[iso_date, value], …] oldest→newest
    source_url: str = ""   # FRED series page (detail-view "View on FRED" link)
    frequency: str = ""    # "daily" | "monthly" | "quarterly" (drives the detail timeframe options)
    long_name: Optional[str] = None  # full official series title; PWA detail sheet only (falls back to display_name)

    def to_request_indicator(self) -> dict[str, Any]:
        """Project to the BriefingRequest.indicator contract (schema-clean)."""
        out: dict[str, Any] = {
            "id": self.id,
            "display_name": self.display_name,
            "value": self.value,
            "unit": self.unit,
            "period_label": self.period_label,
            "last_release_date": self.last_release_date,
        }
        if self.delta is not None:
            out["delta"] = self.delta
        if self.note is not None:
            out["note"] = self.note
        return out


@dataclass
class Release:
    """An indicator whose value changed since the last cached run (≤24h fresh)."""

    id: str
    prior_value: Optional[float]
    new_value: Optional[float]
    note: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": self.id,
            "prior_value": self.prior_value,
            "new_value": self.new_value,
        }
        if self.note is not None:
            out["note"] = self.note
        return out


@dataclass
class PipelineResult:
    """Output of a B1 run.

    `ok=True`  → `briefing_request` carries the data half (as_of_date / indicators /
                 releases_last_24h); regime_today/regime_yesterday/drivers are left for
                 the B2 classifier to fill before the LLM call.
    `ok=False` → at least one value failed the pre-LLM plausibility gate; the caller
                 must skip the LLM and render the deterministic fallback (no briefing).
    """

    ok: bool
    as_of_date: str
    readings: list[IndicatorReading] = field(default_factory=list)
    releases: list[Release] = field(default_factory=list)
    rejected: list[dict[str, Any]] = field(default_factory=list)  # plausibility failures
    any_stale: bool = False
    regime_today: Optional[str] = None
    regime_yesterday: Optional[str] = None
    drivers: list[str] = field(default_factory=list)
    # W1: which classifier branch fired, and the clauses that satisfied it. Carried so the
    # card can answer "why are you calling it that" without the PWA re-deriving the rules.
    rule: str = ""
    basis: list = field(default_factory=list)  # [(indicator_id, observed, op, threshold)]

    def _briefing_readings(self) -> list["IndicatorReading"]:
        """The readings that feed the LLM: the 7 core tier PLUS the curated context handful
        (config.BRIEFING_CONTEXT_IDS — oil/dollar/yield-curve, V3). The remaining drawer/market
        rows stay display-only. The regime + drivers are computed by the classifier from core
        alone, so this wider set only enriches the narration's evidence base, never the regime."""
        return [r for r in self.readings
                if r.tier == "core" or r.id in BRIEFING_CONTEXT_IDS]

    def briefing_request_data(self) -> dict[str, Any]:
        """The data-half of BriefingRequest (core + context). Classifier adds regime_* + drivers."""
        briefing = self._briefing_readings()
        briefing_ids = {r.id for r in briefing}
        return {
            "as_of_date": self.as_of_date,
            "indicators": [r.to_request_indicator() for r in briefing],
            "releases_last_24h": [r.to_dict() for r in self.releases if r.id in briefing_ids],
        }

    def briefing_request(self) -> dict[str, Any]:
        """The complete BriefingRequest handed to the LLM (B3) — core + curated context tier."""
        briefing = self._briefing_readings()
        briefing_ids = {r.id for r in briefing}
        return {
            "as_of_date": self.as_of_date,
            "regime_today": self.regime_today,
            "regime_yesterday": self.regime_yesterday,
            "drivers": self.drivers,
            "indicators": [r.to_request_indicator() for r in briefing],
            "releases_last_24h": [r.to_dict() for r in self.releases if r.id in briefing_ids],
        }

    def indicators_json(self) -> dict[str, Any]:
        """The PWA dashboard feed (indicators.json) — ALL tiers + freshness metadata. The PWA
        groups by `tier`: core rows always visible, drawer collapsed, market in its own strip."""
        return {
            "as_of_date": self.as_of_date,
            "any_stale": self.any_stale,
            "indicators": [
                {
                    **r.to_request_indicator(),
                    "tier": r.tier,
                    "stale": r.stale,
                    "staleness_days": r.staleness_days,
                    "spark": r.spark,
                    "level": r.level,
                    "history": r.history,
                    "source_url": r.source_url,
                    "frequency": r.frequency,
                    "long_name": r.long_name,
                    # Published so the daily eval can RE-RUN the locked classifier against the
                    # same inputs (quality.check_regime_reproducible / R1). delta_3m is the
                    # classifier's only trend signal, so without it R1 can only assert the far
                    # weaker "the label is a known string". Still never enters the LLM contract —
                    # to_request_indicator() omits it by design.
                    "delta_3m": r.delta_3m,
                }
                for r in self.readings
            ],
        }
