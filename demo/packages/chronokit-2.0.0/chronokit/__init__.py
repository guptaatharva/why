"""chronokit 2.0.0 (fictional).

Changelog: "parse() now preserves the wall-clock time written in the input and returns a
naive datetime, which is simpler for most UI use cases." The signature is unchanged.
"""

from datetime import datetime, timezone

__version__ = "2.0.0"


def parse(text: str) -> datetime:
    """Parse an ISO-8601 timestamp and return its wall-clock time (naive)."""
    dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    return dt.replace(tzinfo=None)


def format(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S")
