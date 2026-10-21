"""Local JSON cache (ADR-001: total cache ≤5MB).

Holds the *previous run's* per-indicator snapshot so the next run can detect new
prints (releases_last_24h) and carry yesterday's regime label forward
(regime_yesterday — actually written by the B2 classifier; B1 just persists the slot).

The cache is tiny by construction (7 indicators × a handful of scalars); the 5MB cap
is enforced defensively on save so a future change can't silently blow the budget.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

MAX_CACHE_BYTES = 5 * 1024 * 1024  # 5MB hard cap (ADR-001)


class CacheTooLargeError(RuntimeError):
    pass


class Cache:
    def __init__(self, data: Optional[dict[str, Any]] = None) -> None:
        data = data or {}
        # snapshot: id -> {"value", "period_label", "last_release_date", "observation_date"}
        self.snapshot: dict[str, dict[str, Any]] = data.get("snapshot", {})
        self.regime_yesterday: Optional[str] = data.get("regime_yesterday")

    @classmethod
    def load(cls, path: str | Path) -> "Cache":
        p = Path(path)
        if not p.exists():
            return cls()
        try:
            return cls(json.loads(p.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            # Corrupt cache must never crash the daily run — treat as a cold start.
            return cls()

    def previous(self, indicator_id: str) -> Optional[dict[str, Any]]:
        return self.snapshot.get(indicator_id)

    def to_dict(self) -> dict[str, Any]:
        return {"snapshot": self.snapshot, "regime_yesterday": self.regime_yesterday}

    def update_snapshot(self, readings: list, regime_today: Optional[str] = None) -> None:
        """Record this run's readings as next run's `previous`."""
        self.snapshot = {
            r.id: {
                "value": r.value,
                "period_label": r.period_label,
                "last_release_date": r.last_release_date,
                "observation_date": r.observation_date or r.last_release_date,
            }
            for r in readings
        }
        if regime_today is not None:
            self.regime_yesterday = regime_today

    def save(self, path: str | Path) -> None:
        payload = json.dumps(self.to_dict(), indent=2, sort_keys=True)
        encoded = payload.encode("utf-8")
        if len(encoded) > MAX_CACHE_BYTES:
            raise CacheTooLargeError(
                f"Cache {len(encoded)} bytes exceeds {MAX_CACHE_BYTES} (5MB) cap"
            )
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(payload, encoding="utf-8")
