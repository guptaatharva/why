"""Teams mode: roles, owners, required approvals, the review queue and dashboard identity."""

import pytest
import yaml

from why import team
from why.mine import mine
from why.ops import approve, reject, retire


def _teamify(store, **extra):
    cfg = yaml.safe_load((store.dir / "config.yaml").read_text())
    cfg["team"] = {
        "members": {
            "maya": {"role": "admin", "aliases": ["maya@larkspur.example"]},
            "theo": {"role": "reviewer"},
            "ines": {"role": "viewer"},
            "rafa": {"role": "reviewer"},
        },
        "owners": {"larkspur/payments/**": ["rafa"]},
        "required_approvals": 2,
        **extra,
    }
    (store.dir / "config.yaml").write_text(yaml.safe_dump(cfg))


def _retry(store):
    mine(store, github=False, use_model=False)
    return next(d for d in store.decisions() if "retry" in d.title)


def test_solo_mode_is_unchanged(store):
    d = _retry(store)
    assert team.role(store, "anyone") == "admin"
    assert approve(store, d.id, actor="anyone", use_model=False).activated


def test_viewer_and_outsider_cannot_approve(store):
    _teamify(store)
    d = _retry(store)
    for who in ("ines", "mallory"):
        with pytest.raises(team.PermissionDenied):
            approve(store, d.id, actor=who, use_model=False)
    assert store.get(d.id).status == "proposed" and not store.get(d.id).approvals


def test_two_approvals_and_an_owner(store):
    _teamify(store)
    d = _retry(store)
    assert "rafa" in team.owners_of(store, d)  # scope symbols larkspur.payments.gateway:charge
    r1 = approve(store, d.id, actor="maya@larkspur.example", use_model=False)
    assert r1.pending and not r1.activated and "1 more" in r1.message
    r1b = approve(store, d.id, actor="maya", use_model=False)  # same person twice counts once
    assert r1b.pending
    r2 = approve(store, d.id, actor="theo", use_model=False)
    assert r2.pending and "owner" in r2.message and "more approval" not in r2.message
    q = {row["id"]: row for row in team.queue(store)}
    assert q[d.id]["assigned"] == ["rafa"]
    assert d.id in [row["id"] for row in team.queue(store, "rafa")]
    assert d.id not in [row["id"] for row in team.queue(store, "theo")]
    r3 = approve(store, d.id, actor="rafa", use_model=False)
    assert r3.activated, r3.message
    assert [a["by"] for a in store.get(d.id).approvals] == ["maya", "theo", "rafa"]


def test_retire_needs_admin_or_owner(store):
    _teamify(store, required_approvals=1)
    d = _retry(store)
    assert approve(store, d.id, actor="rafa", use_model=False).activated
    with pytest.raises(team.PermissionDenied):
        retire(store, d.id, actor="theo")
    assert retire(store, d.id, actor="rafa").status == "retired"
    other = next(x for x in store.decisions("proposed"))
    with pytest.raises(team.PermissionDenied):
        reject(store, other.id, actor="ines")


def test_dashboard_tokens_identify_the_member(store, monkeypatch):
    from fastapi.testclient import TestClient

    from why.server.api import registry
    from why.server.app import create_app

    _teamify(store, required_approvals=1)
    d = _retry(store)
    monkeypatch.setenv("WHY_DASHBOARD_TOKENS", "rafa:tok-r,ines:tok-i")
    client = TestClient(create_app(local_root=store.root))
    registry.add("local", store.root)
    url = f"/api/repos/local/decisions/{d.id}/approve"
    assert client.post(url).status_code == 401
    assert client.post(url, headers={"Authorization": "Bearer tok-i"}).status_code == 403
    r = client.post(url, headers={"Authorization": "Bearer tok-r"}).json()
    assert r["ok"], r
    snap = client.get("/api/repos/local").json()
    assert snap["team"]["required_approvals"] == 1 and snap["auth_required"]
    assert client.get("/api/whoami", headers={"Authorization": "Bearer tok-r"}).json()["user"] == "rafa"
