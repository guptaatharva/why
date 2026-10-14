"""The Why GitHub App (F3): run every active guard on every PR and report a check run.

Environment:
    WHY_GITHUB_APP_ID           numeric app id
    WHY_GITHUB_PRIVATE_KEY      PEM contents, or a path to the .pem file
    WHY_GITHUB_WEBHOOK_SECRET   webhook secret
    WHY_WORKDIR                 where repos are cloned (default ~/.why-server)

App permissions: Checks (write), Contents (write, for the guards PR), Pull requests (write),
Metadata (read). Events: pull_request, push, installation, installation_repositories.

Security: guards and decisions always come from the PR's *base* branch. The PR's own
copy of `.why/` and the guard directory are ignored, so a PR cannot disable the guard
that would catch it.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

import httpx
import jwt
from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request

from ..ops import CheckReport, check
from ..store import Store
from .api import registry

log = logging.getLogger("why.github")
router = APIRouter()
API = "https://api.github.com"
_locks: dict[str, threading.Lock] = {}


def workdir() -> Path:
    return Path(os.environ.get("WHY_WORKDIR", Path.home() / ".why-server")).expanduser()


def configured() -> bool:
    return bool(os.environ.get("WHY_GITHUB_APP_ID") and os.environ.get("WHY_GITHUB_PRIVATE_KEY"))


def _private_key() -> str:
    raw = os.environ["WHY_GITHUB_PRIVATE_KEY"]
    return Path(raw).expanduser().read_text() if not raw.lstrip().startswith("-----") else raw


def app_jwt() -> str:
    t = int(time.time())
    return jwt.encode({"iat": t - 60, "exp": t + 540, "iss": os.environ["WHY_GITHUB_APP_ID"]}, _private_key(),
                      algorithm="RS256")


def installation_token(installation_id: int) -> str:
    r = httpx.post(f"{API}/app/installations/{installation_id}/access_tokens",
                   headers={"Authorization": f"Bearer {app_jwt()}", "Accept": "application/vnd.github+json"},
                   timeout=30)
    r.raise_for_status()
    return r.json()["token"]


def gh(token: str) -> httpx.Client:
    return httpx.Client(base_url=API, timeout=30, headers={"Authorization": f"Bearer {token}",
                                                           "Accept": "application/vnd.github+json"})


def verify_signature(secret: str, body: bytes, signature: str | None) -> bool:
    if not signature or not signature.startswith("sha256="):
        return False
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(digest, signature.removeprefix("sha256="))


def _git(cwd: Path, *args: str) -> str:
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"git {args[0]}: {r.stderr.strip()[:500]}")
    return r.stdout


def sync_repo(full_name: str, token: str, ref: str | None = None) -> Path:
    """Clone or fetch `owner/name` into the workdir and check out `ref` (default branch if None)."""
    dest = workdir() / "repos" / full_name
    url = f"https://x-access-token:{token}@github.com/{full_name}.git"
    if not (dest / ".git").exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        _git(dest.parent, "clone", "--quiet", url, dest.name)
    else:
        _git(dest, "remote", "set-url", "origin", url)
        _git(dest, "fetch", "--quiet", "--prune", "origin", "+refs/heads/*:refs/remotes/origin/*")
    if ref:
        _git(dest, "checkout", "--quiet", "--force", ref)
    registry.add(full_name, dest)
    return dest


def _lock(name: str) -> threading.Lock:
    return _locks.setdefault(name, threading.Lock())


def render_summary(report: CheckReport, store: Store) -> tuple[str, str]:
    if not report.items:
        return "No active decisions yet", "Approve decisions in the Why dashboard or with `why approve` to start guarding them."
    if report.ok:
        return f"All {len(report.items)} decisions hold", "\n".join(
            f"- ✅ **{i.decision_id}** {i.title}" for i in report.items)
    lines = [f"Why found **{len(report.violations)}** decision(s) this PR breaks. Tests may still be green: these "
             "are rules the code must keep, mined from this repo's own history.\n"]
    for i in report.violations:
        d = store.get(i.decision_id)
        lines += [
            f"### ❌ {i.decision_id}: {i.title}",
            f"**Rule:** {d.rule}  ",
            f"**Why:** {d.reason or '—'}  ",
            f"**Decided in:** [{d.source.type} {d.source.ref}]({d.source.link})" if d.source.link.startswith("http")
            else f"**Decided in:** {d.source.type} {d.source.ref}",
            f"> {d.source.quote}" if d.source.quote else "",
            "<details><summary>Guard output</summary>\n\n```\n" + i.log[-1500:] + "\n```\n</details>",
            f"Fix the code, or supersede the decision with a new reason: `why supersede {i.decision_id} --reason \"…\"`\n",
        ]
    passed = [i for i in report.items if i.result == "pass"]
    if passed:
        lines.append(f"<details><summary>{len(passed)} decision(s) hold</summary>\n\n" +
                     "\n".join(f"- {i.decision_id} {i.title}" for i in passed) + "\n</details>")
    return f"{len(report.violations)} decision(s) violated", "\n".join(x for x in lines if x is not None)


MAX_SIBLING_PRS = 5


def sibling_conflicts(c: httpx.Client, store: Store, repo: str, pr: dict[str, Any]) -> list[dict[str, Any]]:
    """F7 on GitHub: merge this PR with each other open PR on the same base and run every guard.

    Parallel agent branches can each pass alone and still contradict each other once both land.
    """
    from ..merge import merge_check

    resp = c.get(f"/repos/{repo}/pulls", params={"state": "open", "base": pr["base"]["ref"], "per_page": 30})
    others = [o for o in (resp.json() if isinstance(resp.json(), list) else [])
              if isinstance(o, dict) and o.get("number") != pr["number"]][:MAX_SIBLING_PRS]
    if not others:
        return []
    root = store.root
    mine = f"refs/why/pr/{pr['number']}"
    _git(root, "fetch", "--quiet", "origin", f"+pull/{pr['number']}/head:{mine}")
    found = []
    for o in others:
        ref = f"refs/why/pr/{o['number']}"
        try:
            _git(root, "fetch", "--quiet", "origin", f"+pull/{o['number']}/head:{ref}")
        except RuntimeError:
            continue
        rep = merge_check(store, [mine, ref], base=f"origin/{pr['base']['ref']}", context="pr-merge")
        if rep.get("contradictions"):
            found.append({"pr": o["number"], "title": o.get("title", ""), "url": o.get("html_url", ""),
                          "contradictions": rep["contradictions"]})
    return found


def render_siblings(found: list[dict[str, Any]]) -> str:
    lines = ["\n## Conflicts with other open PRs",
             "Each PR passes alone, but merged together they break these decisions:\n"]
    for f in found:
        for x in f["contradictions"]:
            lines.append(f"- **{x['decision_id']}** {x['title']}: with [#{f['pr']} {f['title']}]({f['url']}). "
                         f"Why: {x['reason'] or '—'}")
    return "\n".join(lines)


def render_upgrades(results: list[tuple[Any, Any]]) -> str:
    lines = ["\n## Dependency upgrades in this PR",
             "Why installed each new version in a sandbox and ran every guard (plus any guarantees the library "
             "published) before and after.\n"]
    for b, rep in results:
        head = f"**{b.package}** {rep.from_version or b.before} → {rep.to_version or b.after}"
        if rep.error:
            lines.append(f"- ⚠️ {head}: could not run ({rep.error[-300:]})")
        elif rep.verdict == "blocked":
            lines.append(f"- ❌ {head}: breaks {len(rep.breaks)} decision(s)")
            for br in rep.breaks:
                rc = br.root_cause or {}
                lines.append(f"  - **{br.decision_id}** {br.title}" + (f". Root cause: `{rc['api']}`" if rc.get("api") else ""))
                if "before" in rc:
                    lines.append(f"    - before: `{str(rc['before'])[:200]}`  \n      after: `{str(rc['after'])[:200]}`")
                if br.explanation:
                    lines.append(f"    - {br.explanation[:600]}")
        else:
            lines.append(f"- ✅ {head}: all {rep.guards_run} guard(s) still hold")
    return "\n".join(lines)


def upgrade_checks(store: Store, base_ref: str, head_sha: str) -> list[tuple[Any, Any]]:
    """F5 on GitHub: a PR that bumps dependencies (Renovate, Dependabot, an agent) gets an upgrade check."""
    from ..upgrade import check_bumps

    return check_bumps(store, f"origin/{base_ref}", head_sha)


def handle_pull_request(payload: dict[str, Any]) -> None:
    pr = payload["pull_request"]
    repo = payload["repository"]["full_name"]
    inst = payload["installation"]["id"]
    head_sha, base_ref = pr["head"]["sha"], pr["base"]["ref"]
    token = installation_token(inst)
    with gh(token) as c:
        run = c.post(f"/repos/{repo}/check-runs", json={
            "name": "Why", "head_sha": head_sha, "status": "in_progress",
            "output": {"title": "Checking decisions…", "summary": "Running every active guard in a sandbox."}}).json()
        try:
            with _lock(repo):
                root = sync_repo(repo, token, f"origin/{base_ref}")
                _git(root, "fetch", "--quiet", "origin", f"pull/{pr['number']}/head")
                store = Store(root)
                if not store.exists:
                    conclusion, title, summary = "neutral", "Why is not set up", "No `.why/` store on the base branch."
                else:
                    report = check(store, context="pr", code_ref=head_sha, trusted_ref=f"origin/{base_ref}",
                                   commit=head_sha, apply_transitions=False,
                                   extra={"pr": pr["number"], "pr_url": pr["html_url"], "head_sha": head_sha,
                                          "pr_title": pr["title"]})
                    title, summary = render_summary(report, store)
                    conclusion = "success" if report.ok else "failure"
                    upgrades = upgrade_checks(store, base_ref, head_sha)
                    if upgrades:
                        summary += render_upgrades(upgrades)
                        blocked = [b for b, r in upgrades if r.verdict == "blocked"]
                        if blocked:
                            conclusion = "failure"
                            title = (f"Upgrade blocked: {', '.join(f'{b.package} {b.after}' for b in blocked)}"
                                     + ("" if report.ok else f"; {len(report.violations)} decision(s) violated"))
                        elif report.ok:
                            title += f"; {len(upgrades)} dependency upgrade(s) checked"
                    siblings = sibling_conflicts(c, store, repo, pr)
                    if siblings:
                        summary += render_siblings(siblings)
                        if report.ok:
                            n = sum(len(f["contradictions"]) for f in siblings)
                            conclusion = "neutral"
                            title = f"Passes alone; {n} decision(s) break when merged with #{siblings[0]['pr']}"
        except Exception as e:  # noqa: BLE001
            log.exception("check failed")
            conclusion, title, summary = "neutral", "Why could not run", f"```\n{str(e)[-1500:]}\n```"
        c.patch(f"/repos/{repo}/check-runs/{run['id']}", json={
            "status": "completed", "conclusion": conclusion, "output": {"title": title, "summary": summary[:65000]}})


def handle_push(payload: dict[str, Any]) -> None:
    """On the default branch, keep decision states honest (active ↔ violated)."""
    repo = payload["repository"]
    if payload.get("ref") != f"refs/heads/{repo['default_branch']}":
        return
    token = installation_token(payload["installation"]["id"])
    with _lock(repo["full_name"]):
        root = sync_repo(repo["full_name"], token, f"origin/{repo['default_branch']}")
        st = Store(root)
        if st.exists:
            # Transitions are written into the server's clone; they are surfaced in the dashboard.
            check(st, context="push", commit=payload.get("after"))


def handle_installation(payload: dict[str, Any]) -> None:
    token = installation_token(payload["installation"]["id"])
    repos = payload.get("repositories") or payload.get("repositories_added") or []
    for r in repos:
        try:
            sync_repo(r["full_name"], token)
        except Exception:  # noqa: BLE001
            log.exception("clone failed for %s", r.get("full_name"))


@router.post("/github/webhook")
async def webhook(request: Request, background: BackgroundTasks,
                  x_github_event: str = Header(""), x_hub_signature_256: str | None = Header(None)) -> dict[str, Any]:
    body = await request.body()
    secret = os.environ.get("WHY_GITHUB_WEBHOOK_SECRET", "")
    if not secret or not verify_signature(secret, body, x_hub_signature_256):
        raise HTTPException(401, "bad signature")
    payload = await request.json()
    action = payload.get("action")
    if x_github_event == "pull_request" and action in ("opened", "synchronize", "reopened", "ready_for_review"):
        background.add_task(handle_pull_request, payload)
    elif x_github_event == "push":
        background.add_task(handle_push, payload)
    elif x_github_event in ("installation", "installation_repositories") and action in ("created", "added"):
        background.add_task(handle_installation, payload)
    return {"ok": True, "event": x_github_event, "action": action}


def open_guards_pr(full_name: str, installation_id: int) -> str:
    """Onboarding step 5: commit approved decisions and their guards on a branch and open one PR."""
    token = installation_token(installation_id)
    with _lock(full_name):
        root = sync_repo(full_name, token)
        st = Store(root)
        branch = f"why/guards-{int(time.time())}"
        _git(root, "checkout", "-q", "-B", branch)
        _git(root, "add", ".why", st.config["guards_dir"])
        _git(root, "-c", "user.name=why[bot]", "-c", "user.email=why-bot@users.noreply.github.com",
             "commit", "-q", "-m", "Add Why decisions and guards")
        _git(root, "push", "-q", "origin", branch)
        active = st.decisions("active")
        body = "Why mined these decisions from this repo's history; each now has a guard test.\n\n" + "\n".join(
            f"- **{d.id}** {d.title}: {d.reason}" for d in active)
        with gh(token) as c:
            default = c.get(f"/repos/{full_name}").json()["default_branch"]
            pr = c.post(f"/repos/{full_name}/pulls", json={
                "title": f"Why: guard {len(active)} decisions", "head": branch, "base": default, "body": body}).json()
        return pr.get("html_url", "")


def load_existing() -> None:
    """Register repos cloned by a previous server run."""
    base = workdir() / "repos"
    if not base.is_dir():
        return
    for owner in base.iterdir():
        for repo in owner.iterdir() if owner.is_dir() else []:
            if (repo / ".why").is_dir():
                registry.add(f"{owner.name}/{repo.name}", repo)
