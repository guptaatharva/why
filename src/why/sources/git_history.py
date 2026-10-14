from __future__ import annotations

from pathlib import Path
from typing import Iterator

from ..gitutil import GitError, commit_link, git, is_repo
from . import Snippet

SEP = "\x1e"
FIELD = "\x1f"


def commits(root: Path, days: int) -> Iterator[Snippet]:
    if not is_repo(root):
        return
    try:
        out = git(
            root, "log", f"--since={days} days ago", f"--format=%H{FIELD}%an{FIELD}%aI{FIELD}%B{SEP}", "--no-merges"
        )
    except GitError:
        return
    for rec in out.split(SEP):
        rec = rec.strip("\n")
        if not rec:
            continue
        sha, author, date, body = (rec.split(FIELD) + ["", "", ""])[:4]
        body = body.strip()
        if body:
            yield Snippet("commit", sha, body, commit_link(root, sha), author, date)
