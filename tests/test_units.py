import hashlib
import hmac
import json

from fastapi.testclient import TestClient

from why.assumptions import check_assumptions, heuristic_verdict
from why.llm import extract_code, extract_json
from why.mine import split_rule
from why.models import Assumption, Decision, Source
from why.runner import parse_junit
from why.server.app import create_app
from why.server.github_app import verify_signature
from why.store import normalize_id


def test_split_rule():
    rule, reason = split_rule("Never retry `charge()` on a timeout: the gateway double-bills on retry.", "")
    assert rule == "Never retry `charge()` on a timeout"
    assert reason == "the gateway double-bills on retry"
    rule, reason = split_rule("Always use cents because floats drift.", "")
    assert rule == "Always use cents" and reason == "because floats drift"


def test_normalize_id():
    assert normalize_id("14") == "D-0014"
    assert normalize_id("#14") == "D-0014"
    assert normalize_id("d-7") == "D-0007"


def test_extract_json_and_code():
    assert extract_json('<think>hmm</think>Sure:\n```json\n[{"a": 1}]\n```') == [{"a": 1}]
    assert extract_json('{"verdict": "holds"}') == {"verdict": "holds"}
    assert extract_code("Here:\n```python\nx = 1\n```\n") == "x = 1\n"


def test_heuristic_verdicts():
    claim = "Fernwave's API rate limit is 100 requests per minute"
    assert heuristic_verdict(claim, "The limit is 100 requests per minute.")[0] == "holds"
    assert heuristic_verdict(claim, "Standard accounts: 60 requests per minute.")[0] == "broken"
    assert heuristic_verdict(claim, "Limits were lowered to 60 requests per minute; 100 requests needs Business.")[0] == "broken"
    assert heuristic_verdict(claim, "Fernwave is great.")[0] == "unclear"


def test_parse_junit_maps_tests_to_decisions():
    xml = """<testsuites><testsuite>
      <testcase classname="test_d0014_never_retry" name="test_d0014_never_retry" file="why_guards/test_d0014_never_retry.py" time="0.1"/>
      <testcase classname="test_d0002_utc" name="test_d0002_utc" time="0.2"><failure message="boom">trace</failure></testcase>
    </testsuite></testsuites>"""
    rs = parse_junit(xml)
    assert [(r.decision_id, r.result) for r in rs] == [("D-0014", "pass"), ("D-0002", "fail")]


def test_github_signature():
    body = b'{"x":1}'
    sig = "sha256=" + hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()
    assert verify_signature("s3cret", body, sig)
    assert not verify_signature("s3cret", body, "sha256=00")
    assert not verify_signature("s3cret", body, None)


def test_assumption_check_with_recorded_search(store):
    d = Decision(id="", title="Send at most 100 SMS per minute", rule="at most 100 SMS/min",
                 source=Source(type="manual"),
                 assumption=Assumption(claim="Fernwave's API rate limit is 100 requests per minute"))
    store.add(d)
    rows = check_assumptions(store, search_fn=lambda q: {"answer": "Now limited to 60 requests per minute.",
                                                         "results": [{"url": "https://x.example"}]})
    assert rows[0]["verdict"] == "broken"
    assert store.get(d.id).assumption.verdict == "broken"


def test_api_snapshot_and_actions(store, monkeypatch):
    from why.server import api

    api.registry.repos.clear()
    client = TestClient(create_app(local_root=store.root))
    assert client.get("/api/health").json()["ok"]
    d = store.add(Decision(id="", title="t", rule="r", source=Source(type="manual")))
    snap = client.get("/api/repos/local").json()
    assert snap["counts"]["proposed"] == 1
    assert snap["decisions"][0]["id"] == d.id
    assert client.get(f"/api/repos/local/decisions/{d.id}").json()["decision"]["title"] == "t"
    r = client.post(f"/api/repos/local/decisions/{d.id}/reject", json={"note": "noise"})
    assert r.json()["decision"]["status"] == "retired"
    monkeypatch.setenv("WHY_DASHBOARD_WRITE", "0")
    assert client.post(f"/api/repos/local/decisions/{d.id}/approve").status_code == 403
    assert json.loads(client.get("/snapshot.json").text)["repo"]["id"] == "local"
