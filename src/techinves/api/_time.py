"""Naive-UTC timestamp helper.

DB `DateTime` columns here are not timezone-aware (portable across sqlite and
Postgres without a `timezone=True` migration decision), so all timestamps
written/compared in this package are naive-but-UTC, produced by this helper
instead of the deprecated `datetime.utcnow()`.
"""

from __future__ import annotations

from datetime import UTC, datetime


def now_naive_utc() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def to_naive_utc(value: datetime) -> datetime:
    """Normalize a possibly tz-aware datetime to naive UTC.

    Postgres rejects a tz-aware value written into a naive `DateTime()`
    column (SQLite accepts it silently, masking the bug in tests).
    """
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)
