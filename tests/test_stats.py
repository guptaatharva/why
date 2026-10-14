"""Success metrics come from the log and evidence Why already keeps."""

import subprocess
import sys

from conftest import ROOT

from why.mine import mine
from why.ops import approve, check, reject, supersede
from why.stats import compute


def test_metrics_from_a_review_and_a_caught_pr(store):
    found = mine(store, github=False, use_model=False)
    retry = next(d for d in found if "retry" in d.title)
    printing = next(d for d in found if "print" in d.title)
    approve(store, retry.id, use_model=False)
    approve(store, printing.id, use_model=False)
    reject(store, next(d for d in found if "SMS" in d.title).id)

    m = compute(store)
    assert (m["review"]["approved"], m["review"]["rejected"]) == (2, 1)
    assert abs(m["review"]["approval_rate"] - 2 / 3) < 1e-9
    assert m["review"]["by_source"]["commit"] == {"approved": 2, "rejected": 1}

    root = store.root
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "why"], cwd=root, check=True)
    subprocess.run(["git", "checkout", "-qb", "agent/retry"], cwd=root, check=True)
    subprocess.run([sys.executable, str(ROOT / "demo" / "agent_retry_patch.py"), str(root)], check=True)
    subprocess.run(["git", "commit", "-qam", "retry"], cwd=root, check=True)
    subprocess.run(["git", "checkout", "-q", "main"], cwd=root, check=True)
    rep = check(store, context="pr", code_ref="agent/retry", trusted_ref="main", apply_transitions=False)
    assert not rep.ok

    m = compute(store)
    e = m["enforcement"]
    assert e["violations_caught"] == 1 and e["by_context"] == {"pr": 1} and e["runs_with_violations"] == 1
    assert e["overridden"] == [] and e["false_positive_rate_upper_bound"] == 0
    assert m["weekly"][-1]["caught"] == 1

    # The team decides the retry is fine after all: that violation now counts as a possible false positive.
    supersede(store, retry.id, "gateway now supports idempotency keys")
    e = compute(store)["enforcement"]
    assert e["overridden"] == [retry.id] and e["false_positive_rate_upper_bound"] == 1.0
