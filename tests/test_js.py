"""The TypeScript/Vitest path: code index, guard templates, PR checks and npm upgrade tracing.

Builds demo/build_demo_js.py (needs pnpm or a working npm, and the npm registry or a warm cache).
"""

import os
import shutil
import subprocess
import sys

import pytest
from conftest import ROOT

from why.codeindex import CodeIndex
from why.mine import mine
from why.ops import approve, check
from why.store import Store
from why.upgrade import upgrade

pytestmark = pytest.mark.skipif(not (shutil.which("pnpm") or shutil.which("npm")), reason="needs a node package manager")


@pytest.fixture(scope="module")
def js_repo(tmp_path_factory):
    dest = tmp_path_factory.mktemp("js") / "larkspur-web"
    r = subprocess.run([sys.executable, str(ROOT / "demo" / "build_demo_js.py"), str(dest)], capture_output=True)
    if r.returncode != 0:
        pytest.skip(f"could not install the JS demo: {r.stderr.decode()[-300:]}")
    st = Store(dest)
    st.init(language="javascript")
    mine(st, github=False, use_model=False)
    for d in st.decisions("proposed"):
        res = approve(st, d.id, use_model=False)
        assert res.activated, res.message
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "add", "-A"], cwd=dest, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "why"], cwd=dest, check=True)
    return st


def test_index_finds_ts_functions_and_callers(js_repo):
    idx = CodeIndex(js_repo.root, language="javascript")
    charge = idx.find("charge")[0]
    assert charge.ref == "src/payments/gateway:charge" and charge.lang == "js"
    assert [c.ref for c in idx.callers(charge)] == ["src/checkout:placeOrder"]


def test_all_three_kinds_compile_to_vitest_guards(js_repo):
    kinds = sorted(d.kind for d in js_repo.decisions("active"))
    assert kinds == ["call_count", "returns", "returns"]
    assert all(d.guard.path.endswith(".test.ts") and d.guard.framework == "vitest" for d in js_repo.decisions("active"))


def test_pr_check_catches_retry_and_floats(js_repo):
    repo = js_repo.root
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run([*git, "checkout", "-qb", "agent/x"], cwd=repo, check=True)
    co = repo / "src" / "checkout.ts"
    co.write_text(co.read_text().replace(
        "    await charge(order.totalCents, customer.cardToken, order.id);",
        "    for (let i = 0; i < 3; i++) { try { await charge(order.totalCents, customer.cardToken, order.id); break; }"
        " catch (e) { if (i === 2) throw e; } }"))
    pr = repo / "src" / "pricing.ts"
    pr.write_text(pr.read_text().replace("Math.round(i.priceCents)", "i.priceCents"))
    subprocess.run([*git, "commit", "-qam", "x"], cwd=repo, check=True)
    subprocess.run([*git, "checkout", "-q", "main"], cwd=repo, check=True)
    rep = check(js_repo, context="pr", code_ref="agent/x", trusted_ref="main", apply_transitions=False)
    assert len(rep.violations) == 2
    assert any("called 3 times" in i.log for i in rep.violations)
    assert any("598.5" in i.log for i in rep.violations)
    assert check(js_repo).ok


def test_npm_upgrade_names_the_changed_api(js_repo):
    node_modules_before = (js_repo.root / "node_modules" / "chronokit" / "package.json").read_text()
    rep = upgrade(js_repo, "chronokit@2.0.0", find_links=str(ROOT / "demo" / "packages" / "npm"))
    assert rep.error == "", rep.error
    assert rep.verdict == "blocked" and (rep.from_version, rep.to_version) == ("1.4.0", "2.0.0")
    (brk,) = rep.breaks
    assert brk.root_cause["api"] == "chronokit.parse"
    assert brk.root_cause["before"].endswith('Z"') and not brk.root_cause["after"].endswith('Z"')
    assert (js_repo.root / "node_modules" / "chronokit" / "package.json").read_text() == node_modules_before, \
        "upgrade checks must never touch the repo's own node_modules"


def test_js_guards_run_on_the_nebius_backend(js_repo, tmp_path, monkeypatch):
    """The Nebius job for a TS repo: bundle through (stand-in) object storage, install from the lockfile with the
    repo's package manager, run Vitest, and read the JSON report back from the job log. The generated job script
    runs locally with bash."""
    import shlex

    import yaml
    from test_nebius import FakeBucket

    from why import objstore, runner

    cfg_path = js_repo.dir / "config.yaml"
    original = cfg_path.read_text()
    cfg = yaml.safe_load(original)
    cfg.update(sandbox="nebius", nebius={"bucket": "why-sandboxes"})
    cfg_path.write_text(yaml.safe_dump(cfg))
    bucket = FakeBucket()
    shims = tmp_path / "bin"
    shims.mkdir()
    (shims / "corepack").write_text("#!/bin/sh\nexit 0\n")  # the node image enables pnpm; here it is installed
    (shims / "corepack").chmod(0o755)
    logs, scripts = {}, []

    def fake_nebius(*args):
        if args[:3] == ("ai", "job", "create"):
            assert args[args.index("--image") + 1] == "node:22-slim"
            script = shlex.split(args[args.index("--args") + 1])[1]
            scripts.append(script)
            r = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                               env={**os.environ, "PATH": f"{shims}:{os.environ['PATH']}"})
            logs["j"] = r.stdout + r.stderr
            return "j"
        if args[:3] == ("ai", "job", "get"):
            return '{"status": {"state": "COMPLETED"}}'
        return logs["j"]

    monkeypatch.setenv("WHY_NO_CACHE", "1")  # earlier tests checked this tree locally; force a real job
    monkeypatch.setattr(runner, "INJECT_LIMIT", 0)
    monkeypatch.setattr(runner, "JOB_ROOT", str(tmp_path / "job"))
    monkeypatch.setattr(runner, "_nebius", fake_nebius)
    monkeypatch.setattr(runner.shutil, "which", lambda n, _w=shutil.which: "/usr/bin/nebius" if n == "nebius" else _w(n))
    monkeypatch.setattr(objstore.Bucket, "from_config", classmethod(lambda cls, c: bucket))
    try:
        report = check(js_repo, context="nebius-js", record=False, apply_transitions=False)
    finally:
        cfg_path.write_text(original)
    assert "node -e" in scripts[0] and "python" not in scripts[0]
    assert "--frozen-lockfile" in scripts[0] and "vitest run" in scripts[0]
    assert report.ok and len(report.items) == 3, [(i.decision_id, i.result, i.log[-300:]) for i in report.items] + \
        [logs.get("j", "")[-1500:]]
    assert bucket.deleted and not bucket.objects


def test_guards_run_when_the_project_narrows_vitest_include(js_repo):
    """Projects often limit Vitest to tests/**; Why's guards in why_guards/ must still run."""
    cfg = js_repo.root / "vitest.config.ts"
    had = cfg.read_text() if cfg.exists() else None
    cfg.write_text('import { defineConfig } from "vitest/config";\n'
                   'export default defineConfig({ test: { include: ["tests/**/*.test.ts"] } });\n')
    try:
        report = check(js_repo, context="local", record=False, apply_transitions=False)
    finally:
        cfg.write_text(had) if had is not None else cfg.unlink()
    assert report.items and all(i.result == "pass" for i in report.items), \
        [(i.decision_id, i.result, i.log[-300:]) for i in report.items]
