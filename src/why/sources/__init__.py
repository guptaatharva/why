"""Readers that turn a repo's history into text snippets the miner can scan."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


@dataclass
class Snippet:
    type: str  # commit | pr_body | pr_comment | agent_log | entire_checkpoint
    ref: str
    text: str
    link: str = ""
    author: str = ""
    date: str = ""


def collect(root: Path, cfg: dict, days: int, github: bool = True) -> Iterator[Snippet]:
    from . import agents, entire, git_history, github as gh

    yield from git_history.commits(root, days)
    yield from entire.checkpoints(root)
    yield from agents.claude_code(root, days, cfg.get("mining", {}).get("claude_project_dirs"))
    yield from agents.codex(root, days)
    yield from agents.session_dirs(root, cfg.get("mining", {}).get("session_dirs", [".why/sessions"]))
    if github:
        yield from gh.pull_requests(root, days)
