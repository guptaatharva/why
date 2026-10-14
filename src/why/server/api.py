"""JSON API used by the dashboard. One snapshot call returns everything a repo view needs."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, Header, HTTPException

from .. import llm, team
from ..gitutil import github_slug, head
from ..models import STATES, now
from ..store import Store, StoreError, normalize_id

router = APIRouter(prefix="/api")


class Registry:
    """Repos the server knows about: the local repo (`why serve`) and any GitHub App installs."""

    def __init__(self) -> None:
        self.repos: dict[str, Path] = {}

    def add(self, repo_id: str, root: Path) -> None:
        self.repos[repo_id] = Path(root)

    def store(self, repo_id: str) -> Store:
        root = self.repos.get(repo_id)
        if root is None:
            raise HTTPException(404, f"unknown repo {repo_id}")
        st = Store(root)
        if not st.exists:
            raise HTTPException(404, f"{repo_id} has no .why store")
        return st


registry = Registry()


def write_enabled() -> bool:
    return os.environ.get("WHY_DASHBOARD_WRITE", "1") not in ("0", "false", "no")


def models_info() -> dict[str, Any]:
    live = llm.available()
    out: dict[str, Any] = {"live": live, "base_url": llm.base_url()}
    for role in ("nano", "super", "ultra"):
        out[role] = llm.model_for(role) if live else os.environ.get(f"WHY_MODEL_{role.upper()}", llm.FALLBACK_IDS[role])
    out["tavily"] = bool(os.environ.get("TAVILY_API_KEY"))
    return out


def _last_results(evidence: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    last: dict[str, dict[str, Any]] = {}
    for e in evidence:
        if e["context"] in ("assumption",):
            continue
        last[e["decision_id"]] = e
    return last


def counts(st: Store) -> dict[str, int]:
    c = {s: 0 for s in STATES}
    for d in st.decisions():
        c[d.status] = c.get(d.status, 0) + 1
    return c


def repo_snapshot(st: Store, name: str | None = None, repo_id: str = "local") -> dict[str, Any]:
    evidence = st.evidence()
    last = _last_results(evidence)
    decisions = []
    for d in st.decisions():
        row = d.to_dict()
        e = last.get(d.id)
        row["last_result"] = e["result"] if e else None
        row["last_context"] = e["context"] if e else None
        row["last_evidence_at"] = e["timestamp"] if e else None
        row["evidence_count"] = sum(1 for x in evidence if x["decision_id"] == d.id)
        decisions.append(row)
    checks = st.reports("check")[:30]
    return {
        "repo": {"id": repo_id, "name": name or st.root.name, "github": github_slug(st.root), "head": head(st.root)},
        "generated_at": now(),
        "counts": counts(st),
        "decisions": decisions,
        "evidence": evidence[-300:],
        "log": st.log()[-300:],
        "checks": checks,
        "upgrades": st.reports("upgrade")[:20],
        "merges": st.reports("merge")[:20],
        "assumptions": st.reports("assumptions")[:5],
        "consults": st.consults(),
        "models": models_info(),
        "write_enabled": write_enabled(),
        "team": team.summary(st),
        "review_queue": team.queue(st),
        "auth_required": bool(_tokens()),
        "registry": _registry_listing(),
    }


def _registry_listing() -> list[dict[str, Any]]:
    from ..registry import list_packages

    try:
        return list_packages()["packages"]
    except OSError:
        return []


@router.get("/health")
def health() -> dict[str, Any]:
    return {"ok": True, "repos": list(registry.repos), "models": models_info()}


@router.get("/repos")
def list_repos() -> list[dict[str, Any]]:
    out = []
    for rid, root in registry.repos.items():
        st = Store(root)
        if not st.exists:
            continue
        checks = st.reports("check")
        last = checks[0] if checks else None
        out.append({
            "id": rid, "name": root.name if rid == "local" else rid, "github": github_slug(root),
            "counts": counts(st),
            "last_check": {"ok": last.get("ok"), "at": last.get("created_at"), "violations": last.get("violations", [])}
            if last else None,
        })
    return out


@router.get("/repos/{repo_id}")
def get_repo(repo_id: str) -> dict[str, Any]:
    st = registry.store(repo_id)
    return repo_snapshot(st, name=None if repo_id == "local" else repo_id, repo_id=repo_id)


@router.get("/repos/{repo_id}/decisions/{decision_id}")
def get_decision(repo_id: str, decision_id: str) -> dict[str, Any]:
    st = registry.store(repo_id)
    try:
        d = st.get(decision_id)
    except StoreError as e:
        raise HTTPException(404, str(e)) from e
    guard_src = None
    if d.guard and (st.root / d.guard.path).exists():
        guard_src = (st.root / d.guard.path).read_text()
    return {"decision": d.to_dict(), "guard_source": guard_src, "evidence": st.evidence(d.id)[-50:],
            "log": st.log(d.id)}


def _tokens() -> dict[str, str]:
    """WHY_DASHBOARD_TOKENS="maya:tok1,theo:tok2" maps bearer tokens to team members."""
    out = {}
    for pair in os.environ.get("WHY_DASHBOARD_TOKENS", "").split(","):
        who, _, tok = pair.strip().partition(":")
        if who and tok:
            out[tok] = who
    return out


def _require_write(authorization: str = "") -> str:
    """Check the dashboard may write and return the acting user."""
    import hmac

    if not write_enabled():
        raise HTTPException(403, "dashboard is read-only (WHY_DASHBOARD_WRITE=0)")
    tokens = _tokens()
    if not tokens:
        return "dashboard"
    given = authorization.removeprefix("Bearer ").strip()
    for tok, who in tokens.items():
        if given and hmac.compare_digest(given, tok):
            return who
    raise HTTPException(401, "sign in: this dashboard needs a member token for changes")


@router.get("/whoami")
def whoami(authorization: str = Header("")) -> dict[str, Any]:
    tokens = _tokens()
    given = authorization.removeprefix("Bearer ").strip()
    who = tokens.get(given) if tokens else ("dashboard" if write_enabled() else None)
    return {"user": who, "auth_required": bool(tokens), "write_enabled": write_enabled()}


@router.post("/repos/{repo_id}/decisions/{decision_id}/{action}")
def act(repo_id: str, decision_id: str, action: str, body: dict[str, Any] = Body(default={}),
        authorization: str = Header("")) -> dict[str, Any]:
    from .. import ops

    who = _require_write(authorization)
    st = registry.store(repo_id)
    did = normalize_id(decision_id)
    try:
        if action == "approve":
            r = ops.approve(st, did, actor=who)
            return {"ok": r.activated, "pending": r.pending, "message": r.message, "runs": r.runs,
                    "decision": r.decision.to_dict()}
        if action == "reject":
            return {"ok": True, "decision": ops.reject(st, did, who, body.get("note", "")).to_dict()}
        if action == "retire":
            return {"ok": True, "decision": ops.retire(st, did, who, body.get("note", "")).to_dict()}
        if action == "supersede":
            if not body.get("reason"):
                raise HTTPException(400, "reason is required")
            new = ops.supersede(st, did, body["reason"], body.get("rule"), actor=who)
            return {"ok": True, "decision": new.to_dict()}
    except team.PermissionDenied as e:
        raise HTTPException(403, str(e)) from e
    except StoreError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": str(e)}
    raise HTTPException(404, f"unknown action {action}")


@router.post("/repos/{repo_id}/check")
def run_check(repo_id: str, authorization: str = Header("")) -> dict[str, Any]:
    from ..ops import check

    _require_write(authorization)
    st = registry.store(repo_id)
    return check(st, context="dashboard").to_dict()
