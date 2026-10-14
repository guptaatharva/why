"""The TypeScript/Vitest path: code index, guard templates, PR checks and npm upgrade tracing.

Builds demo/build_demo_js.py (needs pnpm or a working npm, and the npm registry or a warm cache).
"""

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
