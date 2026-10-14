"""Guard runs are cached per (code tree, guard source): rechecking the same code is free."""

import subprocess

from why import cache
from why.mine import mine
from why.ops import approve, check


def _active_retry(store):
    mine(store, github=False, use_model=False)
    d = next(d for d in store.decisions() if "retry" in d.title)
    assert approve(store, d.id, use_model=False).activated
    return d


def test_same_tree_is_served_from_cache(store):
    d = _active_retry(store)
    first = check(store)
    assert first.ok and first.cached == 0
    second = check(store)
    assert second.ok and second.cached == len(second.items)
    ev = [e for e in store.evidence(d.id) if e["run_id"] == second.run_id]
    assert ev and ev[0]["detail"].get("cached") is True


def test_code_change_or_dirty_tree_misses(store, monkeypatch):
    d = _active_retry(store)
    check(store)
    gw = store.root / "larkspur" / "checkout.py"
    gw.write_text(gw.read_text() + "\n# touched\n")
    assert cache.code_tree(store, None, None) is None  # dirty worktree: never cached
    assert check(store).cached == 0
    subprocess.run(["git", "commit", "-qam", "touch"], cwd=store.root, check=True)
    assert check(store).cached == 0  # new tree
    assert check(store).cached == 1
    # A different patch on the same tree is a different key.
    assert check(store, patch="").cached == 1
    monkeypatch.setenv("WHY_NO_CACHE", "1")
    assert check(store).cached == 0
    assert d.id


def test_guard_edit_invalidates(store):
    d = _active_retry(store)
    check(store)
    g = store.root / store.get(d.id).guard.path
    g.write_text(g.read_text() + "\n# stricter\n")
    assert check(store).cached == 0
