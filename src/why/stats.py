"""The PRD's success metrics, computed from what Why already records (log, evidence, reports, consults).

    approval rate          mined decisions approved at review / (approved + rejected). Pending are excluded.
    breaks caught          guard failures on PRs, merges and upgrades: changes stopped before they merged.
    overridden violations  violations whose decision was then superseded or retired instead of the code being
                           fixed. This is an upper bound on false positives: some overrides are legitimate
                           changes of mind, but every false positive ends up here.
    prevention             agents that asked Why over MCP, and agent diffs that would have broken a decision.
    weekly                 per ISO week, how many changes Why caught.

The PRD targets: approval rate >= 60% (75% at three months), false positives < 10% (< 5%).
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
from typing import Any

from .store import Store

MINED = {"commit", "pr_body", "pr_comment", "agent_log", "entire_checkpoint"}
GATE = ("pr", "pr-merge", "merge", "upgrade", "push")  # contexts where a failure stops a change
TARGETS = {"approval_rate": 0.60, "false_positive_rate": 0.10}


def _week(ts: str) -> str:
    try:
        y, w, _ = datetime.fromisoformat(ts).isocalendar()
        return f"{y}-W{w:02d}"
    except ValueError:
        return "?"


def compute(store: Store) -> dict[str, Any]:
    decisions = {d.id: d for d in store.decisions()}
    log = store.log()
    evidence = store.evidence()

    # ---- review: did mining propose the right things? -------------------------------------
    approved: set[str] = set()
    rejected: set[str] = set()
    for r in log:
        if r["from"] == "proposed" and r["to"] == "active":
            approved.add(r["decision_id"])
        elif r["from"] == "proposed" and r["to"] == "retired":
            rejected.add(r["decision_id"])
    rejected -= approved
    by_source: dict[str, Counter] = defaultdict(Counter)
    for did in approved | rejected:
        d = decisions.get(did)
        if d and d.source.type in MINED:
            by_source[d.source.type]["approved" if did in approved else "rejected"] += 1
    mined_ok = sum(c["approved"] for c in by_source.values())
    mined_no = sum(c["rejected"] for c in by_source.values())
    pending = sum(1 for d in decisions.values() if d.status == "proposed" and d.source.type in MINED)

    # ---- enforcement: what was caught, and what was overridden ---------------------------
    gate = [e for e in evidence if e["context"] in GATE]
    runs = {e["run_id"] for e in gate if e.get("run_id")}
    failed_runs = {e["run_id"] for e in gate if e.get("run_id") and e["result"] == "fail"}
    caught = [e for e in gate if e["result"] == "fail"]
    by_ctx = Counter(e["context"] for e in caught)
    first_fail: dict[str, str] = {}
    for e in caught:
        first_fail.setdefault(e["decision_id"], e["timestamp"])
    overridden = sorted({r["decision_id"] for r in log if r["decision_id"] in first_fail
                         and r["to"] in ("superseded", "retired") and r["ts"] >= first_fail[r["decision_id"]]})
    violated_decisions = len(first_fail)
    fp_rate = (len(overridden) / violated_decisions) if violated_decisions else None

    upgrades = store.reports("upgrade")
    merges = store.reports("merge")

    # ---- prevention: agents asking before editing ----------------------------------------
    consults = store.consults(limit=100000)
    asked = [c for c in consults if c["tool"] == "why_decisions_for"]
    selfchecks = [c for c in consults if c["tool"] == "why_run_guards"]
    proposals = [c for c in consults if c["tool"] == "why_propose_decision"]

    weekly: dict[str, Counter] = defaultdict(Counter)
    for e in caught:
        weekly[_week(e["timestamp"])]["caught"] += 1
    for c in selfchecks:
        if c.get("decisions"):
            weekly[_week(c["ts"])]["prevented"] += 1

    rate = mined_ok / (mined_ok + mined_no) if (mined_ok + mined_no) else None
    return {
        "review": {
            "approved": mined_ok, "rejected": mined_no, "pending": pending, "approval_rate": rate,
            "by_source": {k: dict(v) for k, v in sorted(by_source.items())},
            "target": TARGETS["approval_rate"],
        },
        "enforcement": {
            "gated_runs": len(runs), "runs_with_violations": len(failed_runs),
            "violations_caught": len(caught), "by_context": dict(by_ctx),
            "upgrades_checked": len(upgrades), "upgrades_blocked": sum(1 for u in upgrades if u.get("verdict") == "blocked"),
            "merges_checked": len(merges),
            "merge_contradictions": sum(len(m.get("contradictions") or []) for m in merges),
            "decisions_violated": violated_decisions, "overridden": overridden,
            "false_positive_rate_upper_bound": fp_rate, "target": TARGETS["false_positive_rate"],
        },
        "prevention": {
            "consults": len(asked), "self_checks": len(selfchecks),
            "self_checks_that_caught": sum(1 for c in selfchecks if c.get("decisions")),
            "agent_proposals": len(proposals),
        },
        "guards": {
            "active": sum(1 for d in decisions.values() if d.status == "active"),
            "violated": sum(1 for d in decisions.values() if d.status == "violated"),
            "quarantined_flaky": sum(1 for d in decisions.values() if d.guard and d.guard.flaky),
        },
        "weekly": [{"week": w, **dict(c)} for w, c in sorted(weekly.items())][-12:],
    }


def server_wide(stores: dict[str, Store]) -> dict[str, Any]:
    """Across every repo the server knows (GitHub App installs): the PRD's adoption metrics."""
    weeks: dict[str, set[str]] = defaultdict(set)
    for name, st in stores.items():
        for row in compute(st)["weekly"]:
            if row.get("caught"):
                weeks[row["week"]].add(name)
    return {"repos": len(stores),
            "repos_with_violation_caught_by_week": [{"week": w, "repos": len(r)} for w, r in sorted(weeks.items())][-12:]}
