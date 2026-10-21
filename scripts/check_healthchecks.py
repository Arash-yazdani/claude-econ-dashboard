"""Query healthchecks.io for the status of every check in the project.

Requires a READ-ONLY API key in $HEALTHCHECKS_RO_KEY (project-scoped, not account-scoped).
Create one at: healthchecks.io → your project → Settings → API Access → "Create Read-Only Key".

Usage:
  python scripts/check_healthchecks.py          # print table, exit 1 on any P0/P1 down
  python scripts/check_healthchecks.py --alert   # also send Slack alerts (severity in the message)

Severity tiers:
  P0  Pipeline or scheduler is dead — the reader is affected NOW.
  P1  Publishing but degraded — fallbacks or stale data reaching the reader.
  P2  Eval infrastructure — not reader-facing, investigate next business day.

Exit codes: 0 = all up (or only P2 down), 1 = P0 or P1 down, 2 = cannot query.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

import requests

from macro_pipeline import observability

# Map check names (as they appear on healthchecks.io) to severity tiers.
_TIERS: dict[str, int] = {
    "Daily Macro Brief": 0,
    "Cron Worker": 0,
    "Publish Quality": 1,
    "Data Sources": 1,
    "Judge Daily": 2,
    "Weekly Regression": 2,
}
_TIER_LABEL = {0: "P0", 1: "P1", 2: "P2"}
_TIER_DESC = {
    0: "pipeline/scheduler dead",
    1: "publishing degraded",
    2: "eval infrastructure",
}


def _age(last_ping: str | None) -> str:
    if not last_ping:
        return "never"
    try:
        dt = datetime.fromisoformat(last_ping.replace("Z", "+00:00"))
        h = (datetime.now(timezone.utc) - dt).total_seconds() / 3600
        return f"{h:.1f}h ago"
    except Exception:
        return last_ping


def main() -> int:
    key = os.environ.get("HEALTHCHECKS_RO_KEY", "")
    if not key:
        print("HEALTHCHECKS_RO_KEY is not set — cannot query. "
              "Create a read-only key at healthchecks.io → project → Settings → API Access.")
        return 2

    r = requests.get("https://healthchecks.io/api/v3/checks/",
                     headers={"X-Api-Key": key}, timeout=20)
    if r.status_code == 401:
        print("401 Unauthorized — the read-only key is invalid or expired.")
        return 2
    r.raise_for_status()

    checks = r.json().get("checks", [])
    if not checks:
        print("No checks found in this project.")
        return 0

    down: dict[int, list[str]] = {0: [], 1: [], 2: []}
    print(f"{'Name':<30} {'Tier':<5} {'Status':<10} {'Last ping':<16}")
    print("-" * 65)
    for c in sorted(checks, key=lambda c: c.get("name", "")):
        name = c.get("name", "(unnamed)")
        status = c.get("status", "?")
        tier = _TIERS.get(name, 2)
        last = _age(c.get("last_ping"))
        flag = " !!!" if status == "down" else ""
        print(f"{name:<30} {_TIER_LABEL[tier]:<5} {status:<10} {last:<16}{flag}")
        if status == "down":
            down[tier].append(name)

    if "--alert" in sys.argv:
        for tier in (0, 1, 2):
            if not down[tier]:
                continue
            names = ", ".join(down[tier])
            severity = _TIER_LABEL[tier]
            desc = _TIER_DESC[tier]
            observability.send_alert(
                f"[{severity}] Healthchecks DOWN ({desc}): {names}. "
                f"Run `python scripts/check_healthchecks.py` for the full table.")

    all_down = down[0] + down[1] + down[2]
    if all_down:
        for tier in (0, 1, 2):
            if down[tier]:
                print(f"\n{_TIER_LABEL[tier]} DOWN ({_TIER_DESC[tier]}): {', '.join(down[tier])}")
        # P2 alone does not fail the job — eval infra is not reader-facing.
        return 1 if down[0] or down[1] else 0
    print(f"\nAll {len(checks)} checks UP.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
