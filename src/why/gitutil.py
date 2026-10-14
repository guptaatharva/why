"""Thin wrappers around the git CLI."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path


class GitError(RuntimeError):
    pass


def git(root: Path, *args: str, check: bool = True) -> str:
    r = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise GitError(f"git {' '.join(args)}: {r.stderr.strip()}")
    return r.stdout


def is_repo(root: Path) -> bool:
    return subprocess.run(["git", "rev-parse"], cwd=root, capture_output=True).returncode == 0


def head(root: Path) -> str:
    try:
        return git(root, "rev-parse", "HEAD").strip()
    except GitError:
        return ""


def short(sha: str) -> str:
    return sha[:8]


def remote_url(root: Path) -> str:
    return git(root, "config", "--get", "remote.origin.url", check=False).strip()


def github_slug(root: Path) -> str | None:
    """owner/name if origin is a GitHub remote."""
    m = re.search(r"github\.com[:/]([^/]+)/([^/.]+?)(?:\.git)?$", remote_url(root))
    return f"{m.group(1)}/{m.group(2)}" if m else None


def commit_link(root: Path, sha: str) -> str:
    slug = github_slug(root)
    return f"https://github.com/{slug}/commit/{sha}" if slug else f"commit:{sha}"


def export_tree(root: Path, ref: str, dest: Path) -> None:
    """Write the tree at `ref` into `dest` (no .git)."""
    dest.mkdir(parents=True, exist_ok=True)
    archive = subprocess.run(["git", "archive", "--format=tar", ref], cwd=root, capture_output=True)
    if archive.returncode != 0:
        raise GitError(archive.stderr.decode(errors="replace"))
    subprocess.run(["tar", "-x", "-C", str(dest)], input=archive.stdout, check=True)
