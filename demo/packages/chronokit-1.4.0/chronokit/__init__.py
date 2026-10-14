"""chronokit 1.4.0 (fictional): ISO-8601 parsing that normalises to UTC."""

from datetime import datetime, timezone

__version__ = "1.4.0"


def parse(text: str) -> datetime:
    """Parse an ISO-8601 timestamp. Offsets are honoured and the result is converted to UTC."""
    dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def format(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
