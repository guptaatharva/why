"""Sandboxed guard runs.

A sandbox is a throwaway copy of the code under test. Guards and the decision store are
always taken from a *trusted* source (the base branch for a PR), never from the code
under test, so a change cannot weaken the guards that judge it.

Backends:
    local   temp dir + subprocess (default)
    nebius  Nebius Serverless AI Jobs: the sandbox is shipped as a tarball, pytest runs
            in a container, and results come back through the job logs.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .gitutil import export_tree, git, is_repo
from .guards import decision_id_from_path
from .store import Store


@dataclass
class GuardResult:
    decision_id: str
    test: str
    result: str  # pass | fail | error | skipped
    log: str = ""
    duration_s: float = 0.0


@dataclass
class RunOutcome:
    results: list[GuardResult] = field(default_factory=list)
    raw_log: str = ""
    returncode: int = 0
    duration_s: float = 0.0

    def by_decision(self) -> dict[str, GuardResult]:
        out: dict[str, GuardResult] = {}
        rank = {"error": 3, "fail": 2, "pass": 1, "skipped": 0}
        for r in self.results:
            cur = out.get(r.decision_id)
            if cur is None or rank[r.result] > rank[cur.result]:
                out[r.decision_id] = r
        return out


def _copy_worktree(root: Path, dest: Path) -> None:
    if is_repo(root):
        files = git(root, "ls-files", "-co", "--exclude-standard", "-z").split("\0")
        for rel in filter(None, files):
            src = root / rel
            if src.is_file():
                (dest / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest / rel)
    else:
        shutil.copytree(root, dest, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns(".git", ".venv", "node_modules", "__pycache__"))


class Sandbox:
    def __init__(self, store: Store, code_ref: str | None = None, trusted_ref: str | None = None,
                 code_dir: Path | None = None, keep: bool = False, patch: str | None = None,
                 own_node_modules: bool = False):
        self.store = store
        self.code_ref = code_ref
        self.trusted_ref = trusted_ref
        self.code_dir = code_dir
        self.patch = patch
        self.own_node_modules = own_node_modules  # upgrades install into the sandbox, never the repo
        self.keep = keep or os.environ.get("WHY_KEEP_SANDBOX") == "1"
        self.dir: Path | None = None
        self.venv: Path | None = None
        self.setup_log = ""

    def __enter__(self) -> "Sandbox":
        self.dir = Path(tempfile.mkdtemp(prefix="why-sandbox-"))
        root = self.store.root
        if self.code_dir:
            shutil.copytree(self.code_dir, self.dir, dirs_exist_ok=True, ignore=shutil.ignore_patterns(".git"))
        elif self.code_ref:
            export_tree(root, self.code_ref, self.dir)
        else:
            _copy_worktree(root, self.dir)
        gd = self.store.config["guards_dir"]
        if self.patch and self.patch.strip():
            self._apply_patch(self.patch, gd)
        # Replace guards + store with the trusted copy.
        for rel in (gd, ".why"):
            shutil.rmtree(self.dir / rel, ignore_errors=True)
            if self.trusted_ref:
                tmp = Path(tempfile.mkdtemp(prefix="why-trusted-"))
                try:
                    export_tree(root, self.trusted_ref, tmp)
                    if (tmp / rel).exists():
                        shutil.copytree(tmp / rel, self.dir / rel)
                finally:
                    shutil.rmtree(tmp, ignore_errors=True)
            elif (root / rel).exists():
                shutil.copytree(root / rel, self.dir / rel, ignore=shutil.ignore_patterns("__pycache__"))
        nm = root / "node_modules"
        if nm.is_dir() and not self.own_node_modules and not (self.dir / "node_modules").exists():
            (self.dir / "node_modules").symlink_to(nm)  # JS deps: reuse the repo's install
        self._run_setup()
        install = self.store.config.get("install") or []
        if install:
            # e.g. ["-r", "requirements.txt"]: the repo's own dependencies, isolated per sandbox
            self.pip_install(*install)
        return self

    def __exit__(self, *exc: Any) -> None:
        if self.dir and not self.keep:
            shutil.rmtree(self.dir, ignore_errors=True)

    def _apply_patch(self, diff: str, guards_dir: str) -> None:
        """Apply an agent's unified diff to the sandboxed code. Edits to guards or .why/ are ignored."""
        assert self.dir
        r = subprocess.run(["git", "apply", "--recount", "--whitespace=nowarn", f"--exclude={guards_dir}/*",
                            "--exclude=.why/*", "-"], cwd=self.dir, input=diff, capture_output=True, text=True)
        if r.returncode != 0:
            raise ValueError(f"diff does not apply to the working tree: {r.stderr.strip()[:800]}")

    # ---- environment -------------------------------------------------------

    def _run_setup(self) -> None:
        for cmd in self.store.config.get("setup") or []:
            r = subprocess.run(cmd, shell=True, cwd=self.dir, capture_output=True, text=True,
                               timeout=self.store.config.get("timeout_s", 300))
            self.setup_log += f"$ {cmd}\n{r.stdout}{r.stderr}"
            if r.returncode != 0:
                raise RuntimeError(f"sandbox setup failed: {cmd}\n{r.stderr[-2000:]}")

    def ensure_venv(self) -> Path:
        """An isolated interpreter (inherits host site-packages so pytest is present)."""
        if self.venv:
            return self.venv
        assert self.dir
        venv = self.dir / ".why-venv"
        if shutil.which("uv"):
            subprocess.run(["uv", "venv", "-q", "--python", sys.executable, str(venv)], check=True, capture_output=True)
        else:
            subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True)
        # Let the sandbox see the host's packages (pytest, the repo's deps) *behind* its own,
        # so anything installed into the sandbox shadows the host copy.
        import site

        host = [p for p in site.getsitepackages() if Path(p).is_dir()]
        for sp in venv.glob("lib/python*/site-packages"):
            (sp / "zz_why_host.pth").write_text("\n".join(host) + "\n")
        self.venv = venv
        return venv

    @property
    def python(self) -> str:
        assert self.dir
        for cand in (self.venv, self.dir / ".venv"):
            if cand and (cand / "bin" / "python").exists():
                return str(cand / "bin" / "python")
        return sys.executable

    def pip_install(self, *specs: str) -> str:
        venv = self.ensure_venv()
        py = str(venv / "bin" / "python")
        if shutil.which("uv"):
            cmd = ["uv", "pip", "install", "-q", "--python", py, *specs]
        else:
            cmd = [py, "-m", "pip", "install", "-q", *specs]
        r = subprocess.run(cmd, cwd=self.dir, capture_output=True, text=True, timeout=600)
        if r.returncode != 0:
            raise RuntimeError(f"install failed: {' '.join(specs)}\n{r.stderr[-2000:]}")
        return r.stdout + r.stderr

    # ---- JS packages (upgrade checks) -------------------------------------------

    def package_manager(self) -> str:
        assert self.dir
        pm = self.store.config.get("package_manager")
        if pm:
            return pm
        for lock, name in (("pnpm-lock.yaml", "pnpm"), ("yarn.lock", "yarn"), ("package-lock.json", "npm")):
            if (self.dir / lock).exists() and shutil.which(name):
                return name
        return "pnpm" if shutil.which("pnpm") else "npm"

    def js_install(self, *specs: str) -> str:
        """Install the repo's deps (and optionally `specs`) into the sandbox's own node_modules."""
        assert self.dir
        pm = self.package_manager()
        if specs:
            cmd = {"pnpm": ["pnpm", "add", "--reporter=silent", *specs],
                   "yarn": ["yarn", "add", "--silent", *specs],
                   "npm": ["npm", "install", "--no-audit", "--no-fund", "--no-save", *specs]}[pm]
        else:
            cmd = {"pnpm": ["pnpm", "install", "--prefer-offline", "--reporter=silent"],
                   "yarn": ["yarn", "install", "--silent"],
                   "npm": ["npm", "install", "--no-audit", "--no-fund"]}[pm]
        r = subprocess.run(cmd, cwd=self.dir, capture_output=True, text=True, timeout=900)
        if r.returncode != 0:
            raise RuntimeError(f"{' '.join(cmd[:3])} failed: {(r.stderr or r.stdout)[-1500:]}")
        return r.stdout + r.stderr

    def env(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        assert self.dir
        env = {k: v for k, v in os.environ.items() if not k.startswith(("NEBIUS_", "TAVILY_", "GITHUB_", "GH_"))}
        paths = [str(self.dir / p) for p in self.store.config.get("python_path", ["."])]
        env["PYTHONPATH"] = os.pathsep.join(paths + [str(self.dir / self.store.config["guards_dir"])])
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env.update(extra or {})
        return env

    # ---- running -----------------------------------------------------------

    def guard_paths(self, decision_ids: list[str] | None = None) -> list[str]:
        out = []
        for d in self.store.decisions():
            if d.guard and (decision_ids is None or d.id in decision_ids):
                out.append(d.guard.path)
        return out

    def run(self, guard_paths: list[str], extra_args: list[str] | None = None,
            env: dict[str, str] | None = None) -> RunOutcome:
        assert self.dir
        existing = [p for p in guard_paths if (self.dir / p).exists()]
        missing = [p for p in guard_paths if p not in existing]
        outcome = RunOutcome()
        for p in missing:
            did = decision_id_from_path(p) or "?"
            outcome.results.append(GuardResult(did, p, "error", "guard file missing from sandbox"))
        if not existing:
            return outcome
        backend = self.store.config.get("sandbox", "local")
        if self.store.config.get("language", "python") != "python":
            from .guards import js_framework

            if backend == "nebius":
                return _merge(outcome, run_on_nebius(self, existing, extra_args or [], js=js_framework(self.store)))
            return _merge(outcome, self._run_js(existing, js_framework(self.store), extra_args or [], env))
        if backend == "nebius":
            return _merge(outcome, run_on_nebius(self, existing, extra_args or []))
        return _merge(outcome, self._run_pytest(existing, extra_args or [], env))

    def _run_pytest(self, paths: list[str], extra: list[str], env: dict[str, str] | None) -> RunOutcome:
        assert self.dir
        junit = self.dir / ".why-junit.xml"
        cmd = [self.python, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--rootdir", str(self.dir),
               f"--junitxml={junit}", "-o", "junit_family=xunit1", *extra, *paths]
        t0 = time.time()
        try:
            r = subprocess.run(cmd, cwd=self.dir, env=self.env(env), capture_output=True, text=True,
                               timeout=self.store.config.get("timeout_s", 300))
            log, code = r.stdout + r.stderr, r.returncode
        except subprocess.TimeoutExpired as e:
            log, code = f"timeout after {e.timeout}s\n{e.stdout or ''}", 124
        out = RunOutcome(raw_log=log, returncode=code, duration_s=round(time.time() - t0, 2))
        out.results = parse_junit(junit.read_text()) if junit.exists() else []
        if not out.results:
            for p in paths:
                out.results.append(GuardResult(decision_id_from_path(p) or "?", p, "error", log[-3000:]))
        return out

    def _run_js(self, paths: list[str], framework: str = "jest", extra: list[str] | None = None,
                env: dict[str, str] | None = None) -> RunOutcome:
        """Jest and Vitest both write the same JSON report shape (testResults/assertionResults)."""
        assert self.dir
        report = self.dir / ".why-js-report.json"
        if framework == "vitest":
            local = self.dir / "node_modules" / ".bin" / "vitest"
            runner = [str(local)] if local.exists() else ["npx", "--no-install", "vitest"]
            cmd = [*runner, "run", "--reporter=json", f"--outputFile={report}", "--root", str(self.dir),
                   *(extra or []), *paths]
        else:
            local = self.dir / "node_modules" / ".bin" / "jest"
            runner = [str(local)] if local.exists() else ["npx", "--yes", "jest"]
            cmd = [*runner, "--json", f"--outputFile={report}", "--rootDir", str(self.dir), *(extra or []), *paths]
        t0 = time.time()
        r = subprocess.run(cmd, cwd=self.dir, capture_output=True, text=True, env=self.env(env),  # no secrets
                           timeout=self.store.config.get("timeout_s", 300))
        out = RunOutcome(raw_log=r.stdout + r.stderr, returncode=r.returncode, duration_s=round(time.time() - t0, 2))
        if report.exists():
            out.results = parse_js_report(json.loads(report.read_text()), str(self.dir))
        return out


def parse_js_report(data: dict[str, Any], base: str) -> list[GuardResult]:
    """Jest `--json` and Vitest `--reporter=json` share the testResults/assertionResults shape."""
    out = []
    for tr in data.get("testResults", []):
        rel = os.path.relpath(tr["name"], base) if os.path.isabs(tr["name"]) else tr["name"]
        did = decision_id_from_path(rel) or "?"
        for a in tr.get("assertionResults", []):
            status = {"passed": "pass", "failed": "fail"}.get(a.get("status"), "skipped")
            out.append(GuardResult(did, rel, status, "\n".join(a.get("failureMessages", []))[-3000:],
                                   (a.get("duration") or 0) / 1000))
        if not tr.get("assertionResults") and tr.get("status") == "failed":
            out.append(GuardResult(did, rel, "error", (tr.get("message") or "")[-3000:]))
    return out


def _merge(a: RunOutcome, b: RunOutcome) -> RunOutcome:
    return RunOutcome(a.results + b.results, b.raw_log, b.returncode, b.duration_s)


def parse_junit(xml: str) -> list[GuardResult]:
    out = []
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return out
    for tc in root.iter("testcase"):
        file = tc.get("file") or tc.get("classname", "").replace(".", "/") + ".py"
        did = next((x for x in (decision_id_from_path(c.split(".")[-1] if c != file else c)
                                for c in (file, tc.get("classname", ""), tc.get("name", ""))) if x), "?")
        result, log = "pass", ""
        for tag, res in (("failure", "fail"), ("error", "error"), ("skipped", "skipped")):
            el = tc.find(tag)
            if el is not None:
                result = res
                log = ((el.get("message") or "") + "\n" + (el.text or "")).strip()
                break
        out.append(GuardResult(did, f"{file}::{tc.get('name')}", result, log[-4000:], float(tc.get("time") or 0)))
    return out


# ---- Nebius Serverless AI Jobs backend ----------------------------------------------

BEGIN, END = "===WHY-JUNIT-BEGIN===", "===WHY-JUNIT-END==="
INJECT_LIMIT = 63 * 1024  # `--inject-file` caps files at 64 KiB
JOB_ROOT = ""  # prefix for /w and /why inside the job container (tests run the script locally)


def _bundle(dir: Path) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for p in sorted(dir.rglob("*")):
            rel = p.relative_to(dir)
            if any(part in (".why-venv", ".venv", "node_modules", ".git", "__pycache__") for part in rel.parts):
                continue
            if p.is_file():
                tar.add(p, arcname=rel.as_posix())
    return buf.getvalue()


def _nebius(*args: str) -> str:
    r = subprocess.run(["nebius", *args], capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise RuntimeError(f"nebius {' '.join(args[:3])}: {r.stderr.strip()[:500]}")
    return r.stdout


def _job_install_js(dir: Path, cfg_pm: str | None) -> str:
    """Install the repo's JS deps inside the job from its own lockfile (node_modules is never shipped)."""
    pm = cfg_pm or next((n for lock, n in (("pnpm-lock.yaml", "pnpm"), ("yarn.lock", "yarn"),
                                          ("package-lock.json", "npm")) if (dir / lock).exists()), "npm")
    return {"pnpm": "corepack enable && pnpm install --frozen-lockfile --reporter=silent",
            "yarn": "corepack enable && yarn install --frozen-lockfile --silent",
            "npm": "npm ci --no-audit --no-fund" if (dir / "package-lock.json").exists()
            else "npm install --no-audit --no-fund"}[pm]


def run_on_nebius(sb: Sandbox, paths: list[str], extra: list[str], js: str | None = None) -> RunOutcome:
    """Run guards as a Nebius Serverless AI job (CPU platform). Requires the `nebius` CLI, logged in.

    Python repos run pytest in `nebius.image` (python:3.12-slim); JS/TS repos (`js` = vitest | jest) install
    their deps from the lockfile and run the guards in `nebius.image_js` (node:22-slim)."""
    assert sb.dir
    if not shutil.which("nebius"):
        raise RuntimeError("sandbox: nebius selected but the `nebius` CLI is not installed")
    from .objstore import Bucket

    cfg = sb.store.config.get("nebius", {})
    data = _bundle(sb.dir)
    bundle = Path(tempfile.mkstemp(suffix=".tgz")[1])
    bundle.write_bytes(data)
    bucket, key = None, ""
    if len(data) > INJECT_LIMIT:
        # Too big for --inject-file: stage it in object storage, hand the job a short-lived GET URL.
        bucket = Bucket.from_config(cfg)
        if bucket is None:
            bundle.unlink(missing_ok=True)
            raise RuntimeError(f"sandbox bundle is {len(data)} bytes; nebius inject-file allows 64 KiB. "
                               "Set nebius.bucket in .why/config.yaml (object storage), narrow the repo "
                               "(python_path), or use the local backend.")
        key = f"why-sandbox/{hashlib.sha256(data).hexdigest()[:16]}-{int(time.time())}.tgz"
    R = JOB_ROOT
    pypath = ":".join(f"{R}/w/{p}" for p in sb.store.config.get("python_path", ["."]))
    pypath += f":{R}/w/{sb.store.config['guards_dir']}"
    setup = " && ".join(sb.store.config.get("setup") or ["true"])
    t0 = time.time()
    status, job_id = "", "?"
    try:
        if bucket is not None:
            bucket.put(key, data)
            # No single quotes anywhere: the whole script is wrapped in single quotes for `bash -c`.
            if js:  # node images have no python; Node 22 has fetch built in
                fetch = ("node -e \"fetch(process.argv[1]).then(r=>{if(!r.ok)process.exit(1);return r.arrayBuffer()})"
                         f".then(b=>process.stdout.write(Buffer.from(b)))\" \"{bucket.url('GET', key, 3600)}\" "
                         f"> {R}/why/bundle.tgz && ")
            else:
                fetch = (f"python -c \"import sys,urllib.request as u;u.urlretrieve(sys.argv[1],sys.argv[2])\" "
                         f"\"{bucket.url('GET', key, 3600)}\" {R}/why/bundle.tgz && ")
            inject: list[str] = []
        else:
            fetch = ""
            inject = ["--inject-file", f"{bundle}:/why/bundle.tgz"]
        unpack = f"mkdir -p {R}/w {R}/why && {fetch}cd {R}/w && tar xzf {R}/why/bundle.tgz"
        if js:
            out_file = f"{R}/w/.why-js-report.json"
            run = (f"npx --no-install vitest run --reporter=json --outputFile={out_file} --root {R}/w"
                   if js == "vitest" else f"npx --no-install jest --json --outputFile={out_file} --rootDir {R}/w")
            script = (f"{unpack} && {_job_install_js(sb.dir, sb.store.config.get('package_manager'))} && {setup} && "
                      f"{run} {' '.join(extra)} {' '.join(paths)}; echo {BEGIN}; base64 -w0 {out_file}; echo; echo {END}")
            image = cfg.get("image_js", "node:22-slim")
        else:
            script = (
                f"{unpack} && pip install -q pytest && "
                f"{setup} && PYTHONPATH={pypath} python -m pytest -q -p no:cacheprovider --junitxml={R}/w/j.xml "
                f"{' '.join(extra)} {' '.join(paths)}; echo {BEGIN}; base64 -w0 {R}/w/j.xml; echo; echo {END}"
            )
            image = cfg.get("image", "python:3.12-slim")
        job_id = _nebius(
            "ai", "job", "create", "--name", f"why-{int(t0)}", "--image", image,
            "--container-command", "bash", "--args", f"-c '{script}'",
            "--platform", cfg.get("platform", "cpu-d3"), "--preset", cfg.get("preset", "4vcpu-16gb"),
            "--timeout", cfg.get("timeout", "1h"), *inject,
            "--async", "--format", "jsonpath={.resource_id}",
        ).strip()
        deadline = t0 + sb.store.config.get("timeout_s", 300) + 600
        while time.time() < deadline:
            info = json.loads(_nebius("ai", "job", "get", job_id, "--format", "json") or "{}")
            status = str(info.get("status", {}).get("state", "")).upper()
            if status in ("COMPLETED", "FAILED", "CANCELLED", "CANCELED", "ERROR"):
                break
            time.sleep(10)
        logs = _nebius("ai", "job", "logs", job_id)
    finally:
        bundle.unlink(missing_ok=True)
        if bucket is not None:
            bucket.delete(key)
    out = RunOutcome(raw_log=logs[-20000:], duration_s=round(time.time() - t0, 2))
    if BEGIN in logs and END in logs:
        b64 = logs.split(BEGIN, 1)[1].split(END, 1)[0].strip()
        raw = base64.b64decode(b64).decode(errors="replace") if b64 else ""
        if js:
            try:
                out.results = parse_js_report(json.loads(raw), f"{JOB_ROOT}/w")
            except json.JSONDecodeError:
                out.results = []
        else:
            out.results = parse_junit(raw)
    if not out.results:
        out.results = [GuardResult(decision_id_from_path(p) or "?", p, "error", f"nebius job {job_id} {status}")
                       for p in paths]
    return out
