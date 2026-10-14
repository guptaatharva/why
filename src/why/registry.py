"""Published guarantees (F10): the decision supply chain.

A library maintainer publishes some of their decisions, with guards, as *guarantees* of a
release ("`parse()` always returns UTC" for chronokit 1.4.0). Downstream, `why upgrade` fetches
the guarantees published for the version a project is on and runs them against the new
version before the bump merges, so a library break is caught even if the downstream repo never
wrote a guard for it.

Two ways to publish:
    why publish D-0001 --to-package src/chronokit   ships why_guarantees/ inside the package
    why publish D-0001 --registry https://…          uploads to a Why registry (this server)

Bundle format (JSON):
    {"ecosystem": "pypi"|"npm", "package": "...", "version": "...", "published_at": "...",
     "guarantees": [{"id": "G-0001", "decision": "D-0001", "title", "rule", "reason",
                     "language", "framework", "filename", "source"}]}
"""

from __future__ import annotations

import json
import os
import re
import tomllib
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, Body, Header, HTTPException

from .guards import slug
from .models import now
from .store import Store, StoreError

GUARANTEE_DIR = "why_guarantees"


class RegistryError(RuntimeError):
    pass


# ---- library side ---------------------------------------------------------------------


def package_info(root: Path) -> tuple[str, str, str]:
    """(ecosystem, name, version) from pyproject.toml or package.json."""
    pj = root / "package.json"
    if pj.exists():
        data = json.loads(pj.read_text())
        return "npm", data.get("name", root.name), data.get("version", "0.0.0")
    pp = root / "pyproject.toml"
    if pp.exists():
        data = tomllib.loads(pp.read_text()).get("project", {})
        return "pypi", data.get("name", root.name), str(data.get("version", "0.0.0"))
    raise RegistryError("no package.json or pyproject.toml: cannot tell which package these guarantees belong to")


def _guarantee_filename(gid: str, title: str, language: str, framework: str) -> str:
    num = gid.split("-")[1]
    if language == "python":
        return f"test_g{num}_{slug(title)}.py"
    return f"g{num}_{slug(title)}.test.{'ts' if framework == 'vitest' else 'js'}"


def build_bundle(store: Store, decision_ids: list[str], version: str | None = None) -> dict[str, Any]:
    eco, name, ver = package_info(store.root)
    out = []
    for i, did in enumerate(decision_ids, 1):
        d = store.get(did)
        if d.status != "active" or not d.guard:
            raise StoreError(f"{d.id} must be active with a passing guard to be published (it is {d.status})")
        src = (store.root / d.guard.path).read_text()
        gid = f"G-{i:04d}"
        out.append({"id": gid, "decision": d.id, "title": d.title, "rule": d.rule, "reason": d.reason,
                    "language": d.guard.language, "framework": d.guard.framework,
                    "filename": _guarantee_filename(gid, d.title, d.guard.language, d.guard.framework),
                    "source": src})
    return {"ecosystem": eco, "package": name, "version": version or ver, "published_at": now(), "guarantees": out}


def write_into_package(bundle: dict[str, Any], package_dir: Path) -> list[Path]:
    """Ship guarantees inside the package so `why upgrade` finds them in the installed version."""
    dest = package_dir / GUARANTEE_DIR
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "__init__.py").write_text("") if bundle["ecosystem"] == "pypi" else None
    written = []
    for g in bundle["guarantees"]:
        p = dest / g["filename"]
        p.write_text(g["source"])
        written.append(p)
    (dest / "guarantees.json").write_text(json.dumps({k: v for k, v in bundle.items() if k != "guarantees"} |
                                                     {"guarantees": [{k: v for k, v in g.items() if k != "source"}
                                                                     for g in bundle["guarantees"]]}, indent=2))
    return written


def upload(bundle: dict[str, Any], registry: str, token: str | None = None) -> str:
    url = f"{registry.rstrip('/')}/api/registry/{bundle['ecosystem']}/{bundle['package']}/{bundle['version']}"
    r = httpx.post(url, json=bundle, timeout=30,
                   headers={"Authorization": f"Bearer {token}"} if token else {})
    if r.status_code >= 400:
        raise RegistryError(f"registry refused the upload ({r.status_code}): {r.text[:300]}")
    return url


# ---- downstream side ------------------------------------------------------------------


def registry_url(store: Store) -> str | None:
    return os.environ.get("WHY_REGISTRY_URL") or store.config.get("registry")


def _vkey(v: str) -> tuple:
    return tuple(int(x) if x.isdigit() else x for x in re.split(r"[.\-+]", v))


def fetch(registry: str, ecosystem: str, package: str, version: str) -> dict[str, Any] | None:
    """Guarantees for `version`, or for the closest earlier published version (promises carry forward)."""
    base = f"{registry.rstrip('/')}/api/registry/{ecosystem}/{package}"
    try:
        r = httpx.get(base, timeout=20)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        versions = r.json().get("versions", [])
        eligible = [v for v in versions if _vkey(v) <= _vkey(version)] if version and version != "?" else versions
        if not eligible:
            return None
        pick = max(eligible, key=_vkey)
        r = httpx.get(f"{base}/{pick}", timeout=20)
        r.raise_for_status()
        return r.json()
    except (httpx.HTTPError, ValueError):
        return None


def install_bundle(bundle: dict[str, Any], guards_root: Path, language: str) -> dict[str, dict[str, Any]]:
    """Write a fetched bundle's guarantees into the sandbox; returns {relative path: guarantee}."""
    dest = guards_root / f"_registry_{re.sub(r'[^A-Za-z0-9_]', '_', bundle['package'])}"
    dest.mkdir(parents=True, exist_ok=True)
    out = {}
    for g in bundle.get("guarantees", []):
        if (g.get("language") == "python") != (language == "python"):
            continue
        p = dest / g["filename"]
        p.write_text(g["source"])
        out[p.relative_to(guards_root.parent).as_posix()] = {**g, "package": bundle["package"],
                                                             "version": bundle["version"]}
    return out


# ---- registry server (mounted on the Why server) ----------------------------------------

router = APIRouter(prefix="/api/registry")
SAFE = re.compile(r"^[@A-Za-z0-9_.\-/]+$")


def _root() -> Path:
    return Path(os.environ.get("WHY_WORKDIR", Path.home() / ".why-server")).expanduser() / "registry"


def _pkg_dir(ecosystem: str, package: str) -> Path:
    if ecosystem not in ("pypi", "npm") or not SAFE.match(package) or ".." in package:
        raise HTTPException(400, "bad ecosystem or package name")
    return _root() / ecosystem / package.replace("/", "__")


@router.get("")
def list_packages() -> dict[str, Any]:
    out = []
    for eco in ("pypi", "npm"):
        for d in sorted((_root() / eco).glob("*")) if (_root() / eco).is_dir() else []:
            out.append({"ecosystem": eco, "package": d.name.replace("__", "/"),
                        "versions": sorted((p.stem for p in d.glob("*.json")), key=_vkey)})
    return {"packages": out}


@router.get("/{ecosystem}/{package:path}/{version}")
def get_version(ecosystem: str, package: str, version: str) -> dict[str, Any]:
    if not version[:1].isdigit():  # "/npm/@scope/name" is a package listing, not a version
        return get_package(ecosystem, f"{package}/{version}")
    p = _pkg_dir(ecosystem, package) / f"{version}.json"
    if not SAFE.match(version) or not p.exists():
        raise HTTPException(404, "no guarantees published for that version")
    return json.loads(p.read_text())


@router.get("/{ecosystem}/{package:path}")
def get_package(ecosystem: str, package: str) -> dict[str, Any]:
    d = _pkg_dir(ecosystem, package)
    if not d.is_dir():
        raise HTTPException(404, "package not in the registry")
    return {"ecosystem": ecosystem, "package": package, "versions": sorted((p.stem for p in d.glob("*.json")),
                                                                          key=_vkey)}


@router.post("/{ecosystem}/{package:path}/{version}")
def publish(ecosystem: str, package: str, version: str, bundle: dict[str, Any] = Body(...),
            authorization: str = Header("")) -> dict[str, Any]:
    token = os.environ.get("WHY_REGISTRY_TOKEN")
    if not token:
        raise HTTPException(403, "publishing is disabled on this registry (WHY_REGISTRY_TOKEN unset)")
    import hmac

    if not hmac.compare_digest(authorization.removeprefix("Bearer ").strip(), token):
        raise HTTPException(401, "bad token")
    if not SAFE.match(version) or bundle.get("package") != package or bundle.get("version") != version:
        raise HTTPException(400, "bundle does not match the URL")
    for g in bundle.get("guarantees", []):
        if not re.match(r"^(test_)?g\d{4}_[\w]+(\.test)?\.(py|ts|js)$", g.get("filename", "")):
            raise HTTPException(400, f"bad guarantee filename {g.get('filename')!r}")
    d = _pkg_dir(ecosystem, package)
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{version}.json").write_text(json.dumps(bundle, indent=2))
    return {"ok": True, "url": f"/api/registry/{ecosystem}/{package}/{version}",
            "guarantees": len(bundle.get("guarantees", []))}
