"""The three versioned objects: decisions, guards and evidence."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

STATES = ("proposed", "active", "violated", "superseded", "retired")

# Allowed transitions between decision states. Every transition is logged.
TRANSITIONS: dict[str, set[str]] = {
    "proposed": {"active", "retired"},
    "active": {"violated", "superseded", "retired"},
    "violated": {"active", "superseded", "retired"},
    "superseded": set(),
    "retired": {"proposed"},
}

# Guard kinds Why can compile without a model. Anything else is "custom"
# and needs Nemotron Super (or a human) to write the test.
GUARD_KINDS = ("call_count", "returns", "forbid_pattern", "custom")


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass
class Source:
    """Where a decision was mined from."""

    type: str  # commit | pr_comment | pr_body | agent_log | entire_checkpoint | manual
    ref: str = ""  # commit SHA, PR number, session id, ...
    quote: str = ""
    link: str = ""
    author: str = ""
    date: str = ""


@dataclass
class Assumption:
    """An outside fact a decision depends on, checked with Tavily (F9)."""

    claim: str
    query: str = ""
    verdict: str = "unchecked"  # unchecked | holds | broken | unclear
    checked_at: str = ""
    explanation: str = ""
    urls: list[str] = field(default_factory=list)


@dataclass
class GuardRef:
    path: str  # repo-relative path of the generated test
    language: str = "python"
    framework: str = "pytest"
    generated_by: str = "template"  # template | nemotron-super | human
    flaky: bool = False
    stability_runs: int = 0


@dataclass
class Decision:
    id: str
    title: str
    rule: str
    reason: str = ""
    status: str = "proposed"
    kind: str = "custom"
    params: dict[str, Any] = field(default_factory=dict)
    scope: dict[str, list[str]] = field(default_factory=lambda: {"files": [], "symbols": []})
    source: Source = field(default_factory=lambda: Source(type="manual"))
    confidence: float = 0.5
    assumption: Assumption | None = None
    guard: GuardRef | None = None
    supersedes: str | None = None
    superseded_by: str | None = None
    approvals: list[dict[str, str]] = field(default_factory=list)  # [{by, at}], teams mode
    created_at: str = field(default_factory=now)
    updated_at: str = field(default_factory=now)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return {k: v for k, v in d.items() if v is not None and not (k == "approvals" and not v)}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Decision":
        d = dict(d)
        d["source"] = Source(**d.get("source") or {"type": "manual"})
        if d.get("assumption"):
            d["assumption"] = Assumption(**d["assumption"])
        if d.get("guard"):
            d["guard"] = GuardRef(**d["guard"])
        known = cls.__dataclass_fields__.keys()
        return cls(**{k: v for k, v in d.items() if k in known})


@dataclass
class Evidence:
    """The result of running one guard in one context."""

    decision_id: str
    result: str  # pass | fail | error | skipped
    context: str  # local | pr | merge | upgrade | stability
    commit: str = ""
    run_id: str = ""
    log: str = ""
    duration_s: float = 0.0
    detail: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
