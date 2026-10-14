"""Entire Checkpoints (https://docs.entire.io) as an input.

Entire links commits to checkpoints with an `Entire-Checkpoint: <id>` trailer and stores
checkpoint contents either on the `entire/checkpoints/v1` branch (at `<id[:2]>/<id[2:]>/`)
or under per-checkpoint refs. The per-file schema is not documented, so this reader is
best-effort: it pulls every text-like file in a checkpoint and lets the miner scan it.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterator

from ..gitutil import GitError, git, is_repo
from . import Snippet

BRANCH = "entire/checkpoints/v1"


def _checkpoint_ids(root: Path) -> list[tuple[str, str]]:
    out = git(root, "log", "--format=%H%x1f%B%x1e", check=False)
    ids = []
    for rec in out.split("\x1e"):
        if "\x1f" not in rec:
            continue
        sha, body = rec.strip().split("\x1f", 1)
        for m in re.finditer(r"^Entire-Checkpoint:\s*([0-9a-zA-Z]+)\s*$", body, flags=re.M):
            ids.append((m.group(1), sha))
    return ids


def _tree_files(root: Path, treeish: str) -> list[str]:
    try:
        return [p for p in git(root, "ls-tree", "-r", "--name-only", treeish).splitlines() if p]
    except GitError:
        return []


def _flatten(text: str, path: str) -> str:
    if path.endswith(".json"):
        try:
            return json.dumps(json.loads(text), indent=1)[:20000]
        except json.JSONDecodeError:
            return text
    return text[:20000]


def checkpoints(root: Path) -> Iterator[Snippet]:
    if not is_repo(root):
        return
    ids = _checkpoint_ids(root)
    if not ids:
        return
    has_branch = bool(git(root, "rev-parse", "--verify", "--quiet", BRANCH, check=False).strip())
    refs = git(root, "for-each-ref", "--format=%(refname)", check=False).splitlines()
    for cid, sha in ids:
        files: list[tuple[str, str]] = []  # (treeish, path)
        if has_branch:
            prefix = f"{cid[:2]}/{cid[2:]}/"
            files += [(BRANCH, p) for p in _tree_files(root, BRANCH) if p.startswith(prefix)]
        for ref in refs:
            if "entire" in ref and cid in ref:
                files += [(ref, p) for p in _tree_files(root, ref)]
        for treeish, path in files:
            if not re.search(r"\.(jsonl?|md|txt)$", path):
                continue
            text = git(root, "show", f"{treeish}:{path}", check=False)
            if path.endswith(".jsonl"):
                from .agents import _text_of

                lines = []
                for line in text.splitlines():
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    msg = rec.get("message", rec) if isinstance(rec, dict) else {}
                    t = _text_of(msg.get("content")) if isinstance(msg, dict) else ""
                    if t:
                        lines.append(t)
                text = "\n\n".join(lines)
            else:
                text = _flatten(text, path)
            if text.strip():
                yield Snippet("entire_checkpoint", cid, text, f"commit:{sha}#entire:{cid}")
