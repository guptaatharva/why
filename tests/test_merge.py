"""F7: two agent branches that merge cleanly in git but contradict each other's decisions."""

import subprocess

from why.merge import merge_check
from why.mine import mine
from why.models import Decision, Source
from why.ops import approve

GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@t"]


def _git(repo, *args):
    subprocess.run([*GIT, *args], cwd=repo, check=True, capture_output=True)


def test_branches_contradict_each_other(store):
    repo = store.root
    mine(store, github=False, use_model=False)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "why store")

    # Agent A: adds refunds and a decision (with guard) that refunds never exceed what was charged.
    _git(repo, "checkout", "-qb", "agent/refunds")
    (repo / "larkspur" / "refunds.py").write_text(
        "from larkspur.pricing import apply_discount\n\n\n"
        "def refund_amount(total_cents: int, discount_percent: int) -> int:\n"
        "    return apply_discount(total_cents, discount_percent)\n")
    d = store.add(Decision(id="", title="Refunds never exceed the amount charged",
                           rule="`refund_amount(1000, 15)` never returns more than 1000",
                           reason="the gateway rejects over-refunds and flags the merchant",
                           kind="returns", params={"target": "larkspur.refunds:refund_amount",
                                                   "call": "refund_amount(1000, 15)",
                                                   "predicate": "0 <= result <= 1000"},
                           source=Source(type="agent_mcp", ref="agent-a")))
    assert approve(store, d.id, use_model=False).activated
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "refunds")

    # Agent B (from main): "fix" discounts by treating the percent as a surcharge for a promo. Different file.
    _git(repo, "checkout", "-q", "main")
    pricing = repo / "larkspur" / "pricing.py"
    pricing.write_text(pricing.read_text().replace(
        "return total_cents - (total_cents * percent) // 100",
        "return total_cents + (total_cents * percent) // 100"))
    _git(repo, "checkout", "-qb", "agent/promo")
    _git(repo, "commit", "-qam", "promo pricing")
    _git(repo, "checkout", "-q", "main")
    subprocess.run(["git", "checkout", "--", "."], cwd=repo, check=True)

    # Each branch alone is fine for Why: B has no refunds decision, A has the old pricing.
    assert merge_check(store, "agent/refunds", base="main")["ok"]
    assert merge_check(store, "agent/promo", base="main")["ok"]

    rep = merge_check(store, ["agent/refunds", "agent/promo"], base="main")
    assert not rep["ok"] and not rep.get("text_conflict")
    (x,) = rep["contradictions"]
    assert x["decision_id"] == d.id and x["from"] == "agent/refunds" and x["broken_by"] == ["agent/promo"]
    assert "1150" in x["log"]


def test_branch_cannot_weaken_a_base_guard(store):
    repo = store.root
    mine(store, github=False, use_model=False)
    retry = next(x for x in store.decisions() if "retry" in x.title)
    approve(store, retry.id, use_model=False)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "why")
    _git(repo, "checkout", "-qb", "sneaky")
    import sys

    from conftest import ROOT

    subprocess.run([sys.executable, str(ROOT / "demo" / "agent_retry_patch.py"), str(repo)], check=True)
    guard = repo / store.get(retry.id).guard.path
    guard.write_text(guard.read_text().replace("MAX_CALLS = 1", "MAX_CALLS = 99"))
    _git(repo, "commit", "-qam", "retry + relax guard")
    _git(repo, "checkout", "-q", "main")
    rep = merge_check(store, "sneaky", base="main")
    assert [x["decision_id"] for x in rep["contradictions"]] == [retry.id]


def test_id_clash_between_branches(store):
    repo = store.root
    store.add(Decision(id="", title="base", rule="base rule", source=Source(type="manual")))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    for br, rule in (("a", "Rule from agent A about caching"), ("b", "Rule from agent B about logging")):
        _git(repo, "checkout", "-qb", br, "main")
        store.add(Decision(id="", title=rule, rule=rule, source=Source(type="manual")))
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", br)
    _git(repo, "checkout", "-q", "main")
    rep = merge_check(store, ["a", "b"], base="main")
    assert rep["text_conflict"] and ".why/decisions/D-0002.yaml" in rep["conflicted_files"]
    assert [c["decision_id"] for c in rep["id_clashes"]] == ["D-0002"]
