"""Teams: roles, decision owners, required approvals and the review queue.

Off by default. A repo opts in with a `team:` block in `.why/config.yaml`:

    team:
      members:
        maya:  {role: admin,    aliases: [maya@larkspur.example, "Maya Okafor"]}
        theo:  {role: reviewer}
        ines:  {role: viewer}
      owners:                         # CODEOWNERS-style: path glob -> members
        "larkspur/payments/**": [maya]
      required_approvals: 2           # distinct approvers before a decision can go active

Roles, lowest to highest:
    viewer    read only (the default for anyone not listed)
    reviewer  approve / reject / edit proposed decisions
    admin     everything, including retire and supersede of active decisions

A decision whose scope touches an owned path also needs one approval from an owner of that path.
Owners may retire or supersede decisions on their paths even if they are only reviewers.

Without a `team:` block every actor is an admin and one approval activates (solo mode).
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from typing import Any

from .models import Decision, now
from .store import Store, StoreError

ROLES = ("viewer", "reviewer", "admin")
NEEDS = {"approve": "reviewer", "reject": "reviewer", "edit": "reviewer", "add": "reviewer",
         "retire": "admin", "supersede": "admin", "guard": "reviewer"}
SYSTEM_ACTORS = {"why", "why check", "github-app"}


class PermissionDenied(StoreError):
    pass


def config(store: Store) -> dict[str, Any] | None:
    t = store.config.get("team")
    return t if isinstance(t, dict) and t.get("members") else None


def resolve(store: Store, actor: str) -> str | None:
    """Member handle for an actor name, email or alias; None if not a member."""
    t = config(store)
    if not t:
        return actor
    a = actor.strip().lower()
    for handle, info in t["members"].items():
        names = [handle, *((info or {}).get("aliases") or [])]
        if a in (n.lower() for n in names):
            return handle
    return None


def role(store: Store, actor: str) -> str:
    t = config(store)
    if not t or actor in SYSTEM_ACTORS:
        return "admin"
    h = resolve(store, actor)
    if h is None:
        return "viewer"
    r = (t["members"].get(h) or {}).get("role", "reviewer")
    return r if r in ROLES else "viewer"


def _matches(path: str, pattern: str) -> bool:
    pattern = pattern.lstrip("/")
    if pattern.endswith("/**"):
        return path == pattern[:-3] or path.startswith(pattern[:-2])
    return fnmatch.fnmatch(path, pattern) or path.startswith(pattern.rstrip("/") + "/")


def owners_of(store: Store, d: Decision) -> list[str]:
    t = config(store)
    if not t:
        return []
    files = list(d.scope.get("files") or [])
    for s in d.scope.get("symbols") or []:  # "pkg.mod:func" -> pkg/mod
        mod = s.split(":")[0]
        files.append(mod.replace(".", "/"))
    out: list[str] = []
    for pattern, people in (t.get("owners") or {}).items():
        if any(_matches(f, pattern) for f in files):
            out += [p for p in people if p not in out]
    return out


def required(store: Store) -> int:
    t = config(store)
    return max(1, int(t.get("required_approvals", 1))) if t else 1


def require(store: Store, actor: str, action: str, d: Decision | None = None) -> None:
    need = NEEDS.get(action, "admin")
    have = role(store, actor)
    if ROLES.index(have) >= ROLES.index(need):
        return
    if d is not None and action in ("retire", "supersede") and resolve(store, actor) in owners_of(store, d) \
            and have != "viewer":
        return
    who = resolve(store, actor) or actor
    raise PermissionDenied(f"{who} is a {have}; `{action}` needs {need}"
                           + (" or ownership of the decision's code" if action in ("retire", "supersede") else ""))


@dataclass
class ApprovalState:
    approvals: list[str]
    needed: int
    owners: list[str]
    owner_ok: bool
    satisfied: bool
    missing: list[str] = field(default_factory=list)


def approval_state(store: Store, d: Decision) -> ApprovalState:
    by = []
    for a in d.approvals:
        h = resolve(store, a["by"]) or a["by"]
        if h not in by:
            by.append(h)
    needed = required(store)
    owners = owners_of(store, d)
    owner_ok = not owners or any(b in owners for b in by)
    missing = []
    if len(by) < needed:
        missing.append(f"{needed - len(by)} more approval(s)")
    if not owner_ok:
        missing.append(f"an owner ({', '.join(owners)})")
    return ApprovalState(by, needed, owners, owner_ok, len(by) >= needed and owner_ok, missing)


def record_approval(store: Store, d: Decision, actor: str) -> ApprovalState:
    """Add `actor`'s approval (once) and return whether the decision may now activate."""
    require(store, actor, "approve", d)
    h = resolve(store, actor) or actor
    if not any((resolve(store, a["by"]) or a["by"]) == h for a in d.approvals):
        d.approvals.append({"by": h, "at": now()})
        store.save(d)
    return approval_state(store, d)


def reviewers(store: Store) -> list[str]:
    t = config(store)
    if not t:
        return []
    return [h for h, i in t["members"].items() if (i or {}).get("role", "reviewer") in ("reviewer", "admin")]


def queue(store: Store, actor: str | None = None) -> list[dict[str, Any]]:
    """Proposed decisions awaiting review, strongest evidence first, with who should look at them."""
    me = resolve(store, actor) if actor else None
    rows = []
    for d in store.decisions("proposed"):
        st = approval_state(store, d)
        owners = st.owners
        assigned = owners if owners and not st.owner_ok else [r for r in (owners or reviewers(store))
                                                              if r not in st.approvals]
        if me and me not in assigned:
            continue
        rows.append({"id": d.id, "title": d.title, "rule": d.rule, "confidence": d.confidence,
                     "source": d.source.type, "source_ref": d.source.ref, "created_at": d.created_at,
                     "approvals": st.approvals, "needed": st.needed, "owners": owners, "assigned": assigned,
                     "waiting_on": st.missing})
    rows.sort(key=lambda r: (-r["confidence"], r["created_at"]))
    return rows


def summary(store: Store) -> dict[str, Any] | None:
    t = config(store)
    if not t:
        return None
    return {"members": {h: (i or {}).get("role", "reviewer") for h, i in t["members"].items()},
            "owners": t.get("owners") or {}, "required_approvals": required(store)}
