"""Scale check for the PRD requirement: guard checks finish in under 5 minutes on a 50k-line repo.

Generates a synthetic Python service (default 50k lines across ~500 modules, 200 commits of history,
24 decisions of all three template kinds), then times the operations a team runs every day:

    why mine        rule-based miner over the full history and code index
    why approve     guard generation + 3 stability runs per decision
    why check       cold (fresh sandbox), cached (same tree), and after a one-line edit (dirty tree)

    uv run python bench/scale.py                 # 50k lines
    uv run python bench/scale.py --lines 200000 --json out.json

The code is synthetic and the decisions are written in, so this measures Why's own overhead (copying the
sandbox, indexing, running pytest), not how long a real repo's imports take.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from why.mine import mine  # noqa: E402
from why.models import Decision, Source  # noqa: E402
from why.ops import approve, check  # noqa: E402
from why.store import Store  # noqa: E402

LIMIT_S = 300
FUNCS_PER_MODULE = 9  # ~11 lines each + header: ~100 lines per module


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.name=bench", "-c", "user.email=bench@why.example", *args], cwd=repo,
                   check=True, capture_output=True)


def module_source(i: int) -> str:
    out = [f'"""Synthetic module {i}: pricing, ledger and formatting helpers."""', "", "import math", ""]
    for j in range(FUNCS_PER_MODULE):
        out += [
            f"def fn_{j}(x: int) -> int:",
            f'    """Helper {i}.{j}."""',
            f"    total = x * {j + 1} + {i % 7}",
            "    if total < 0:",
            "        total = -total",
            f"    for k in range({(j % 3) + 1}):",
            "        total += k",
            "    total = int(math.floor(total))",
            "    return total",
            "",
            "",
        ]
    return "\n".join(out)


GATEWAY = '''"""Payment gateway {g}."""


class GatewayTimeout(Exception):
    pass


def charge(amount_cents: int) -> str:
    """Capture a payment. The gateway double-bills if the same capture is retried."""
    return f"ch_{{amount_cents}}"
'''

CHECKOUT = '''"""Checkout flow {g}."""

from bigapp.pay.gateway_{g} import GatewayTimeout, charge


def place_order(amount_cents: int) -> str:
    try:
        return charge(amount_cents)
    except GatewayTimeout:
        return "payment_pending"
'''


def build(dest: Path, lines: int) -> int:
    dest.mkdir(parents=True)
    _git(dest, "init", "-q", "-b", "main")
    (dest / "bigapp" / "pay").mkdir(parents=True)
    (dest / "bigapp" / "__init__.py").write_text("")
    (dest / "bigapp" / "pay" / "__init__.py").write_text("")
    for g in range(6):
        (dest / "bigapp" / "pay" / f"gateway_{g}.py").write_text(GATEWAY.format(g=g))
        (dest / "bigapp" / "pay" / f"checkout_{g}.py").write_text(CHECKOUT.format(g=g))
    n_modules = max(1, lines // module_source(0).count("\n"))
    per_commit = max(1, n_modules // 200)
    total = 0
    for i in range(n_modules):
        src = module_source(i)
        total += src.count("\n") + 1
        (dest / "bigapp" / f"mod_{i}.py").write_text(src)
        if i % per_commit == per_commit - 1 or i == n_modules - 1:
            _git(dest, "add", "-A")
            msg = f"Add helpers batch {i // per_commit}"
            if i % (per_commit * 20) == 0:  # a few decision-shaped messages for the miner to chew on
                msg += f"\n\nNever retry `charge()` in checkout_{(i // per_commit) % 6}: the gateway double-bills."
            _git(dest, "commit", "-qm", msg)
    return total


def decisions(st: Store) -> list[str]:
    ids = []
    for g in range(6):
        d = st.add(Decision(id="", title=f"Never retry charge() in checkout {g}", rule="Never retry charge()",
                            reason="the gateway double-bills on retry", kind="call_count",
                            params={"target": f"bigapp.pay.gateway_{g}:charge",
                                    "entry": f"bigapp.pay.checkout_{g}:place_order",
                                    "raise": "GatewayTimeout", "max_calls": 1},
                            scope={"files": [f"bigapp/pay/checkout_{g}.py"], "symbols": []},
                            source=Source(type="manual")))
        ids.append(d.id)
    for k in range(12):
        d = st.add(Decision(id="", title=f"fn_{k % 9} in mod_{k * 37} never returns a negative total",
                            rule="totals are non-negative", reason="ledger rows are unsigned", kind="returns",
                            params={"target": f"bigapp.mod_{k * 37}:fn_{k % 9}", "call": f"fn_{k % 9}(-{k + 5})",
                                    "predicate": "result >= 0"},
                            scope={"files": [f"bigapp/mod_{k * 37}.py"], "symbols": []}, source=Source(type="manual")))
        ids.append(d.id)
    for word in ("print", "eval", "pickle.loads", "os.system", "breakpoint", "input"):
        d = st.add(Decision(id="", title=f"No {word}() in bigapp", rule=f"Never call {word}() in bigapp/",
                            reason="security and log hygiene", kind="forbid_pattern",
                            params={"pattern": rf"\b{word.replace('.', r'\.')}\s*\(", "paths": ["bigapp/"],
                                    "glob": "*.py"},
                            scope={"files": ["bigapp/"], "symbols": []}, source=Source(type="manual")))
        ids.append(d.id)
    return ids


def timed(label: str, fn, results: dict) -> object:
    t0 = time.time()
    out = fn()
    results[label] = round(time.time() - t0, 2)
    print(f"  {label:<28} {results[label]:>8.2f}s")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lines", type=int, default=50_000)
    ap.add_argument("--json")
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args()
    os.environ.setdefault("WHY_OFFLINE", "1")
    tmp = Path(tempfile.mkdtemp(prefix="why-scale-"))
    repo = tmp / "bigapp-repo"
    res: dict = {}
    try:
        print(f"Building a synthetic repo of ~{args.lines:,} lines…")
        lines = timed("build repo (not Why)", lambda: build(repo, args.lines), res)
        res["lines"] = lines
        print(f"  {lines:,} lines of Python in {len(list(repo.rglob('*.py')))} files\n")
        st = Store(repo)
        st.init()
        timed("why mine (rules, 200 commits)", lambda: mine(st, github=False, use_model=False), res)
        for d in st.decisions("proposed"):  # mined duplicates of the hand-written ones below
            st.transition(d.id, "retired", "bench", "bench uses explicit decisions")
        ids = decisions(st)
        res["guards"] = len(ids)

        def approve_all():
            bad = [r.message for r in (approve(st, i, use_model=False) for i in ids) if not r.activated]
            if bad:
                raise SystemExit("approve failed: " + "; ".join(bad[:3]))

        timed(f"why approve x{len(ids)} (3 runs each)", approve_all, res)
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", "why: guards")
        rep = timed("why check, cold", lambda: check(st, record=False), res)
        assert rep.ok and len(rep.items) == len(ids), [(i.decision_id, i.result) for i in rep.items if i.result != "pass"]
        rep = timed("why check, same tree (cache)", lambda: check(st, record=False), res)
        res["cached_guards"] = rep.cached
        f = repo / "bigapp" / "mod_3.py"
        f.write_text(f.read_text() + "\n# touched\n")
        rep = timed("why check, after 1-line edit", lambda: check(st, record=False), res)
        assert rep.ok
        worst = max(res["why check, cold"], res["why check, after 1-line edit"])
        res["limit_s"] = LIMIT_S
        res["ok"] = worst < LIMIT_S
        print(f"\nGuard check on {lines:,} lines with {len(ids)} guards: {worst:.1f}s "
              f"({'within' if res['ok'] else 'OVER'} the {LIMIT_S // 60}-minute budget).")
        if args.json:
            Path(args.json).write_text(json.dumps(res, indent=2))
        return 0 if res["ok"] else 1
    finally:
        if args.keep:
            print(f"kept {repo}")
        else:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
