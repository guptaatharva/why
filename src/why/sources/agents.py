"""Agent session logs: Claude Code, Codex, and plain transcript folders."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Iterator

from . import Snippet


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for c in content:
            if isinstance(c, dict) and c.get("type") in ("text", "input_text", "output_text"):
                parts.append(c.get("text", ""))
        return "\n".join(parts)
    return ""


def _jsonl_messages(path: Path) -> Iterator[tuple[str, str, str]]:
    """(role, text, timestamp) for each chat message in a JSONL transcript."""
    for line in path.read_text(errors="replace").splitlines():
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(rec, dict):
            continue
        msg = rec.get("message") or rec.get("payload") or rec
        if not isinstance(msg, dict):
            continue
        role = msg.get("role") or rec.get("type") or ""
        text = _text_of(msg.get("content"))
        if role in ("user", "assistant") and text.strip():
            yield role, text, rec.get("timestamp", "")


MAX_TYPED = 1500  # longer user messages are almost always pasted docs, specs or skill files


def _typed_by_human(role: str, text: str) -> bool:
    """Decisions in agent sessions are stated by the person; agent replies mostly report status."""
    t = text.lstrip()
    return role == "user" and len(t) <= MAX_TYPED and not t.startswith("<") and "system-reminder" not in t


def _recent(path: Path, days: int) -> bool:
    return path.stat().st_mtime > time.time() - days * 86400


def _claude_slug(path: Path) -> str:
    return re.sub(r"[^A-Za-z0-9-]", "-", str(path))


def claude_code(root: Path, days: int, extra_dirs: list[str] | None = None) -> Iterator[Snippet]:
    """Claude Code sessions run in the repo, plus sessions run one folder up that mention the repo by name."""
    base = Path.home() / ".claude" / "projects"
    own = base / _claude_slug(root)
    files = sorted(own.glob("*.jsonl")) if own.is_dir() else []
    parent = base / _claude_slug(root.parent)
    if root.parent not in (Path.home(), Path.home() / "Desktop") and parent.is_dir():
        files += [f for f in sorted(parent.glob("*.jsonl")) if root.name in f.read_text(errors="replace")]
    for d in extra_dirs or []:  # mining.claude_project_dirs: folders the sessions were started in
        proj = base / _claude_slug(Path(d).expanduser().resolve())
        files += [f for f in sorted(proj.glob("*.jsonl")) if f not in files] if proj.is_dir() else []
    for f in files:
        if not _recent(f, days):
            continue
        for role, text, ts in _jsonl_messages(f):
            if _typed_by_human(role, text):
                yield Snippet("agent_log", f"claude-code:{f.stem[:8]}", text, f"file://{f}", role, ts)


def codex(root: Path, days: int) -> Iterator[Snippet]:
    base = Path.home() / ".codex" / "sessions"
    if not base.is_dir():
        return
    for f in base.rglob("*.jsonl"):
        if not _recent(f, days):
            continue
        head = f.read_text(errors="replace")[:4000]
        if str(root) not in head:
            continue
        for role, text, ts in _jsonl_messages(f):
            if _typed_by_human(role, text):
                yield Snippet("agent_log", f"codex:{f.stem[:8]}", text, f"file://{f}", role, ts)


def session_dirs(root: Path, dirs: list[str]) -> Iterator[Snippet]:
    """Transcripts dropped into the repo (e.g. .why/sessions/*.jsonl|md|txt)."""
    for d in dirs:
        base = root / d
        if not base.is_dir():
            continue
        for f in sorted(base.rglob("*")):
            if not f.is_file():
                continue
            rel = f.relative_to(root).as_posix()
            if f.suffix == ".jsonl":
                for role, text, ts in _jsonl_messages(f):
                    yield Snippet("agent_log", f"session:{f.stem}", text, rel, role, ts)
            elif f.suffix in (".md", ".txt"):
                yield Snippet("agent_log", f"session:{f.stem}", f.read_text(errors="replace"), rel)
