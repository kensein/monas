"""Normalisasi timestamp observasi/forecast untuk join HARP."""
from __future__ import annotations

from datetime import datetime, timezone


def normalize_valid_time(ts: str | datetime | None) -> str | None:
    """UTC ISO8601 dengan suffix Z, tanpa microsecond."""
    if ts is None or ts == "":
        return None
    if isinstance(ts, datetime):
        dt = ts
    else:
        s = str(ts).strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(s)
        except ValueError:
            try:
                dt = datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S")
            except ValueError:
                return str(ts)
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
