"""Model-backed paths with Nemotron stubbed out (no network)."""

import re

from why import guards, llm, mine as mine_mod
from why.mine import mine
from why.ops import approve


def test_nano_mining_output_is_validated(store, monkeypatch):
    monkeypatch.setattr(llm, "available", lambda: True)

    def fake_chat_json(role, system, user, **kw):
        assert role == "nano"
        chunks = re.split(r"\n\n(?=\[\d+\] \()", user.split("SNIPPETS:\n", 1)[1])
        idx = next(int(c[1:c.index("]")]) for c in chunks if "Never retry `charge()`" in c)
        items = [
            {"snippet": "SN", "quote": "Never retry `charge()` on a timeout", "title": "Never retry charge()", "rule": "Never retry charge()",
             "reason": "double billing", "symbols": ["charge", "place_order"], "kind": "call_count",
             "params": {"target": "larkspur.payments.gateway:charge", "entry": "larkspur.checkout:place_order",
                        "raise": "TimeoutError", "max_calls": 1}},
            {"snippet": "SN", "quote": "Prices are always rounded up", "title": "Hallucinated", "rule": "Hallucinated rule about nothing",
             "reason": "", "symbols": ["does_not_exist"], "kind": "returns",
             "params": {"target": "nope:missing", "call": "missing()", "predicate": "True"}},
            {"snippet": 99, "title": "bad index"},
        ]
        for it in items:
            if it.get("snippet") == "SN":
                it["snippet"] = idx
        return items

    monkeypatch.setattr(llm, "chat_json", fake_chat_json)
    found = mine(store, github=False)
    titles = {d.title: d for d in found}
    assert "Never retry charge()" in titles
    assert titles["Never retry charge()"].kind == "call_count"
    assert not [d for d in store.decisions() if d.title == "Hallucinated"], "ungrounded quotes are dropped"


def test_super_writes_custom_guard(store, monkeypatch):
    monkeypatch.setattr(llm, "available", lambda: True)
    mine(store, github=False, use_model=False)
    cents = next(d for d in store.decisions() if "integer cents" in d.title)
    assert cents.kind == "custom"

    def fake_chat(role, system, user, **kw):
        assert role == "super" and "integer cents" in user
        return """Here is the guard:
```python
from types import SimpleNamespace as NS
from larkspur.pricing import cart_total, apply_discount

def test_d0004_prices_are_integer_cents():
    cart = NS(items=[NS(price_cents=199, quantity=3)])
    assert isinstance(cart_total(cart), int), "Violates D-0004: float drift broke refunds"
    assert isinstance(apply_discount(999, 15), int), "Violates D-0004"
```"""

    monkeypatch.setattr(llm, "chat", fake_chat)
    res = approve(store, cents.id)
    assert res.activated, res.message
    d = store.get(cents.id)
    assert d.guard.generated_by == "nemotron-super"
    assert "Why guard for" in (store.root / d.guard.path).read_text()


def test_bad_model_guard_is_rejected(store, monkeypatch):
    monkeypatch.setattr(llm, "available", lambda: True)
    mine(store, github=False, use_model=False)
    cents = next(d for d in store.decisions() if "integer cents" in d.title)
    monkeypatch.setattr(llm, "chat", lambda *a, **k: "```python\ndef test_x(:\n```")
    try:
        guards.generate(store, cents, mine_mod.index_for(store))
    except SyntaxError:
        pass
    else:
        raise AssertionError("syntax errors in model output must not become guards")
