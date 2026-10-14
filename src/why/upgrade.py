"""Upgrade check (F5): run the repo's guards against a new dependency version before the bump merges.

Steps:
  1. Sandbox the repo, run all active guards (plus guarantees the library publishes)
     on the current version, recording every call into the package.
  2. Install the new version, run the same guards again.
  3. Any guard that passed before and fails now is a behavioural break. Diff the two
     call traces to find the library API whose return value changed (the root cause),
     and have Nemotron Ultra explain it when a model is available.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from . import llm
from .gitutil import head
from .models import Evidence, now
from .runner import RunOutcome, Sandbox
from .store import Store

GUARANTEE_DIRS = ("why_guarantees",)


@dataclass
class Break:
    decision_id: str
    title: str
    reason: str
    log: str
    root_cause: dict[str, Any] | None
    explanation: str


@dataclass
class UpgradeReport:
    id: str
    package: str
    from_version: str
    to_version: str
    verdict: str  # blocked | approved | error
    breaks: list[Break]
    guards_run: int
    passed_before: int
    passed_after: int
    guarantees: list[str] = field(default_factory=list)
    registry: dict[str, Any] = field(default_factory=dict)  # which published bundle was applied
    commit: str = ""
    error: str = ""
    created_at: str = field(default_factory=now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def parse_spec(spec: str) -> tuple[str, str]:
    m = re.match(r"^(@?[A-Za-z0-9_.\-\[\]/]+?)\s*(?:@|==)\s*([^@\s]+)$", spec.strip())
    if not m:
        raise ValueError(f"expected <pkg>@<version>, got {spec!r}")
    return m.group(1), m.group(2)


def _import_name(sb: Sandbox, dist: str) -> str:
    """Top-level import name for a distribution (chronokit, dateutil for python-dateutil, ...)."""
    code = (
        "import importlib.metadata as m, sys\n"
        f"d = m.distribution({dist!r})\n"
        "top = (d.read_text('top_level.txt') or '').split()\n"
        "if not top:\n"
        "    top = sorted({f.parts[0] for f in (d.files or []) if f.suffix == '.py' and '.dist-info' not in str(f)})\n"
        "print(top[0].removesuffix('.py') if top else '')\n"
    )
    r = subprocess.run([sb.python, "-c", code], capture_output=True, text=True, env=sb.env())
    return r.stdout.strip() or dist.replace("-", "_")


def _version(sb: Sandbox, dist: str) -> str:
    r = subprocess.run([sb.python, "-c", f"import importlib.metadata as m; print(m.version({dist!r}))"],
                       capture_output=True, text=True, env=sb.env())
    return r.stdout.strip() if r.returncode == 0 else ""


def _collect_guarantees(sb: Sandbox, module: str) -> list[str]:
    """Published guarantees (F10): guard tests a library ships inside its package."""
    assert sb.dir
    r = subprocess.run([sb.python, "-c", f"import {module}, os; print(os.path.dirname({module}.__file__))"],
                       capture_output=True, text=True, env=sb.env())
    pkg_dir = Path(r.stdout.strip()) if r.returncode == 0 else None
    out = []
    if pkg_dir:
        for name in GUARANTEE_DIRS:
            src = pkg_dir / name
            if src.is_dir():
                dest = sb.dir / sb.store.config["guards_dir"] / f"_guarantees_{module}"
                shutil.rmtree(dest, ignore_errors=True)
                shutil.copytree(src, dest)
                out += [p.relative_to(sb.dir).as_posix() for p in sorted(dest.glob("test_*.py"))]
    return out


def _run_traced(sb: Sandbox, paths: list[str], module: str, label: str) -> tuple[RunOutcome, dict[str, list]]:
    assert sb.dir
    plugin = sb.dir / sb.store.config["guards_dir"] / "_why_trace.py"
    plugin.write_text((Path(__file__).parent / "trace_plugin.txt").read_text())
    out_file = sb.dir / f".why-trace-{label}.json"
    outcome = sb.run(paths, extra_args=["-p", "_why_trace"],
                     env={"WHY_TRACE_PKG": module, "WHY_TRACE_OUT": str(out_file)})
    traces = json.loads(out_file.read_text()) if out_file.exists() else {}
    return outcome, traces


def _test_for(results, decision_id: str) -> str | None:
    for r in results:
        if r.decision_id == decision_id:
            return r.test.split("::")[0]
    return None


def root_cause(before: dict[str, list], after: dict[str, list], test_file: str) -> dict[str, Any] | None:
    """First call into the package whose return value differs between versions, same args."""
    def calls_for(traces):
        return [c for nodeid, cs in traces.items() if nodeid.split("::")[0].endswith(Path(test_file).name) for c in cs]

    old, new = calls_for(before), calls_for(after)
    diffs = []
    old_by_key = {}
    for c in old:
        old_by_key.setdefault((c["fn"], json.dumps(c["args"], sort_keys=True)), c["ret"])
    for i, c in enumerate(new):
        key = (c["fn"], json.dumps(c["args"], sort_keys=True))
        if key in old_by_key and old_by_key[key] != c["ret"]:
            diffs.append({"api": c["fn"], "args": c["args"], "before": old_by_key[key], "after": c["ret"], "order": i})
    if not diffs:
        # Same API called with different effective arguments (e.g. a changed default) or a new API.
        first_old: dict[str, dict] = {}
        for c in old:
            first_old.setdefault(c["fn"], c)
        for i, c in enumerate(new):
            o = first_old.get(c["fn"])
            if o is None:
                return {"api": c["fn"], "note": "called only under the new version", "order": i}
            changed = {k: (o["args"].get(k), v) for k, v in c["args"].items() if o["args"].get(k) != v}
            if changed or o["ret"] != c["ret"]:
                return {"api": c["fn"], "order": i,
                        "before": ", ".join(f"{k}={b}" for k, (b, _) in changed.items()) or o["ret"],
                        "after": ", ".join(f"{k}={a}" for k, (_, a) in changed.items()) or c["ret"],
                        "note": "effective arguments changed" if changed else "return value changed"}
        return None
    counts: dict[str, int] = {}
    for d in diffs:
        counts[d["api"]] = counts.get(d["api"], 0) + 1
    diffs.sort(key=lambda d: (d["order"], -counts[d["api"]]))
    top = dict(diffs[0])
    top["candidates"] = sorted(counts, key=lambda k: -counts[k])
    return top


ULTRA_SYSTEM = """You diagnose behavioural dependency breaks. A library upgrade kept its API but changed
behaviour, and a guard enforcing a project decision now fails. Given the decision, the failure and the
changed return values, explain in 2-3 sentences what changed upstream and what the project should do
(pin, adapt the call site, or supersede the decision). Be concrete; no preamble."""


def explain(pkg: str, frm: str, to: str, decision: Any, log: str, rc: dict[str, Any] | None) -> str:
    fallback = ""
    if rc and "before" in rc:
        fallback = (f"`{rc['api']}` returns {rc['after']} under {pkg} {to} but {rc['before']} under {frm}, "
                    f"which breaks {decision.id} ({decision.title}).")
    elif rc:
        fallback = f"{pkg} {to} newly calls `{rc['api']}`; {decision.id} ({decision.title}) no longer holds."
    else:
        fallback = f"{decision.id} ({decision.title}) fails under {pkg} {to}; no single changed API was isolated."
    if not llm.available():
        return fallback
    user = (f"Package: {pkg} {frm} -> {to}\nDecision {decision.id}: {decision.rule}\nReason: {decision.reason}\n"
            f"Guard failure:\n{log[-1500:]}\nChanged return values: {json.dumps(rc)[:1500]}")
    try:
        return llm.chat("ultra", ULTRA_SYSTEM, user, max_tokens=400) or fallback
    except llm.LLMError:
        return fallback


# ---- npm (Vitest) ---------------------------------------------------------------------


def _npm_spec(pkg: str, version: str, find_links: str | None) -> str:
    """`pkg@ver`, or a local tarball `<find_links>/<pkg>-<ver>.tgz` when one exists (private/demo packages)."""
    if find_links:
        name = pkg.split("/")[-1]
        tgz = Path(find_links).expanduser().resolve() / f"{name}-{version}.tgz"
        if tgz.exists():
            return str(tgz)
    return f"{pkg}@{version}"


def _npm_version(sb: Sandbox, pkg: str) -> str:
    assert sb.dir
    pj = sb.dir / "node_modules" / pkg / "package.json"
    return json.loads(pj.read_text()).get("version", "") if pj.exists() else ""


def _run_traced_js(sb: Sandbox, paths: list[str], pkg: str, label: str) -> tuple[RunOutcome, dict[str, list]]:
    assert sb.dir
    gd = sb.dir / sb.store.config["guards_dir"]
    setup = gd / "_why_trace.setup.ts"
    setup.write_text((Path(__file__).parent / "trace_setup_js.txt").read_text().replace("__WHY_PKG__", pkg))
    base = next((c for c in ("vitest.config.ts", "vitest.config.mts", "vitest.config.js", "vitest.config.mjs",
                             "vite.config.ts", "vite.config.mts", "vite.config.js") if (sb.dir / c).exists()), None)
    cfg = sb.dir / "why.vitest.config.mts"
    if base:
        cfg.write_text(f'import {{ defineConfig, mergeConfig }} from "vitest/config";\nimport base from "./{base}";\n'
                       f'export default mergeConfig(base, defineConfig({{ test: {{ setupFiles: ["{setup}"] }} }}));\n')
    else:
        cfg.write_text(f'import {{ defineConfig }} from "vitest/config";\n'
                       f'export default defineConfig({{ test: {{ setupFiles: ["{setup}"] }} }});\n')
    out_file = sb.dir / f".why-trace-{label}.json"
    out_file.unlink(missing_ok=True)
    outcome = sb.run(paths, extra_args=["--config", str(cfg)], env={"WHY_TRACE_OUT": str(out_file)})
    traces = json.loads(out_file.read_text()) if out_file.exists() else {}
    return outcome, traces


def _run_traced_jest(sb: Sandbox, paths: list[str], pkg: str, label: str) -> tuple[RunOutcome, dict[str, list]]:
    """Jest: inject the tracer through setupFilesAfterEnv in a config that extends the project's own."""
    assert sb.dir
    setup = sb.dir / sb.store.config["guards_dir"] / "_why_trace.setup.cjs"
    setup.write_text((Path(__file__).parent / "trace_setup_jest.txt").read_text().replace("__WHY_PKG__", pkg))
    base = next((c for c in ("jest.config.js", "jest.config.cjs", "jest.config.json") if (sb.dir / c).exists()), None)
    load = f'require("./{base}")' if base else 'require("./package.json").jest || {}'
    cfg = sb.dir / "why.jest.config.cjs"
    cfg.write_text(f"const base = {load};\nmodule.exports = {{ ...base, rootDir: __dirname,\n"
                   f"  setupFilesAfterEnv: [...(base.setupFilesAfterEnv || []), {json.dumps(str(setup))}] }};\n")
    out_file = sb.dir / f".why-trace-{label}.json"
    out_file.unlink(missing_ok=True)
    outcome = sb.run(paths, extra_args=["--config", str(cfg)], env={"WHY_TRACE_OUT": str(out_file)})
    traces = json.loads(out_file.read_text()) if out_file.exists() else {}
    return outcome, traces


def _upgrade_js(store: Store, pkg: str, to: str, find_links: str | None, from_version: str | None,
                report: "UpgradeReport", decisions: dict, meta: dict) -> tuple[RunOutcome, RunOutcome, dict, dict]:
    from .guards import js_framework

    traced = _run_traced_js if js_framework(store) == "vitest" else _run_traced_jest
    with Sandbox(store, own_node_modules=True) as sb:
        sb.js_install()
        if from_version:
            sb.js_install(_npm_spec(pkg, from_version, find_links))
        report.from_version = _npm_version(sb, pkg) or from_version or "?"
        paths = [d.guard.path for d in decisions.values()]
        report.guarantees = _registry_guarantees(sb, "npm", pkg, report.from_version, report, meta)
        paths += report.guarantees
        before, tr_before = traced(sb, paths, pkg, "before")
        sb.js_install(_npm_spec(pkg, to, find_links))
        report.to_version = _npm_version(sb, pkg) or to
        after, tr_after = traced(sb, paths, pkg, "after")
    return before, after, tr_before, tr_after


def _registry_guarantees(sb: Sandbox, ecosystem: str, pkg: str, version: str, report: "UpgradeReport",
                         meta: dict[str, dict[str, Any]]) -> list[str]:
    """Fetch the guarantees `pkg` published for the version we are on and drop them into the sandbox."""
    from . import registry

    url = registry.registry_url(sb.store)
    if not url:
        return []
    bundle = registry.fetch(url, ecosystem, pkg, version)
    if not bundle:
        return []
    assert sb.dir
    installed = registry.install_bundle(bundle, sb.dir / sb.store.config["guards_dir"],
                                        sb.store.config.get("language", "python"))
    meta.update(installed)
    report.registry = {"url": url, "package": bundle["package"], "version": bundle["version"],
                       "guarantees": len(installed)}
    return list(installed)


def _upgrade_py(store: Store, pkg: str, to: str, find_links: str | None, from_version: str | None,
                report: "UpgradeReport", decisions: dict, meta: dict) -> tuple[RunOutcome, RunOutcome, dict, dict]:
    extra = ["--find-links", find_links] if find_links else []
    with Sandbox(store) as sb:
        sb.ensure_venv()
        if from_version:
            sb.pip_install(*extra, f"{pkg}=={from_version}")
        report.from_version = _version(sb, pkg) or from_version or "?"
        module = _import_name(sb, pkg)
        paths = [d.guard.path for d in decisions.values()]
        report.guarantees = _collect_guarantees(sb, module)
        report.guarantees += _registry_guarantees(sb, "pypi", pkg, report.from_version, report, meta)
        before, tr_before = _run_traced(sb, paths, module, "before")
        sb.pip_install(*extra, f"{pkg}=={to}")
        report.to_version = _version(sb, pkg) or to
        report.guarantees = sorted(set(report.guarantees) | set(_collect_guarantees(sb, module)))
        after, tr_after = _run_traced(sb, paths + report.guarantees, module, "after")
    return before, after, tr_before, tr_after


def upgrade(store: Store, spec: str, find_links: str | None = None, from_version: str | None = None,
            record: bool = True) -> UpgradeReport:
    pkg, to = parse_spec(spec)
    decisions = {d.id: d for d in store.decisions(("active", "violated")) if d.guard and not d.guard.flaky}
    rid = uuid.uuid4().hex[:12]
    commit = head(store.root)
    report = UpgradeReport(rid, pkg, from_version or "", to, "error", [], 0, 0, 0, commit=commit)
    run = _upgrade_js if store.config.get("language") == "javascript" else _upgrade_py
    meta: dict[str, dict[str, Any]] = {}  # guarantee path -> published metadata
    try:
        before, after, tr_before, tr_after = run(store, pkg, to, find_links, from_version, report, decisions, meta)
    except Exception as e:  # noqa: BLE001
        report.error = str(e)[-2000:]
        if record:
            store.save_report("upgrade", f"{pkg}@{to}-{rid}", report.to_dict())
        return report

    b, a = before.by_decision(), after.by_decision()
    report.guards_run = len(a)
    report.passed_before = sum(1 for r in b.values() if r.result == "pass")
    report.passed_after = sum(1 for r in a.values() if r.result == "pass")
    for did, r in a.items():
        was = b.get(did)
        if r.result in ("fail", "error") and (was is None or was.result == "pass"):
            d = decisions.get(did)
            test_file = _test_for(after.results, did) or ""
            rc = root_cause(tr_before, tr_after, test_file) if test_file else None
            if d is None:  # a guarantee the library published (registry bundle or shipped in the package)
                from .models import Decision as _D

                g = next((m for path, m in meta.items() if test_file and (path.endswith(test_file) or
                                                                           test_file.endswith(path))), None)
                if g:
                    d = _D(id=f"{g['package']}@{g['version']} {g['id']}", title=g["title"], rule=g["rule"],
                           reason=g.get("reason", ""))
                else:
                    d = _D(id=did if did != "?" else f"guarantee:{Path(test_file).stem}",
                           title=Path(test_file).stem, rule=f"{pkg} published guarantee {Path(test_file).name}")
            report.breaks.append(Break(d.id, d.title, d.reason, r.log[-3000:], rc,
                                       explain(pkg, report.from_version, report.to_version, d, r.log, rc)))
            if record and did in decisions:
                store.add_evidence(Evidence(did, r.result, "upgrade", commit, rid, r.log[-3000:], r.duration_s,
                                            detail={"package": pkg, "from": report.from_version,
                                                    "to": report.to_version, "root_cause": rc}))
    report.verdict = "blocked" if report.breaks else "approved"
    if record:
        store.save_report("upgrade", f"{pkg}@{to}-{rid}", report.to_dict())
    return report
