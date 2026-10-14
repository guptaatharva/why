"""Guard-run cache keyed by code tree and guard content.

A guard's result is a pure function of (the code under test, the guard file, the guard support
files, the sandbox setup). When all of those are pinned by git object ids we can reuse an
earlier result instead of paying for another sandbox run: the same PR re-checked after a
comment, a merge check that repeats a branch already checked, a push of a commit that was
already checked as a PR head.

Only reproducible inputs are cached: a git ref, or a clean worktree (its HEAD tree). A dirty
worktree or a loose directory always runs. `WHY_NO_CACHE=1` or `cache: false` in config
turns it off.

Stored in `.why/cache/` (with its own .gitignore): one JSON file per key.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from .gitutil import git
from .guards import decision_id_from_path
from .store import Store


def enabled(store: Store) -> bool:
    return os.environ.get("WHY_NO_CACHE") != "1" and store.config.get("cache", True) is not False


def _dir(store: Store) -> Path:
    d = store.dir / "cache"
    if not d.is_dir():
        d.mkdir(parents=True, exist_ok=True)
        (d / ".gitignore").write_text("*\n")
    return d


def code_tree(store: Store, code_ref: str | None, patch: str | None) -> str | None:
    """A git tree id for the code under test, or None when the input is not reproducible."""
    root = store.root
    gd = store.config["guards_dir"]
    if code_ref:
        tree = git(root, "rev-parse", f"{code_ref}^{{tree}}", check=False).strip()
    else:
        dirty = git(root, "status", "--porcelain", "--untracked-files=normal", "--", ".", f":!{gd}", ":!.why",
                    check=False)
        if dirty.strip():
            return None
        tree = git(root, "rev-parse", "HEAD^{tree}", check=False).strip()
    if not tree:
        return None
    if patch and patch.strip():
        tree += ":" + hashlib.sha256(patch.encode()).hexdigest()
    return tree


def _read(store: Store, rel: str, trusted_ref: str | None) -> str:
    if trusted_ref:
        return git(store.root, "show", f"{trusted_ref}:{rel}", check=False)
    p = store.root / rel
    return p.read_text(errors="replace") if p.is_file() else ""


def support_hash(store: Store, trusted_ref: str | None) -> str:
    """Hash of guard support files (conftest, _why_support, setup files) plus sandbox config."""
    gd = store.config["guards_dir"]
    if trusted_ref:
        names = git(store.root, "ls-tree", "-r", "--name-only", trusted_ref, "--", gd, check=False).split()
    else:
        base = store.root / gd
        names = sorted(p.relative_to(store.root).as_posix() for p in base.rglob("*")
                       if p.is_file() and "__pycache__" not in p.parts) if base.is_dir() else []
    h = hashlib.sha256()
    for rel in sorted(names):
        if decision_id_from_path(rel) or Path(rel).name.startswith("_retired_"):
            continue
        h.update(rel.encode() + b"\0" + _read(store, rel, trusted_ref).encode() + b"\0")
    cfg = store.config
    h.update(json.dumps({k: cfg.get(k) for k in ("language", "python_path", "setup", "install", "sandbox")},
                        sort_keys=True, default=str).encode())
    return h.hexdigest()


def key(tree: str, guard_source: str, support: str) -> str:
    return hashlib.sha256(f"{tree}\0{support}\0{guard_source}".encode()).hexdigest()[:40]


def get(store: Store, k: str) -> dict[str, Any] | None:
    p = _dir(store) / f"{k}.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except json.JSONDecodeError:
        return None


def put(store: Store, k: str, result: dict[str, Any]) -> None:
    if result.get("result") not in ("pass", "fail"):
        return  # errors may be environmental; always retry them
    (_dir(store) / f"{k}.json").write_text(json.dumps(result))


def clear(store: Store) -> int:
    d = store.dir / "cache"
    n = 0
    for p in d.glob("*.json") if d.is_dir() else []:
        p.unlink()
        n += 1
    return n
