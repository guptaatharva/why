"""The demo story, end to end, with no model and no network."""

import subprocess
import sys

from conftest import DIST, ROOT

from why.mine import mine
from why.ops import approve, check, supersede
from why.upgrade import upgrade


def _git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _by_title(store, needle):
    return next(d for d in store.decisions() if needle in d.title)


def test_mine_proposes_decisions_with_sources(store):
    found = mine(store, github=False, use_model=False)
    titles = [d.title for d in found]
    assert any("retry" in t for t in titles)
    assert any("UTC" in t for t in titles)
    assert not any("Fridays" in t for t in titles), "hedged jokes should not be proposed"
    for d in found:
        assert d.status == "proposed"
        assert d.source.quote and d.source.ref
    retry = _by_title(store, "retry")
    assert retry.kind == "call_count"
    assert retry.params["target"] == "larkspur.payments.gateway:charge"
    assert retry.params["entry"] == "larkspur.checkout:place_order"
    assert "double-bills" in retry.reason


def test_mining_is_idempotent(store):
    first = mine(store, github=False, use_model=False)
    second = mine(store, github=False, use_model=False)
    assert first and not second


def test_approve_check_and_catch_agent_retry(store):
    mine(store, github=False, use_model=False)
    retry = _by_title(store, "retry")
    utc = _by_title(store, "UTC")
    for d in (retry, utc):
        res = approve(store, d.id, use_model=False)
        assert res.activated, res.message
    custom = _by_title(store, "integer cents")
    assert custom.kind == "custom"

    report = check(store)
    assert report.ok and len(report.items) == 2

    repo = store.root
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "why")
    _git(repo, "checkout", "-qb", "agent/retry")
    subprocess.run([sys.executable, str(ROOT / "demo" / "agent_retry_patch.py"), str(repo)], check=True)
    _git(repo, "commit", "-qam", "retry")
    _git(repo, "checkout", "-q", "main")

    pr = check(store, context="pr", code_ref="agent/retry", trusted_ref="main", apply_transitions=False)
    assert not pr.ok
    assert [v.decision_id for v in pr.violations] == [retry.id]
    assert "called 3 times" in pr.violations[0].log
    assert store.get(retry.id).status == "active"  # PR checks record evidence only

    local = check(store, code_ref="agent/retry", trusted_ref="main")
    assert not local.ok
    assert store.get(retry.id).status == "violated"
    assert check(store).ok
    assert store.get(retry.id).status == "active"


def test_pr_cannot_delete_its_own_guard(store):
    mine(store, github=False, use_model=False)
    retry = _by_title(store, "retry")
    approve(store, retry.id, use_model=False)
    repo = store.root
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "why")
    _git(repo, "checkout", "-qb", "sneaky")
    subprocess.run([sys.executable, str(ROOT / "demo" / "agent_retry_patch.py"), str(repo)], check=True)
    (repo / store.get(retry.id).guard.path).unlink()
    _git(repo, "commit", "-qam", "retry and drop guard")
    _git(repo, "checkout", "-q", "main")
    pr = check(store, context="pr", code_ref="sneaky", trusted_ref="main", apply_transitions=False)
    assert not pr.ok


def test_upgrade_blocks_behavioural_break_and_finds_root_cause(store):
    mine(store, github=False, use_model=False)
    utc = _by_title(store, "UTC")
    approve(store, utc.id, use_model=False)
    rep = upgrade(store, "chronokit@2.0.0", find_links=str(DIST))
    assert rep.error == ""
    assert rep.verdict == "blocked"
    assert rep.from_version == "1.4.0" and rep.to_version == "2.0.0"
    (brk,) = rep.breaks
    assert brk.decision_id == utc.id
    assert brk.root_cause["api"] == "chronokit.parse"
    assert "tzinfo=datetime.timezone.utc" in brk.root_cause["before"]
    assert "tzinfo" not in brk.root_cause["after"]
    assert store.reports("upgrade")


def test_supersede_records_reason_and_removes_guard(store):
    mine(store, github=False, use_model=False)
    retry = _by_title(store, "retry")
    approve(store, retry.id, use_model=False)
    guard = store.root / store.get(retry.id).guard.path
    assert guard.exists()
    new = supersede(store, retry.id, "Quillpay v3 supports idempotent retries", "Retry `charge()` at most twice")
    old = store.get(retry.id)
    assert old.status == "superseded" and old.superseded_by == new.id
    assert new.supersedes == retry.id and new.status == "proposed"
    assert not guard.exists()
    assert [r["to"] for r in store.log(retry.id)] == ["proposed", "proposed", "active", "superseded"]
