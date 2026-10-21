"""B1 orchestration: FRED ingest → reading assembly → plausibility → staleness →
releases → cache, producing the data half of a BriefingRequest.

Seam for B2: this module deliberately stops at the data half. The classifier consumes
`PipelineResult.readings` (it needs value + delta_3m) and fills regime_today /
regime_yesterday / drivers before the B3 LLM call.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any, Optional

from . import classifier, deltas, derived, observability, plausibility, staleness
from ._dates import format_period_label
from .cache import Cache
from .config import (
    BLS_SERIES,
    BLS_SERIES_PAGE,
    CACHE_HISTORY_LEN,
    FRED_SERIES_PAGE,
    HISTORY_LEN,
    INDICATORS,
)
from .fred_client import FredSource
from .models import IndicatorReading, Observation, PipelineResult

DEFAULT_CACHE_PATH = "data/cache/pipeline-cache.json"
DEFAULT_INDICATORS_PATH = "public/indicators.json"

# Detail-view chart: target points after downsampling, and how many freshest points to keep at
# FULL resolution (so 1M/3M views on a daily series stay smooth) before thinning older history.
HISTORY_POINTS = 220
HISTORY_RECENT_FULL = 90


def _downsample_history(observations: list[Observation]) -> list[list[Any]]:
    """[[iso_date, value], …] oldest→newest, ~HISTORY_POINTS points. Keeps the most recent
    HISTORY_RECENT_FULL at full resolution (smooth short ranges) and thins the older tail."""
    real = [[o.date, o.value] for o in reversed(observations) if o.value is not None]
    if len(real) <= HISTORY_POINTS:
        return real
    recent = real[-HISTORY_RECENT_FULL:]
    older = real[:-HISTORY_RECENT_FULL]
    budget = max(HISTORY_POINTS - len(recent), 1)
    step = len(older) / budget
    older_ds = [older[int(i * step)] for i in range(budget)]
    return older_ds + recent


def _pick_value(observations: list[Observation]) -> tuple[Optional[float], str]:
    """Return (freshest non-null value, its observation date).

    Daily series carry trailing nulls on weekends/holidays — use the latest *real*
    value. If the whole window is null, value is None at the most-recent period.
    """
    if not observations:
        raise ValueError("no observations returned")
    for obs in observations:  # newest-first
        if obs.value is not None:
            return obs.value, obs.date
    return None, observations[0].date


def _source_url(spec) -> str:
    """The row's 'View source' link. An explicit spec.source_url wins (e.g. gold_spot -> gold-api.com);
    a BLS-origin series links to its BLS series page (the primary publisher — "use the source"); every
    other row links to its FRED series page."""
    if spec.source_url:
        return spec.source_url
    bls = BLS_SERIES.get(spec.fred_series_id)
    if bls:
        return f"{BLS_SERIES_PAGE}/{bls}"
    return f"{FRED_SERIES_PAGE}/{spec.fred_series_id}"


def build_reading(spec, result) -> IndicatorReading:
    observations = result.observations
    value, observation_date = _pick_value(observations)
    return IndicatorReading(
        id=spec.id,
        display_name=spec.display_name,
        value=value,
        unit=spec.unit,
        period_label=format_period_label(observation_date, spec.frequency),
        last_release_date=result.last_updated,
        observation_date=observation_date,
        delta=deltas.compute_delta(observations),
        delta_3m=deltas.compute_delta_3m(observations),
        # last ≤12 real values, oldest→newest, for the PWA sparkline (PWA-only; not sent to the LLM)
        spark=list(reversed([o.value for o in observations if o.value is not None][:12])),
        tier=spec.tier,
        # V2 detail view (PWA-only): downsampled multi-year history + dive-deeper link + cadence.
        history=_downsample_history(observations),
        source_url=_source_url(spec),
        frequency=spec.frequency.value,
        long_name=spec.long_name,
    )


def run_pipeline(
    source: FredSource,
    *,
    as_of: Optional[date] = None,
    cache_path: str | Path = DEFAULT_CACHE_PATH,
    write_indicators_to: Optional[str | Path] = DEFAULT_INDICATORS_PATH,
) -> PipelineResult:
    as_of = as_of or date.today()
    cache = Cache.load(cache_path)

    readings: list[IndicatorReading] = [
        build_reading(spec, source.fetch_series(spec.fred_series_id, spec.units, HISTORY_LEN))
        for spec in INDICATORS
    ]

    # V2 chip "raw numbers" mode: publish each indicator's underlying level. A "lin" indicator
    # already IS its level (value); a rate/change indicator (pc1/pch/chg) needs a second fetch of
    # the SAME series with units="lin" to recover the raw level (CPI index ~322 behind 4.2% YoY).
    # Display-only — never enters the briefing_request, so the LLM contract/regression is unchanged.
    for spec, r in zip(INDICATORS, readings):
        if spec.units == "lin":
            r.level = r.value
        else:
            lin = source.fetch_series(spec.fred_series_id, "lin", CACHE_HISTORY_LEN)
            r.level, _ = _pick_value(lin.observations)

    # V2: a display-only (drawer/market) value outside its plausible range drops to None so the
    # PWA shows "—" instead of a garbage number — and so it can't reach the core gate below.
    for r in readings:
        if r.tier != "core" and not plausibility.is_plausible(r.value, r.id):
            r.value = None
            observability.record_blanked(r.id)  # else the blanking is completely silent

    # Gate 1 — plausibility (CORE tier only). A single implausible CORE value fails the run
    # (skip LLM → fallback); drawer/market values never gate (see plausibility.validate).
    pl = plausibility.validate(readings)
    if not pl.ok:
        return PipelineResult(
            ok=False, as_of_date=as_of.isoformat(), readings=readings, rejected=pl.failures
        )

    # Gate 2 — staleness is advisory only (carry-forward #2): annotate, never reject.
    any_stale = staleness.apply(readings, as_of)

    releases = deltas.detect_releases(readings, cache)

    # B2 — deterministic regime classification. regime_yesterday comes from the prior
    # run's cached label; it also powers the recovery hysteresis (v1.1 Fix B).
    regime_yesterday = cache.regime_yesterday
    classification = classifier.classify(readings, prev_regime=regime_yesterday)

    cache.update_snapshot(readings, regime_today=classification.regime)
    cache.save(cache_path)

    # V3 derived "spread" rows (2s10s, real rates) — computed from the readings above, NO new FRED
    # call. Appended HERE on purpose: AFTER plausibility/staleness (which index INDICATORS_BY_ID[r.id]
    # and would KeyError on these non-config ids) and AFTER update_snapshot/classify (keep them out of
    # the cache + regime). Non-core → never reach briefing_request; indicators_json() publishes them.
    readings = readings + derived.build_derived(readings)

    result = PipelineResult(
        ok=True,
        as_of_date=as_of.isoformat(),
        readings=readings,
        releases=releases,
        any_stale=any_stale,
        regime_today=classification.regime,
        regime_yesterday=regime_yesterday,
        drivers=classification.drivers,
        rule=classification.rule,
        basis=list(classification.basis),
    )

    if write_indicators_to is not None:
        p = Path(write_indicators_to)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(result.indicators_json(), indent=2), encoding="utf-8")

    return result
