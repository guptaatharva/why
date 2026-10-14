"""The GitHub App PR flow against a local 'remote' with the GitHub API mocked."""

import json
import subprocess
import sys

import httpx
from conftest import ROOT

from why.mine import mine
from why.ops import approve
from why.server import github_app


def _git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout


def test_pull_request_check_run(store, tmp_path, monkeypatch):
    origin = store.root
    mine(store, github=False, use_model=False)
    retry = next(d for d in store.decisions() if "retry" in d.title)
    approve(store, retry.id, use_model=False)
    _git(origin, "add", "-A")
    _git(origin, "commit", "-qm", "why")
    _git(origin, "checkout", "-qb", "agent/retry")
    subprocess.run([sys.executable, str(ROOT / "demo" / "agent_retry_patch.py"), str(origin)], check=True)
    _git(origin, "commit", "-qam", "retry")
    head_sha = _git(origin, "rev-parse", "HEAD").strip()
    _git(origin, "update-ref", "refs/pull/7/head", head_sha)
    _git(origin, "checkout", "-q", "main")

    monkeypatch.setenv("WHY_WORKDIR", str(tmp_path / "server"))
    monkeypatch.setattr(github_app, "installation_token", lambda inst: "tok")

    def fake_sync(full_name, token, ref=None):
        dest = tmp_path / "server" / "repos" / full_name
        if not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            _git(dest.parent, "clone", "-q", str(origin), dest.name)
        if ref:
            _git(dest, "checkout", "-q", "--force", ref)
        github_app.registry.add(full_name, dest)
        return dest

    monkeypatch.setattr(github_app, "sync_repo", fake_sync)
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path.endswith("/pulls"):
            return httpx.Response(200, json=[])  # no other open PRs
        body = json.loads(request.content or b"{}")
        calls.append((request.method, request.url.path, body))
        return httpx.Response(201, json={"id": 99})

    monkeypatch.setattr(github_app, "gh", lambda token: httpx.Client(base_url=github_app.API,
                                                                     transport=httpx.MockTransport(handler)))
    payload = {
        "action": "opened", "installation": {"id": 1}, "repository": {"full_name": "larkspur/store"},
        "pull_request": {"number": 7, "html_url": "https://github.com/larkspur/store/pull/7", "title": "retry",
                         "head": {"sha": head_sha}, "base": {"ref": "main"}},
    }
    github_app.handle_pull_request(payload)

    (method, path, created), (_, patch_path, done) = calls
    assert (method, path) == ("POST", "/repos/larkspur/store/check-runs")
    assert created["head_sha"] == head_sha and created["status"] == "in_progress"
    assert patch_path == "/repos/larkspur/store/check-runs/99"
    assert done["conclusion"] == "failure"
    assert retry.id in done["output"]["summary"]
    assert "double-bills" in done["output"]["summary"]


def test_webhook_rejects_bad_signature(monkeypatch):
    from fastapi.testclient import TestClient

    from why.server.app import create_app

    monkeypatch.setenv("WHY_GITHUB_WEBHOOK_SECRET", "s")
    client = TestClient(create_app())
    r = client.post("/github/webhook", content=b"{}", headers={"X-GitHub-Event": "ping",
                                                                "X-Hub-Signature-256": "sha256=bad"})
    assert r.status_code == 401


def test_sibling_pr_contradiction_is_reported(store, tmp_path, monkeypatch):
    """PR #8 adds a refunds decision; open PR #9 changes the discount maths. Each passes alone."""
    origin = store.root
    _git(origin, "add", "-A")
    _git(origin, "commit", "-qm", "why store")
    _git(origin, "checkout", "-qb", "agent/refunds")
    (origin / "larkspur" / "refunds.py").write_text(
        "from larkspur.pricing import apply_discount\n\n\ndef refund_amount(total, pct):\n"
        "    return apply_discount(total, pct)\n")
    from why.models import Decision, Source

    d = store.add(Decision(id="", title="Refunds never exceed the amount charged", rule="refunds <= charged",
                           reason="the gateway rejects over-refunds", kind="returns",
                           params={"target": "larkspur.refunds:refund_amount", "call": "refund_amount(1000, 15)",
                                   "predicate": "0 <= result <= 1000"}, source=Source(type="manual")))
    assert approve(store, d.id, use_model=False).activated
    _git(origin, "add", "-A")
    _git(origin, "commit", "-qm", "refunds")
    _git(origin, "update-ref", "refs/pull/8/head", "HEAD")
    head8 = _git(origin, "rev-parse", "HEAD").strip()
    _git(origin, "checkout", "-q", "main")
    _git(origin, "checkout", "-qb", "agent/promo")
    pricing = origin / "larkspur" / "pricing.py"
    pricing.write_text(pricing.read_text().replace("total_cents - (total_cents", "total_cents + (total_cents"))
    _git(origin, "commit", "-qam", "promo")
    _git(origin, "update-ref", "refs/pull/9/head", "HEAD")
    _git(origin, "checkout", "-q", "main")

    monkeypatch.setenv("WHY_WORKDIR", str(tmp_path / "server"))
    monkeypatch.setattr(github_app, "installation_token", lambda inst: "tok")

    def fake_sync(full_name, token, ref=None):
        dest = tmp_path / "server" / "repos" / full_name
        if not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            _git(dest.parent, "clone", "-q", str(origin), dest.name)
        if ref:
            _git(dest, "checkout", "-q", "--force", ref)
        return dest

    monkeypatch.setattr(github_app, "sync_repo", fake_sync)
    patched = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json=[{"number": 9, "title": "Promo pricing", "html_url": "https://x/9"}])
        if request.method == "PATCH":
            patched.append(json.loads(request.content))
        return httpx.Response(201, json={"id": 1})

    monkeypatch.setattr(github_app, "gh", lambda token: httpx.Client(base_url=github_app.API,
                                                                     transport=httpx.MockTransport(handler)))
    github_app.handle_pull_request({
        "action": "opened", "installation": {"id": 1}, "repository": {"full_name": "larkspur/store"},
        "pull_request": {"number": 8, "html_url": "https://x/8", "title": "refunds",
                         "head": {"sha": head8}, "base": {"ref": "main"}}})
    (done,) = patched
    assert done["conclusion"] == "neutral", done["output"]
    assert "#9" in done["output"]["title"]
    assert "Conflicts with other open PRs" in done["output"]["summary"] and d.id in done["output"]["summary"]
