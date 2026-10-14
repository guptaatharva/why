"""Jest (CommonJS) repos: guard templates and npm upgrade tracing through setupFilesAfterEnv."""

import json
import shutil
import subprocess
import sys

import pytest
from conftest import ROOT

from why.mine import mine
from why.ops import approve, check
from why.store import Store
from why.upgrade import upgrade

NPM = ROOT / "demo" / "packages" / "npm"
pytestmark = pytest.mark.skipif(not shutil.which("pnpm"), reason="needs pnpm")

FILES = {
    "package.json": json.dumps({"name": "larkspur-jest", "private": True, "scripts": {"test": "jest"},
                                "devDependencies": {"jest": "29.7.0"}}),
    "src/dates.js": 'const { parse } = require("chronokit");\n\nfunction parseDate(text) {\n  return parse(text);\n}\n\n'
                    "module.exports = { parseDate };\n",
    "src/payments/gateway.js": "async function charge(amount, token, key) {\n  throw new Error('network');\n}\n\n"
                               "module.exports = { charge };\n",
    "src/checkout.js": 'const { charge } = require("./payments/gateway");\n\n'
                       "async function placeOrder(cart, customer) {\n  try {\n    await charge(1, customer.token, 'k');\n"
                       "  } catch (e) {\n    return 'payment_pending';\n  }\n  return 'paid';\n}\n\n"
                       "module.exports = { placeOrder };\n",
}
HISTORY = ("Never retry `charge()` on a timeout: the gateway double-bills on retry.\n\n"
           "`parseDate('2026-03-01T10:00:00+05:30')` must always return UTC because timestamps are compared "
           "across regions.")


@pytest.fixture(scope="module")
def jest_repo(tmp_path_factory):
    sys.path.insert(0, str(ROOT / "demo"))
    import build_demo_js

    build_demo_js.build_packages()
    repo = tmp_path_factory.mktemp("jest") / "larkspur-jest"
    for rel, src in FILES.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(src)
    (repo / "vendor").mkdir()
    shutil.copy(NPM / "chronokit-1.4.0.tgz", repo / "vendor")
    r = subprocess.run(["pnpm", "install", "--reporter=silent"], cwd=repo, capture_output=True, text=True)
    r2 = subprocess.run(["pnpm", "add", "--reporter=silent", "./vendor/chronokit-1.4.0.tgz"], cwd=repo,
                        capture_output=True, text=True)
    if r.returncode or r2.returncode:
        pytest.skip(f"pnpm install failed: {(r.stderr + r2.stderr)[-300:]}")
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@t"]
    (repo / ".gitignore").write_text("node_modules/\n")
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run([*git, "add", "-A"], cwd=repo, check=True)
    subprocess.run([*git, "commit", "-qm", HISTORY], cwd=repo, check=True)
    st = Store(repo)
    st.init(language="javascript")
    mine(st, github=False, use_model=False)
    for d in st.decisions("proposed"):
        res = approve(st, d.id, use_model=False)
        assert res.activated, res.message
    subprocess.run([*git, "add", "-A"], cwd=repo, check=True)
    subprocess.run([*git, "commit", "-qm", "why"], cwd=repo, check=True)
    return st


def test_jest_guards_are_plain_js(jest_repo):
    ds = jest_repo.decisions("active")
    assert sorted(d.kind for d in ds) == ["call_count", "returns"]
    for d in ds:
        assert d.guard.framework == "jest" and d.guard.path.endswith(".test.js")
        assert " as any" not in (jest_repo.root / d.guard.path).read_text()


def test_jest_catches_retry_with_reason(jest_repo):
    co = jest_repo.root / "src" / "checkout.js"
    original = co.read_text()
    co.write_text(original.replace("await charge(1, customer.token, 'k');",
                                   "for (let i = 0; i < 3; i++) { try { await charge(1, customer.token, 'k'); "
                                   "break; } catch (e) { if (i === 2) throw e; } }"))
    try:
        rep = check(jest_repo, apply_transitions=False)
    finally:
        co.write_text(original)
    (v,) = rep.violations
    assert "called 3 times" in v.log and "double-bills" in v.log


def test_jest_upgrade_names_the_changed_api(jest_repo):
    rep = upgrade(jest_repo, "chronokit@2.0.0", find_links=str(NPM))
    assert rep.error == "", rep.error
    assert rep.verdict == "blocked"
    (brk,) = rep.breaks
    assert brk.root_cause and brk.root_cause["api"] == "chronokit.parse"
    assert brk.root_cause["before"].endswith('Z"')
