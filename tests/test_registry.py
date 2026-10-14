"""F10: a library publishes a guarantee; a downstream repo with no guard of its own is protected."""

import shutil
import subprocess

import httpx
from conftest import DIST, ROOT
from fastapi.testclient import TestClient

from why import registry
from why.models import Decision, Source
from why.ops import approve
from why.store import Store
from why.upgrade import upgrade


def _library_repo(tmp_path) -> Store:
    lib = tmp_path / "chronokit"
    shutil.copytree(ROOT / "demo" / "packages" / "chronokit-1.4.0", lib)
    subprocess.run(["git", "init", "-q"], cwd=lib, check=True)
    st = Store(lib)
    st.init()
    d = st.add(Decision(id="", title="parse() always returns UTC", rule="parse() always returns a UTC datetime",
                        reason="downstream code compares timestamps across regions", kind="returns",
                        params={"target": "chronokit:parse", "call": "parse('2026-03-01T10:00:00+05:30')",
                                "predicate": "result.utcoffset().total_seconds() == 0"},
                        source=Source(type="manual")))
    assert approve(st, d.id, use_model=False).activated
    return st


def test_publish_and_protect_downstream(tmp_path, store, monkeypatch):
    monkeypatch.setenv("WHY_WORKDIR", str(tmp_path / "server"))
    monkeypatch.setenv("WHY_REGISTRY_TOKEN", "pub")
    from why.server import api
    from why.server.app import create_app

    api.registry.repos.clear()
    client = TestClient(create_app(), base_url="http://registry.test")

    # The library maintainers publish their decision as a guarantee of 1.4.0.
    lib = _library_repo(tmp_path)
    bundle = registry.build_bundle(lib, ["D-0001"])
    assert (bundle["ecosystem"], bundle["package"], bundle["version"]) == ("pypi", "chronokit", "1.4.0")
    assert client.post("/api/registry/pypi/chronokit/1.4.0", json=bundle).status_code == 401
    assert client.post("/api/registry/pypi/chronokit/1.4.0", json=bundle,
                       headers={"authorization": "Bearer pub"}).json()["guarantees"] == 1
    assert client.get("/api/registry/pypi/chronokit").json()["versions"] == ["1.4.0"]

    # Downstream: Larkspur has NO decisions of its own, so without the registry the bump sails through.
    assert not store.decisions()
    rep = upgrade(store, "chronokit@2.0.0", find_links=str(DIST))
    assert rep.verdict == "approved"

    # With the registry configured, chronokit's own guarantee for 1.4.0 runs against 2.0.0 and blocks it.
    monkeypatch.setenv("WHY_REGISTRY_URL", "http://registry.test")
    monkeypatch.setattr(httpx, "get", lambda url, **kw: client.get(url.replace("http://registry.test", "")))
    rep = upgrade(store, "chronokit@2.0.0", find_links=str(DIST))
    assert rep.registry == {"url": "http://registry.test", "package": "chronokit", "version": "1.4.0",
                            "guarantees": 1}
    assert rep.verdict == "blocked", rep.error
    (brk,) = rep.breaks
    assert brk.decision_id == "chronokit@1.4.0 G-0001" and brk.title == "parse() always returns UTC"
    assert brk.root_cause["api"] == "chronokit.parse"


def test_publish_into_package(tmp_path):
    lib = _library_repo(tmp_path)
    bundle = registry.build_bundle(lib, ["D-0001"])
    written = registry.write_into_package(bundle, lib.root / "chronokit")
    assert [p.name for p in written] == ["test_g0001_parse_always_returns_utc.py"]
    assert (lib.root / "chronokit" / "why_guarantees" / "guarantees.json").exists()


def test_registry_rejects_bad_names(tmp_path, monkeypatch):
    monkeypatch.setenv("WHY_WORKDIR", str(tmp_path))
    monkeypatch.setenv("WHY_REGISTRY_TOKEN", "pub")
    from why.server.app import create_app

    client = TestClient(create_app())
    h = {"authorization": "Bearer pub"}
    bad = {"package": "x", "version": "1.0.0", "guarantees": [{"filename": "../../evil.py", "source": ""}]}
    assert client.post("/api/registry/pypi/x/1.0.0", json=bad, headers=h).status_code == 400
    assert client.post("/api/registry/cargo/x/1.0.0", json={**bad, "guarantees": []}, headers=h).status_code == 400
