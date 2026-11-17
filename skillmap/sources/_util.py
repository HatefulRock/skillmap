from __future__ import annotations

from datetime import UTC, date, datetime


def parse_date(value: object) -> date | None:
    """ISO strings, epoch milliseconds, or None, to a date (UTC)."""
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000, tz=UTC).date()
    text = str(value).strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            return None


def dig(obj: object, *path: str | int, default: object = None) -> object:
    for key in path:
        if isinstance(obj, dict) and isinstance(key, str):
            obj = obj.get(key)
        elif isinstance(obj, list) and isinstance(key, int) and -len(obj) <= key < len(obj):
            obj = obj[key]
        else:
            return default
        if obj is None:
            return default
    return obj
