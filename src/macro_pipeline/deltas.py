"""Delta computation + release detection.

- `compute_delta`: 1-period change vs the previous observation (goes into the
  BriefingRequest indicator.delta).
- `compute_delta_3m`: ~3-month change, the classifier's labor/sentiment trend input
  (Δ3m unemployment, Δ umich — see docs/classifier/regime-classifier-v1.0.md). Chosen
  by nearest observation to (latest_date − 91 days) so it is robust across monthly /
  quarterly / daily cadences and gaps.
- `detect_releases`: an indicator "released" when a genuinely new print (newer
  observation_date than the cached run) has landed → releases_last_24h entry.
"""

from __future__ import annotations

from typing import Optional

from ._dates import parse_iso
from .cache import Cache
from .models import IndicatorReading, Observation, Release

_THREE_MONTHS_DAYS = 91


def compute_delta(observations: list[Observation]) -> Optional[float]:
    """latest − previous, when both are present. observations are newest-first."""
    present = [o for o in observations if o.value is not None]
    if len(present) < 2:
        return None
    return round(present[0].value - present[1].value, 4)


def compute_delta_3m(observations: list[Observation]) -> Optional[float]:
    """latest − (value nearest to 3 months before latest). newest-first input."""
    present = [o for o in observations if o.value is not None]
    if len(present) < 2:
        return None
    latest = present[0]
    latest_date = parse_iso(latest.date)
    target_ordinal = latest_date.toordinal() - _THREE_MONTHS_DAYS
    # Nearest prior observation to the 3-months-ago target (exclude latest itself).
    candidates = present[1:]
    best = min(candidates, key=lambda o: abs(parse_iso(o.date).toordinal() - target_ordinal))
    return round(latest.value - best.value, 4)


def detect_releases(readings: list[IndicatorReading], cache: Cache) -> list[Release]:
    """Emit a Release for each indicator whose latest print is newer than the cache.

    Cold start (no cached snapshot for an id) yields no release — we can't know what
    is "new" on the first run.
    """
    releases: list[Release] = []
    for r in readings:
        prev = cache.previous(r.id)
        if not prev:
            continue
        prev_obs_date = prev.get("observation_date")
        if prev_obs_date and r.observation_date and r.observation_date > prev_obs_date:
            releases.append(
                Release(id=r.id, prior_value=prev.get("value"), new_value=r.value)
            )
    return releases
