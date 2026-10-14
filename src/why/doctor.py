"""`why doctor`: what is configured, what is live, and what to do next."""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import httpx

from . import llm
from .gitutil import github_slug, is_repo
from .store import Store


@dataclass
class Check:
    area: str
    name: str
    state: str  # ok | warn | off | fail
    detail: str
    fix: str = ""


def _run(cmd: list[str], timeout: int = 10) -> tuple[int, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout + r.stderr).strip()
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        return 127, str(e)


def _object_storage(store: Store, live: bool) -> Check:
    """Repos whose sandbox is over 64 KiB reach Nebius jobs through a bucket (objstore.py)."""
    from .objstore import Bucket

    cfg = store.config.get("nebius", {}) or {}
    fix = "set nebius.bucket in .why/config.yaml and NEBIUS_S3_ACCESS_KEY_ID / NEBIUS_S3_SECRET_ACCESS_KEY"
    if not cfg.get("bucket"):
        return Check("sandbox", "object storage", "warn", "no bucket: sandboxes over 64 KiB cannot run on Nebius", fix)
    try:
        b = Bucket.from_config(cfg)
        assert b is not None
    except RuntimeError as e:
        return Check("sandbox", "object storage", "fail", str(e), fix)
    if not live:
        return Check("sandbox", "object storage", "ok", f"bucket {cfg['bucket']} (keys set, not probed)")
    try:
        b.put("why-doctor/probe", b"ok")
        b.delete("why-doctor/probe")
        return Check("sandbox", "object storage", "ok", f"bucket {cfg['bucket']}: write + delete work")
    except Exception as e:  # noqa: BLE001
        return Check("sandbox", "object storage", "fail", str(e)[:200], fix)


def run(store: Store, live: bool = True) -> list[Check]:
    out: list[Check] = []

    # ---- repo -------------------------------------------------------------------
    if not is_repo(store.root):
        out.append(Check("repo", "git", "fail", f"{store.root} is not a git repository", "git init"))
    else:
        out.append(Check("repo", "git", "ok", str(store.root)))
    if store.exists and (store.dir / "config.yaml").exists():
        cfg = store.config
        counts = {s: len(store.decisions(s)) for s in ("active", "proposed", "violated")}
        out.append(Check("repo", ".why store", "ok",
                         f"{cfg['language']} · {counts['active']} active, {counts['proposed']} proposed, "
                         f"{counts['violated']} violated"))
        if cfg["language"] == "javascript":
            from .guards import js_framework

            fw = js_framework(store)
            ok = (store.root / "node_modules" / ".bin" / fw).exists()
            out.append(Check("repo", f"{fw} runner", "ok" if ok else "fail",
                             "installed" if ok else f"node_modules/.bin/{fw} missing", "npm install"))
    else:
        out.append(Check("repo", ".why store", "off", "not initialised", "why init"))

    # ---- Nemotron on Nebius Token Factory ------------------------------------------
    if not llm.api_key():
        out.append(Check("models", "Nebius Token Factory", "off",
                         "NEBIUS_API_KEY not set: rule-based mining and guard templates only",
                         "export NEBIUS_API_KEY=…"))
    elif os.environ.get("WHY_OFFLINE") in ("1", "true", "yes"):
        out.append(Check("models", "Nebius Token Factory", "warn", "key set but WHY_OFFLINE=1", "unset WHY_OFFLINE"))
    elif live:
        try:
            ids = llm.list_models()
            nem = [i for i in ids if "nemotron" in i.lower()]
            out.append(Check("models", "Nebius Token Factory", "ok", f"{len(ids)} models, {len(nem)} Nemotron"))
            for role in ("nano", "super", "ultra"):
                mid = llm.model_for(role)
                present = mid in ids
                out.append(Check("models", f"{role} → {mid}", "ok" if present else "fail",
                                 "available" if present else "not in /models",
                                 f"export WHY_MODEL_{role.upper()}=<id from `why models --all`>"))
            try:
                pong = llm.chat("nano", "Reply with the single word: pong", "ping", max_tokens=20)
                out.append(Check("models", "nano round-trip", "ok" if "pong" in pong.lower() else "warn",
                                 repr(pong[:60])))
            except llm.LLMError as e:
                out.append(Check("models", "nano round-trip", "fail", str(e)[:160]))
        except Exception as e:  # noqa: BLE001
            out.append(Check("models", "Nebius Token Factory", "fail", f"{llm.base_url()}: {str(e)[:160]}",
                             "check the key and WHY_LLM_BASE_URL"))

    # ---- Tavily -------------------------------------------------------------------------
    key = os.environ.get("TAVILY_API_KEY")
    if not key:
        out.append(Check("tavily", "Tavily search", "off", "TAVILY_API_KEY not set: `why assume` needs it",
                         "export TAVILY_API_KEY=tvly-…"))
    elif live:
        try:
            r = httpx.post("https://api.tavily.com/search", headers={"Authorization": f"Bearer {key}"},
                           json={"query": "Tavily API", "max_results": 1}, timeout=20)
            out.append(Check("tavily", "Tavily search", "ok" if r.status_code == 200 else "fail",
                             f"HTTP {r.status_code}" + ("" if r.status_code == 200 else f": {r.text[:120]}")))
        except httpx.HTTPError as e:
            out.append(Check("tavily", "Tavily search", "fail", str(e)[:160]))

    # ---- GitHub -------------------------------------------------------------------------
    slug = github_slug(store.root) if is_repo(store.root) else None
    out.append(Check("github", "remote", "ok" if slug else "warn", slug or "origin is not a GitHub remote",
                     "" if slug else "git remote add origin https://github.com/<owner>/<repo>.git"))
    code, text = _run(["gh", "auth", "status"])
    out.append(Check("github", "gh CLI", "ok" if code == 0 else "warn",
                     "logged in (PR mining, `why pr`)" if code == 0 else "not logged in", "gh auth login"))
    app = all(os.environ.get(k) for k in ("WHY_GITHUB_APP_ID", "WHY_GITHUB_PRIVATE_KEY", "WHY_GITHUB_WEBHOOK_SECRET"))
    if app and live:
        try:
            from .server.github_app import app_jwt

            r = httpx.get("https://api.github.com/app", headers={"Authorization": f"Bearer {app_jwt()}",
                                                                   "Accept": "application/vnd.github+json"}, timeout=20)
            name = r.json().get("slug", "?") if r.status_code == 200 else r.text[:120]
            out.append(Check("github", "GitHub App", "ok" if r.status_code == 200 else "fail", f"app: {name}"))
        except Exception as e:  # noqa: BLE001
            out.append(Check("github", "GitHub App", "fail", str(e)[:160]))
    else:
        out.append(Check("github", "GitHub App", "off" if not app else "warn",
                         "WHY_GITHUB_APP_ID / _PRIVATE_KEY / _WEBHOOK_SECRET not set" if not app else "set",
                         "set the WHY_GITHUB_* variables (see .env.example)"))

    # ---- sandbox ------------------------------------------------------------------------
    backend = store.config.get("sandbox", "local") if store.exists else "local"
    out.append(Check("sandbox", "uv", "ok" if shutil.which("uv") else "warn",
                     "fast sandbox venvs" if shutil.which("uv") else "falling back to python -m venv"))
    if backend == "nebius" or shutil.which("nebius"):
        code, text = _run(["nebius", "profile", "list"]) if shutil.which("nebius") else (127, "")
        out.append(Check("sandbox", "Nebius Serverless Jobs",
                         "ok" if code == 0 else ("fail" if backend == "nebius" else "off"),
                         "nebius CLI configured" if code == 0 else "nebius CLI missing or not logged in",
                         "https://docs.nebius.com/cli/install"))
    else:
        out.append(Check("sandbox", "Nebius Serverless Jobs", "off", "using the local sandbox",
                         "install the nebius CLI and set `sandbox: nebius`"))
    if backend == "nebius":
        out.append(_object_storage(store, live))

    # ---- MCP ----------------------------------------------------------------------------
    mcp_json = store.root / ".mcp.json"
    out.append(Check("mcp", "agent feed", "ok" if mcp_json.exists() else "off",
                     ".mcp.json registers `why mcp`" if mcp_json.exists() else "agents cannot ask Why yet",
                     "why mcp-install"))
    return out


def install_mcp(store: Store) -> Path:
    """Write a project .mcp.json so Claude Code (and other clients) start `why mcp` in this repo."""
    import json

    p = store.root / ".mcp.json"
    data = json.loads(p.read_text()) if p.exists() else {}
    data.setdefault("mcpServers", {})["why"] = {"command": "why", "args": ["mcp"]}
    p.write_text(json.dumps(data, indent=2) + "\n")
    return p
