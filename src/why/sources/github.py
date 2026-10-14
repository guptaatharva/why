"""PR bodies and comments from the GitHub REST API (token from GITHUB_TOKEN or `gh auth token`)."""

from __future__ import annotations

import os
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

import httpx

from ..gitutil import github_slug
from . import Snippet


def token() -> str | None:
    t = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if t:
        return t
    try:
        r = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=5)
        return r.stdout.strip() or None
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None


def pull_requests(root: Path, days: int, max_prs: int = 100) -> Iterator[Snippet]:
    slug = github_slug(root)
    tok = token()
    if not slug or not tok:
        return
    since = datetime.now(timezone.utc) - timedelta(days=days)
    headers = {"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json"}
    try:
        with httpx.Client(base_url="https://api.github.com", headers=headers, timeout=30) as c:
            prs = c.get(f"/repos/{slug}/pulls", params={"state": "all", "per_page": max_prs, "sort": "updated",
                                                        "direction": "desc"}).json()
            if not isinstance(prs, list):
                return
            for pr in prs:
                updated = datetime.fromisoformat(pr["updated_at"].replace("Z", "+00:00"))
                if updated < since:
                    break
                n = pr["number"]
                if pr.get("body"):
                    yield Snippet("pr_body", f"#{n}", f"{pr['title']}\n\n{pr['body']}", pr["html_url"],
                                  pr["user"]["login"], pr["created_at"])
                for url in (f"/repos/{slug}/issues/{n}/comments", f"/repos/{slug}/pulls/{n}/comments"):
                    for cm in c.get(url, params={"per_page": 100}).json() or []:
                        if isinstance(cm, dict) and cm.get("body"):
                            yield Snippet("pr_comment", f"#{n}", cm["body"], cm["html_url"],
                                          cm["user"]["login"], cm["created_at"])
    except httpx.HTTPError:
        return
