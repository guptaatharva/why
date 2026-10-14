"""Find dependency bumps between two refs, so a Renovate / Dependabot / agent PR gets an upgrade check.

Reads pinned versions from the files a bump touches, preferring lockfiles (resolved versions) over
manifests (ranges):

    Python   uv.lock, poetry.lock, requirements*.txt (`==`), pyproject.toml ([project] deps with `==`)
    npm      package-lock.json (direct deps), pnpm-lock.yaml (importers '.'), package.json (exact or ^/~)

A bump is a package present at both refs with a different version. New and removed packages are
not bumps: there is no "before" behaviour to compare.
"""

from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass
from typing import Callable

import yaml

from .gitutil import git


@dataclass(frozen=True)
class Bump:
    ecosystem: str  # pypi | npm
    package: str
    before: str
    after: str
    file: str

    @property
    def spec(self) -> str:
        return f"{self.package}@{self.after}"


def _norm_py(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _clean_npm(v: str) -> str | None:
    v = v.strip()
    m = re.fullmatch(r"[\^~=v]?(\d+\.\d+\.\d+(?:[-+][\w.\-]+)?)", v)
    return m.group(1) if m else None


def _requirements(text: str) -> dict[str, str]:
    out = {}
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        m = re.match(r"^([A-Za-z0-9][A-Za-z0-9._\-]*)(?:\[[^\]]*\])?\s*==\s*([\w.\-+!]+)", line)
        if m:
            out[_norm_py(m.group(1))] = m.group(2)
    return out


def _pyproject(text: str) -> dict[str, str]:
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return {}
    deps = list(data.get("project", {}).get("dependencies", []))
    for group in (data.get("project", {}).get("optional-dependencies") or {}).values():
        deps += group
    return _requirements("\n".join(deps))


def _toml_lock(text: str) -> dict[str, str]:  # uv.lock and poetry.lock share [[package]] name/version
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return {}
    return {_norm_py(p["name"]): str(p["version"]) for p in data.get("package", [])
            if p.get("name") and p.get("version") and not (p.get("source") or {}).get("editable")
            and not (p.get("source") or {}).get("virtual")}


def _package_json(text: str) -> dict[str, str]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {}
    out = {}
    for key in ("dependencies", "devDependencies", "optionalDependencies"):
        for name, rng in (data.get(key) or {}).items():
            v = _clean_npm(str(rng))
            if v:
                out[name] = v
    return out


def _package_lock(text: str) -> dict[str, str]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {}
    out = {}
    for path, info in (data.get("packages") or {}).items():
        # Only top-level installs: "node_modules/x" or "node_modules/@s/x", not nested copies.
        m = re.fullmatch(r"node_modules/((?:@[^/]+/)?[^/]+)", path)
        if m and isinstance(info, dict) and info.get("version"):
            out[m.group(1)] = info["version"]
    if not out:  # lockfileVersion 1
        for name, info in (data.get("dependencies") or {}).items():
            if isinstance(info, dict) and info.get("version"):
                out[name] = info["version"]
    return out


def _pnpm_lock(text: str) -> dict[str, str]:
    try:
        data = yaml.safe_load(text) or {}
    except yaml.YAMLError:
        return {}
    out = {}
    imp = (data.get("importers") or {}).get(".") or data  # v6+/v9 importers, or v5 top level
    for key in ("dependencies", "devDependencies", "optionalDependencies"):
        for name, info in (imp.get(key) or {}).items():
            raw = info.get("version") if isinstance(info, dict) else info
            v = _clean_npm(str(raw).split("(")[0]) if raw else None
            if v:
                out[name] = v
    return out


# (ecosystem, filename pattern, parser), most authoritative first per ecosystem
SOURCES: list[tuple[str, str, Callable[[str], dict[str, str]]]] = [
    ("pypi", r"(^|/)uv\.lock$", _toml_lock),
    ("pypi", r"(^|/)poetry\.lock$", _toml_lock),
    ("pypi", r"(^|/)requirements[^/]*\.txt$", _requirements),
    ("pypi", r"(^|/)pyproject\.toml$", _pyproject),
    ("npm", r"(^|/)package-lock\.json$", _package_lock),
    ("npm", r"(^|/)pnpm-lock\.yaml$", _pnpm_lock),
    ("npm", r"(^|/)package\.json$", _package_json),
]


def find(root, base: str, head: str, ecosystem: str | None = None) -> list[Bump]:
    """Dependency bumps from `base` to `head`, one per package (the most authoritative file wins)."""
    changed = git(root, "diff", "--name-only", f"{base}...{head}", check=False).split()
    if not changed:
        changed = git(root, "diff", "--name-only", base, head, check=False).split()
    found: dict[tuple[str, str], Bump] = {}
    for eco, pattern, parse in SOURCES:
        if ecosystem and eco != ecosystem:
            continue
        for path in changed:
            if not re.search(pattern, path) or "node_modules/" in path:
                continue
            before = parse(git(root, "show", f"{base}:{path}", check=False))
            after = parse(git(root, "show", f"{head}:{path}", check=False))
            for name in sorted(before.keys() & after.keys()):
                if before[name] != after[name]:
                    found.setdefault((eco, name), Bump(eco, name, before[name], after[name], path))
    return list(found.values())
