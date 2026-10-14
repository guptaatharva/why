"""High-level operations shared by the CLI, the GitHub App and the dashboard API."""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

from . import cache, guards, team
from .codeindex import CodeIndex
from .gitutil import head
from .mine import index_for
from .models import Decision, Evidence, now
from .runner import Sandbox
from .store import Store, StoreError

STABILITY_RUNS = 3


@dataclass
class ApproveResult:
    decision: Decision
    activated: bool
    message: str
    runs: list[str] = field(default_factory=list)
    pending: bool = False  # recorded, but more approvals are needed (teams mode)


def approve(store: Store, decision_id: str, actor: str = "human", use_model: bool | None = None,
            index: CodeIndex | None = None) -> ApproveResult:
    """Approve a proposed decision: generate its guard, prove it stable, then activate it."""
    d = store.get(decision_id)
    if d.status not in ("proposed", "violated"):
        raise StoreError(f"{d.id} is {d.status}; only proposed decisions can be approved")
    if d.status == "proposed":
        gate = team.record_approval(store, d, actor)
        if not gate.satisfied:
            note = f"approved by {team.resolve(store, actor) or actor}; waiting on {' and '.join(gate.missing)}"
            store.log_event(d.id, d.status, d.status, actor, note=note)
            return ApproveResult(store.get(d.id), False, f"{d.id} {note}", pending=True)
    else:
        team.require(store, actor, "approve", d)
    if d.guard is None:
        d.guard = guards.adopt_existing(store, d) or guards.generate(store, d, index or index_for(store), use_model)
        store.save(d)
        store.log_event(d.id, d.status, d.status, actor, note=f"guard generated: {d.guard.path} ({d.guard.generated_by})")
    results = stability(store, d)
    d = store.get(d.id)
    passes = results.count("pass")
    if passes == len(results):
        d.guard.flaky, d.guard.stability_runs = False, len(results)
        store.save(d)
        store.transition(d.id, "active", actor, note=f"approved; guard passed {passes}/{len(results)} runs")
        return ApproveResult(store.get(d.id), True, f"{d.id} active: guard passed {passes}/{len(results)}", results)
    if passes == 0:
        return ApproveResult(d, False, f"{d.id} stays proposed: the guard fails on the current code. Either the code "
                                       f"already breaks this decision, or the guard is wrong ({d.guard.path}).", results)
    d.guard.flaky, d.guard.stability_runs = True, len(results)
    store.save(d)
    store.log_event(d.id, d.status, d.status, "why", note=f"guard quarantined as flaky ({passes}/{len(results)})")
    return ApproveResult(d, False, f"{d.id} quarantined: guard is flaky ({passes}/{len(results)} passes).", results)


def stability(store: Store, d: Decision, runs: int = STABILITY_RUNS) -> list[str]:
    assert d.guard
    results = []
    commit = head(store.root)
    with Sandbox(store) as sb:
        for _ in range(runs):
            outcome = sb.run([d.guard.path])
            r = outcome.by_decision().get(d.id)
            res = r.result if r else "error"
            results.append(res)
            store.add_evidence(Evidence(d.id, res, "stability", commit, log=(r.log if r else outcome.raw_log)[-3000:],
                                        duration_s=r.duration_s if r else 0))
    return results


def reject(store: Store, decision_id: str, actor: str = "human", note: str = "") -> Decision:
    team.require(store, actor, "reject", store.get(decision_id))
    return store.transition(decision_id, "retired", actor, note or "rejected at review")


def retire(store: Store, decision_id: str, actor: str = "human", note: str = "") -> Decision:
    team.require(store, actor, "retire", store.get(decision_id))
    d = store.transition(decision_id, "retired", actor, note or "retired")
    if d.guard:
        p = store.root / d.guard.path
        if p.exists():
            p.rename(p.with_name("_retired_" + p.name))
            d.guard.path = p.with_name("_retired_" + p.name).relative_to(store.root).as_posix()
            store.save(d)
    return d


def supersede(store: Store, decision_id: str, reason: str, new_rule: str | None = None, actor: str = "human") -> Decision:
    """Replace a decision with a newer one that records why the old one no longer holds."""
    old = store.get(decision_id)
    team.require(store, actor, "supersede", old)
    new = Decision(
        id=store.next_id(),
        title=(new_rule or f"Superseded: {old.title}")[:72],
        rule=new_rule or f"{old.id} no longer applies",
        reason=reason,
        status="proposed" if new_rule else "retired",
        kind="custom",
        scope=old.scope,
        source=old.source.__class__(type="manual", ref=old.id, quote=reason, author=actor, date=now()),
        confidence=1.0,
        supersedes=old.id,
    )
    store.add(new, actor=actor)
    old = store.transition(old.id, "superseded", actor, note=f"superseded by {new.id}: {reason}")
    old.superseded_by = new.id
    if old.guard:
        p = store.root / old.guard.path
        if p.exists():
            p.unlink()
    store.save(old)
    return new


# ---- checks (F3) ---------------------------------------------------------------------


@dataclass
class CheckItem:
    decision_id: str
    title: str
    reason: str
    result: str
    log: str
    source_link: str
    source_ref: str
    cached: bool = False


@dataclass
class CheckReport:
    run_id: str
    context: str
    commit: str
    items: list[CheckItem]
    duration_s: float
    created_at: str = field(default_factory=now)

    @property
    def cached(self) -> int:
        return sum(1 for i in self.items if i.cached)

    @property
    def violations(self) -> list[CheckItem]:
        return [i for i in self.items if i.result in ("fail", "error")]

    @property
    def ok(self) -> bool:
        return not self.violations

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["ok"] = self.ok
        d["violations"] = [i.decision_id for i in self.violations]
        return d


def check(store: Store, context: str = "local", code_ref: str | None = None, trusted_ref: str | None = None,
          code_dir=None, commit: str | None = None, apply_transitions: bool = True,
          extra: dict[str, Any] | None = None, patch: str | None = None, record: bool = True) -> CheckReport:
    """Run every active (and currently violated) guard against the code; record evidence."""
    decisions = [d for d in store.decisions(("active", "violated")) if d.guard and not d.guard.flaky]
    if not commit and code_ref:
        from .gitutil import git

        commit = git(store.root, "rev-parse", code_ref, check=False).strip() or code_ref
    commit = commit or head(store.root)
    run_id = uuid.uuid4().hex[:12]
    items: list[CheckItem] = []
    duration = 0.0
    if decisions:
        # Reuse results for guards already run on this exact code tree (cache.py).
        keys: dict[str, str] = {}
        hits: dict[str, dict[str, Any]] = {}
        tree = cache.code_tree(store, code_ref, patch) if cache.enabled(store) and not code_dir else None
        if tree:
            support = cache.support_hash(store, trusted_ref)
            for d in decisions:
                assert d.guard
                src = cache._read(store, d.guard.path, trusted_ref)
                if src:
                    keys[d.id] = cache.key(tree, src, support)
                    hit = cache.get(store, keys[d.id])
                    if hit:
                        hits[d.id] = hit
        to_run = [d for d in decisions if d.id not in hits]
        by: dict[str, Any] = {}
        raw_log = ""
        if to_run:
            with Sandbox(store, code_ref=code_ref, trusted_ref=trusted_ref, code_dir=code_dir, patch=patch) as sb:
                outcome = sb.run([d.guard.path for d in to_run])  # type: ignore[union-attr]
            duration = outcome.duration_s
            by, raw_log = outcome.by_decision(), outcome.raw_log
        for d in decisions:
            cached = d.id in hits
            if cached:
                res, log, dur = hits[d.id]["result"], hits[d.id]["log"], 0.0
            else:
                r = by.get(d.id)
                res = r.result if r else "error"
                log = (r.log if r else raw_log[-2000:]) or ""
                dur = r.duration_s if r else 0
                if d.id in keys:
                    cache.put(store, keys[d.id], {"result": res, "log": log[-3000:], "commit": commit,
                                                  "at": now()})
            items.append(CheckItem(d.id, d.title, d.reason, res, log, d.source.link, d.source.ref, cached))
            if record:
                store.add_evidence(Evidence(d.id, res, context, commit, run_id, log[-3000:], dur,
                                            detail={**(extra or {}), **({"cached": True} if cached else {})}))
            if apply_transitions:
                if res == "fail" and d.status == "active":
                    store.transition(d.id, "violated", "why check", note=f"guard failed at {commit[:12]} ({context})")
                elif res == "pass" and d.status == "violated":
                    store.transition(d.id, "active", "why check", note=f"guard passing again at {commit[:12]}")
    report = CheckReport(run_id, context, commit, items, duration)
    if record:
        store.save_report("check", run_id, {**report.to_dict(), **(extra or {})})
    return report


# ---- blame (F4) ----------------------------------------------------------------------


def blame(store: Store, target: str) -> list[Decision]:
    """Decisions that govern `file[:line]`."""
    file, _, line = target.partition(":")
    file = file.lstrip("./")
    index = index_for(store)
    func = index.at(file, int(line)) if line.isdigit() else None
    out = []
    for d in store.decisions():
        if d.status in ("retired",):
            continue
        syms = d.scope.get("symbols", [])
        files = d.scope.get("files", [])
        if func is not None:
            if func.ref in syms or any(s.endswith(f":{func.qualname}") for s in syms):
                out.append(d)
                continue
            if not syms and file in files:
                out.append(d)
        elif file in files or any(file.startswith(p.rstrip("/")) for p in d.params.get("paths", []) if p != "."):
            out.append(d)
    return out
