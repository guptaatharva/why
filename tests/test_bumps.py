"""Dependency bumps are found from lockfiles / manifests and checked like `why upgrade` (CLI and GitHub App)."""

import json
import subprocess

import httpx
import yaml
from conftest import DIST

from why import bumps
from why.mine import mine
from why.ops import approve
from why.server import github_app
from why.upgrade import check_bumps


def _git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout


def test_parsers():
    assert bumps._requirements("Chronokit==1.4.0  # pinned\nhttpx>=0.27\nfoo[bar]==2.0\n") == \
        {"chronokit": "1.4.0", "foo": "2.0"}
    lock = '[[package]]\nname = "chronokit"\nversion = "2.0.0"\n\n[[package]]\nname = "app"\nversion = "0.1.0"\n' \
           'source = { editable = "." }\n'
    assert bumps._toml_lock(lock) == {"chronokit": "2.0.0"}
    assert bumps._package_json(json.dumps({"dependencies": {"left-pad": "^1.3.0", "x": "latest"},
                                           "devDependencies": {"vitest": "~2.1.8"}})) == \
        {"left-pad": "1.3.0", "vitest": "2.1.8"}
    pl = {"packages": {"": {}, "node_modules/@acme/dates": {"version": "2.0.0"},
                       "node_modules/a/node_modules/b": {"version": "9.9.9"}}}
    assert bumps._package_lock(json.dumps(pl)) == {"@acme/dates": "2.0.0"}
    pnpm = yaml.safe_dump({"lockfileVersion": "9.0", "importers": {".": {"dependencies": {
        "chronokit": {"specifier": "^1.4.0", "version": "1.4.0"},
        "vite": {"specifier": "^5", "version": "5.4.11(@types/node@22.0.0)"}}}}})
    assert bumps._pnpm_lock(pnpm) == {"chronokit": "1.4.0", "vite": "5.4.11"}


def _renovate_branch(store, version="2.0.0"):
    root = store.root
    mine(store, github=False, use_model=False)
    utc = next(d for d in store.decisions() if "UTC" in d.title)
    assert approve(store, utc.id, use_model=False).activated
    cfg = yaml.safe_load((store.dir / "config.yaml").read_text())
    cfg["find_links"] = str(DIST)
    (store.dir / "config.yaml").write_text(yaml.safe_dump(cfg))
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "why")
    _git(root, "checkout", "-qb", "renovate/chronokit-2.x")
    (root / "requirements.txt").write_text(f"chronokit=={version}\n")
    _git(root, "commit", "-qam", f"Update dependency chronokit to v{version}")
    head = _git(root, "rev-parse", "HEAD").strip()
    _git(root, "checkout", "-q", "main")
    return utc, head


def test_check_bumps_blocks_renovate_branch(store):
    utc, head = _renovate_branch(store)
    found = bumps.find(store.root, "main", head, "pypi")
    assert [(b.package, b.before, b.after) for b in found] == [("chronokit", "1.4.0", "2.0.0")]
    [(b, rep)] = check_bumps(store, "main", head)
    assert rep.verdict == "blocked", rep.error
    assert rep.from_version == "1.4.0" and rep.to_version == "2.0.0"
    assert rep.breaks[0].decision_id == utc.id
    assert rep.breaks[0].root_cause["api"] == "chronokit.parse"


def test_github_app_blocks_dependency_pr(store, tmp_path, monkeypatch):
    utc, head = _renovate_branch(store)
    origin = store.root
    _git(origin, "update-ref", "refs/pull/11/head", head)
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
            return httpx.Response(200, json=[])
        calls.append((request.method, json.loads(request.content or b"{}")))
        return httpx.Response(201, json={"id": 5})

    monkeypatch.setattr(github_app, "gh", lambda token: httpx.Client(base_url=github_app.API,
                                                                     transport=httpx.MockTransport(handler)))
    github_app.handle_pull_request({
        "action": "opened", "installation": {"id": 1}, "repository": {"full_name": "larkspur/store"},
        "pull_request": {"number": 11, "html_url": "https://github.com/larkspur/store/pull/11",
                         "title": "Update dependency chronokit to v2", "head": {"sha": head},
                         "base": {"ref": "main"}}})
    done = calls[-1][1]
    assert done["conclusion"] == "failure", done["output"]
    assert done["output"]["title"].startswith("Upgrade blocked: chronokit 2.0.0")
    summary = done["output"]["summary"]
    assert "chronokit" in summary and "1.4.0 → 2.0.0" in summary and utc.id in summary
    assert "chronokit.parse" in summary
