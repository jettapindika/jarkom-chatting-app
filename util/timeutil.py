"""ISO-8601 UTC timestamps with millisecond precision.

Every timestamp that travels on the wire or into a trace event is produced
here, so the format is defined in exactly one place.
"""

from __future__ import annotations

from datetime import datetime, timezone

__all__ = ["ISO8601_FORMAT", "parse_iso8601", "to_iso8601", "utc_now"]

_MILLISECONDS_PER_SECOND = 1000

#: ``strftime``/``strptime`` pattern matching :func:`to_iso8601` output.
ISO8601_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"


def utc_now() -> datetime:
    """Return the current time as a timezone-aware UTC :class:`datetime`."""
    return datetime.now(timezone.utc)


def to_iso8601(moment: datetime) -> str:
    """Format ``moment`` as ISO-8601 UTC, truncated to milliseconds.

    Naive datetimes are rejected rather than guessed at: an ambiguous local
    time silently rendered as UTC is the kind of bug that only shows up when
    two machines in different timezones talk to each other.
    """
    if moment.tzinfo is None:
        raise ValueError("moment must be timezone-aware")
    utc = moment.astimezone(timezone.utc)
    millis = utc.microsecond // _MILLISECONDS_PER_SECOND
    return f"{utc.strftime('%Y-%m-%dT%H:%M:%S')}.{millis:03d}Z"


def parse_iso8601(text: str) -> datetime:
    """Parse the exact format produced by :func:`to_iso8601`.

    Raises:
        ValueError: if ``text`` is not an ISO-8601 UTC timestamp with
            millisecond precision.
    """
    return datetime.strptime(text, ISO8601_FORMAT).replace(tzinfo=timezone.utc)
