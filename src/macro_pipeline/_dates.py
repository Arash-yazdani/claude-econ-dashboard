"""Small date helpers (period-label formatting + ISO parsing)."""

from __future__ import annotations

from datetime import date

from .config import Frequency

_MONTH_ABBR = ("", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
               "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def parse_iso(d: str) -> date:
    return date.fromisoformat(d[:10])


def format_period_label(observation_date: str, frequency: Frequency) -> str:
    """Human period label matching the regression fixtures.

    monthly  "2026-05-01" -> "May 2026"
    quarterly "2026-01-01" -> "Q1 2026"
    daily    "2026-06-18" -> "2026-06-18"
    """
    if frequency is Frequency.DAILY:
        return observation_date[:10]
    d = parse_iso(observation_date)
    if frequency is Frequency.QUARTERLY:
        quarter = (d.month - 1) // 3 + 1
        return f"Q{quarter} {d.year}"
    return f"{_MONTH_ABBR[d.month]} {d.year}"
