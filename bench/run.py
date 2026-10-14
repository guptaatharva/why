#!/usr/bin/env python3
"""Run `why upgrade` over the micro benchmark and report breaks caught.

    uv run python bench/run.py [--json out.json]

For each case: build old/new wheels of the library, create a client repo, record the
team's decision, approve it (guard generated + 3 stability runs), then check the upgrade.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cases import CASES  # noqa: E402

from why.models import Decision, Source  # noqa: E402
from why.ops import approve  # noqa: E402
from why.store import Store  # noqa: E402
from why.upgrade import upgrade  # noqa: E402


def build_lib(work: Path, case: dict, version: str, src: str) -> None:
    pkg = work / f"{case['lib']}-{version}"
    (pkg / case["lib"]).mkdir(parents=True)
    (pkg / case["lib"] / "__init__.py").write_text(src)
    (pkg / "pyproject.toml").write_text(
        f'[project]\nname = "{case["lib"]}"\nversion = "{version}"\n[build-system]\n'
        f'requires = ["setuptools>=61"]\nbuild-backend = "setuptools.build_meta"\n'
        f'[tool.setuptools]\npackages = ["{case["lib"]}"]\n')
    subprocess.run(["uv", "build", "-q", "--wheel", "--out-dir", str(work / "dist"), str(pkg)], check=True,
                   capture_output=True)


def run_case(case: dict) -> dict:
    t0 = time.time()
    with tempfile.TemporaryDirectory(prefix=f"bbc-{case['name']}-") as tmp:
        work = Path(tmp)
        build_lib(work, case, "1.0.0", case["old"])
        build_lib(work, case, "2.0.0", case["new"])
        repo = work / "client"
        (repo / "app").mkdir(parents=True)
        (repo / "app" / "__init__.py").write_text("")
        (repo / "app" / "core.py").write_text(case["app"])
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        st = Store(repo)
        st.init()
        dd = case["decision"]
        d = st.add(Decision(id="", title=dd["title"], rule=dd["title"], reason=dd["reason"], kind=dd["kind"],
                            params=dd["params"], source=Source(type="manual", ref="bench")))
        out = {"case": case["name"], "expect_break": case["expect_break"]}
        # Install the old version only inside each sandbox, so cases cannot leak into each other.
        import yaml

        cfg = st.config
        cfg["install"] = ["--find-links", str(work / "dist"), f"{case['lib']}==1.0.0"]
        (st.dir / "config.yaml").write_text(yaml.safe_dump(cfg, sort_keys=False))
        res = approve(st, d.id, use_model=False)
        out["approved"] = res.activated
        if not res.activated:
            out.update(caught=False, note=res.message)
            return out
        rep = upgrade(st, f"{case['lib']}@2.0.0", find_links=str(work / "dist"), from_version="1.0.0")
        out["verdict"] = rep.verdict
        out["error"] = rep.error
        out["caught"] = rep.verdict == "blocked"
        api = rep.breaks[0].root_cause.get("api") if rep.breaks and rep.breaks[0].root_cause else None
        out["root_cause"] = api
        out["root_cause_correct"] = (api == case["expect_api"]) if case["expect_break"] else None
        out["seconds"] = round(time.time() - t0, 1)
        return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json")
    ap.add_argument("-k", help="only cases whose name contains this")
    args = ap.parse_args()
    rows = [run_case(c) for c in CASES if not args.k or args.k in c["name"]]
    breaks = [r for r in rows if r["expect_break"]]
    controls = [r for r in rows if not r["expect_break"]]
    caught = sum(1 for r in breaks if r.get("caught"))
    rc = sum(1 for r in breaks if r.get("root_cause_correct"))
    fp = sum(1 for r in controls if r.get("caught"))
    for r in rows:
        ok = r.get("caught") == r["expect_break"]
        print(f"{'✓' if ok else '✗'} {r['case']:<26} verdict={r.get('verdict', '-'):<9} "
              f"root_cause={r.get('root_cause') or '-':<22} {r.get('seconds', '')}s {r.get('error') or r.get('note', '')}")
    print(f"\nBreaks caught: {caught}/{len(breaks)}   root cause correct: {rc}/{len(breaks)}   "
          f"false positives on controls: {fp}/{len(controls)}")
    if args.json:
        Path(args.json).write_text(json.dumps({"rows": rows, "caught": caught, "breaks": len(breaks),
                                               "root_cause_correct": rc, "false_positives": fp}, indent=2))
    return 0 if caught == len(breaks) and fp == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
