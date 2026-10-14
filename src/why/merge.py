"""Decision merge (F7): catch contradictions between parallel agent branches that git cannot see.

Two branches can merge cleanly line-wise and still break each other's decisions: agent A records
"refunds never exceed the order total" with a guard, while agent B changes the discount maths that
guard depends on. Each branch is green alone. `why merge` builds the merged tree (base + every
branch) in a sandbox and runs the union of all their guards on it.

Trust: decisions that exist on the base run with the base's guard, so a branch cannot weaken them.
A branch's *new* decisions run with that branch's guard, which is the point: they are what the
other branch might contradict.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .gitutil import GitError, git
from .guards import decision_id_from_path
from .models import Decision, Evidence, now
from .runner import Sandbox
from .store import Store


@dataclass
class Sourced:
    decision: Decision
    ref: str  # where its guard comes from
    origin: str  # "base" or the branch that introduced it


def _decisions_at(store: Store, ref: str) -> dict[str, Decision]:
    out = {}
    names = git(store.root, "ls-tree", "--name-only", f"{ref}:.why/decisions", check=False).split()
    for n in names:
        raw = git(store.root, "show", f"{ref}:.why/decisions/{n}", check=False)
        if raw:
            d = Decision.from_dict(yaml.safe_load(raw))
            out[d.id] = d
    return out


def _collect(store: Store, base: str, branches: list[str]) -> tuple[list[Sourced], list[dict[str, Any]]]:
    base_ds = _decisions_at(store, base)
    out = [Sourced(d, base, "base") for d in base_ds.values()]
    clashes = []
    seen_new: dict[str, Sourced] = {}
    for br in branches:
        for did, d in _decisions_at(store, br).items():
            if did in base_ds and base_ds[did].rule == d.rule:
                continue  # inherited from base: judged by the base's guard
            if did in base_ds or did in seen_new:
                other = base_ds.get(did) or seen_new[did].decision
                clashes.append({"decision_id": did, "branch": br, "rule": d.rule, "other_rule": other.rule})
            s = Sourced(d, br, br)
            seen_new.setdefault(did, s)
            out.append(s)
    return [s for s in out if s.decision.status in ("active", "violated") and s.decision.guard], clashes


def merge_check(store: Store, others: str | list[str], base: str = "HEAD", record: bool = True,
                context: str = "merge") -> dict[str, Any]:
    branches = [others] if isinstance(others, str) else list(others)
    root = store.root
    label = "+".join(branches)
    try:
        guarded, clashes = _collect(store, base, branches)
    except GitError as e:
        return {"ok": False, "error": str(e), "base": base, "other": label, "created_at": now()}

    wt = Path(tempfile.mkdtemp(prefix="why-merge-"))
    try:
        git(root, "worktree", "add", "--detach", str(wt), base)
        r = subprocess.run(["git", "-c", "user.name=why", "-c", "user.email=why@localhost", "merge", "--no-commit",
                            "--no-ff", *branches], cwd=wt, capture_output=True, text=True)
        unmerged = git(wt, "diff", "--name-only", "--diff-filter=U", check=False).split() if r.returncode else []
        if r.returncode != 0 and (unmerged or "CONFLICT" in r.stdout + r.stderr or "conflict" in r.stdout + r.stderr):
            # Two branches that each minted the same decision id conflict on .why/decisions/D-xxxx.yaml.
            return {"ok": False, "text_conflict": True, "detail": (r.stdout + r.stderr)[-2000:], "base": base,
                    "other": label, "branches": branches, "id_clashes": clashes, "contradictions": [],
                    "conflicted_files": unmerged, "created_at": now()}
        if r.returncode != 0:
            return {"ok": False, "error": (r.stdout + r.stderr)[-2000:], "base": base, "other": label,
                    "created_at": now()}
        gd = store.config["guards_dir"]
        shutil.rmtree(wt / gd, ignore_errors=True)
        # Each guard from the ref its decision trusts; support files from the base.
        for rel in git(root, "ls-tree", "-r", "--name-only", base, "--", gd, check=False).split():
            if not decision_id_from_path(rel):
                (wt / rel).parent.mkdir(parents=True, exist_ok=True)
                (wt / rel).write_text(git(root, "show", f"{base}:{rel}"))
        paths: dict[str, Sourced] = {}
        for s in guarded:
            assert s.decision.guard
            content = git(root, "show", f"{s.ref}:{s.decision.guard.path}", check=False)
            if not content:
                continue
            path = s.decision.guard.path
            if path in paths:  # two branches wrote the same guard filename
                p = Path(path)
                path = str(p.with_name(f"{p.stem.split('.')[0]}_{s.origin.replace('/', '_')}{''.join(p.suffixes)}"))
            (wt / path).parent.mkdir(parents=True, exist_ok=True)
            (wt / path).write_text(content)
            paths[path] = s
        with Sandbox(store, code_dir=wt) as sb:
            assert sb.dir
            shutil.rmtree(sb.dir / gd, ignore_errors=True)
            shutil.copytree(wt / gd, sb.dir / gd)
            outcome = sb.run(list(paths))
    except GitError as e:
        return {"ok": False, "error": str(e), "base": base, "other": label, "created_at": now()}
    finally:
        subprocess.run(["git", "worktree", "remove", "--force", str(wt)], cwd=root, capture_output=True)
        shutil.rmtree(wt, ignore_errors=True)

    rid = uuid.uuid4().hex[:12]
    failed: dict[str, str] = {}
    for res in outcome.results:
        test_file = res.test.split("::")[0]
        match = next((p for p in paths if test_file.endswith(p) or p.endswith(test_file)), None)
        if match and res.result in ("fail", "error"):
            failed[match] = res.log
    contradictions = []
    for path, s in paths.items():
        if path not in failed:
            continue
        d = s.decision
        # Which other branch's code breaks it: a base decision broken only by the merge, or a branch decision
        # broken by the other branches.
        against = [b for b in branches if b != s.origin]
        contradictions.append({"decision_id": d.id, "title": d.title, "reason": d.reason, "rule": d.rule,
                               "from": s.origin, "broken_by": against or ["merge"], "guard": path,
                               "log": failed[path][-2000:]})
        if record:
            store.add_evidence(Evidence(d.id, "fail", context, f"{base}+{label}", rid, failed[path][-3000:],
                                        detail={"from": s.origin, "branches": branches}))
    report = {"id": rid, "ok": not contradictions and not clashes, "base": base, "other": label,
              "branches": branches, "guards_run": len(paths), "contradictions": contradictions,
              "id_clashes": clashes, "created_at": now()}
    if record:
        store.save_report("merge", f"{label.replace('/', '_')}-{rid}", report)
    return report
