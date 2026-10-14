"""F8: the MCP agent context feed, in-process and over a real stdio MCP session."""

import asyncio
import json
import subprocess
import sys

from conftest import ROOT

from why.mcp_server import build_server, decisions_for, propose_decision, run_guards
from why.mine import mine
from why.ops import approve


def _setup(store):
    mine(store, github=False, use_model=False)
    retry = next(d for d in store.decisions() if "retry" in d.title)
    approve(store, retry.id, use_model=False)
    return store.get(retry.id)


def _retry_diff(repo) -> str:
    """The agent's retry change as a unified diff, without leaving it in the working tree."""
    subprocess.run([sys.executable, str(ROOT / "demo" / "agent_retry_patch.py"), str(repo)], check=True)
    diff = subprocess.run(["git", "diff"], cwd=repo, capture_output=True, text=True, check=True).stdout
    subprocess.run(["git", "checkout", "--", "larkspur/checkout.py"], cwd=repo, check=True)
    return diff


def test_decisions_for_file_and_line(store):
    retry = _setup(store)
    out = decisions_for(store, "larkspur/checkout.py", 20)
    assert [d["id"] for d in out["decisions"]] == [retry.id]
    d = out["decisions"][0]
    assert "double-bills" in d["reason"] and d["status"] == "active" and d["last_result"] == "pass"
    notify = [d["id"] for d in decisions_for(store, "larkspur/notify.py")["decisions"]]
    assert retry.id not in notify and notify, "the folder-wide print() rule governs notify.py, the retry rule does not"
    assert decisions_for(store, "README.md")["decisions"] == []
    assert decisions_for(store, str(store.root / "larkspur"))["decisions"], "directories and absolute paths work"
    assert len(store.consults()) == 4


def test_run_guards_on_a_diff_without_touching_files(store):
    retry = _setup(store)
    before = (store.root / "larkspur/checkout.py").read_text()
    diff = _retry_diff(store.root)
    out = run_guards(store, diff)
    assert not out["ok"]
    assert [v["id"] for v in out["violations"]] == [retry.id]
    assert "double-bills" in out["violations"][0]["reason"]
    assert (store.root / "larkspur/checkout.py").read_text() == before
    assert store.get(retry.id).status == "active", "self-checks never change decision state"
    assert run_guards(store)["ok"]
    assert "does not apply" in run_guards(store, "garbage\n")["error"]


def test_diff_cannot_edit_guards(store):
    retry = _setup(store)
    diff = _retry_diff(store.root)
    guard = store.root / retry.guard.path
    original = guard.read_text()
    guard.write_text(original.replace("MAX_CALLS = 1", "MAX_CALLS = 99"))
    diff += subprocess.run(["git", "diff", "--no-index", "/dev/null", str(guard)], cwd=store.root,
                           capture_output=True, text=True).stdout.replace(str(store.root) + "/", "")
    guard.write_text(original)
    assert not run_guards(store, diff)["ok"]


def test_propose_decision(store):
    _setup(store)
    out = propose_decision(store, "Never call `send_batch()` from a request handler",
                           "it sleeps 60s per 100 messages and would block the request")
    assert out["ok"] and out["decision"]["status"].startswith("proposed")
    d = store.get(out["decision"]["id"])
    assert d.source.type == "agent_mcp" and "larkspur/notify.py" in d.scope["files"]
    assert propose_decision(store, "Never call `send_batch()` from a request handler", "dup")["duplicate_of"] == d.id
    assert not propose_decision(store, "Always do X", "")["ok"]


def test_server_exposes_the_three_tools(store):
    _setup(store)
    server = build_server(store)

    async def go():
        tools = {t.name for t in await server.list_tools()}
        res = await server.call_tool("why_decisions_for", {"path": "larkspur/checkout.py"})
        return tools, res

    tools, res = asyncio.run(go())
    assert tools == {"why_decisions_for", "why_propose_decision", "why_run_guards"}
    assert "double-bills" in json.dumps(res, default=str)


def test_stdio_session(store):
    """A real MCP client talks to `why mcp` over stdio."""
    _setup(store)
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    params = StdioServerParameters(command=sys.executable, args=["-m", "why.cli", "-C", str(store.root), "mcp"],
                                   env={"WHY_OFFLINE": "1", "PATH": "/usr/bin:/bin"})

    async def go():
        async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
            await s.initialize()
            names = sorted(t.name for t in (await s.list_tools()).tools)
            res = await s.call_tool("why_decisions_for", {"path": "larkspur/checkout.py", "line": 20})
            return names, res

    names, res = asyncio.run(go())
    assert names == ["why_decisions_for", "why_propose_decision", "why_run_guards"]
    text = json.dumps(res.structured_content or [c.text for c in res.content if hasattr(c, "text")])
    assert "Never retry" in text


def _rpc(client, method, params=None, token=None, rid=1):
    headers = {"accept": "application/json, text/event-stream", "content-type": "application/json"}
    if token:
        headers["authorization"] = f"Bearer {token}"
    return client.post("/mcp/", json={"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}},
                       headers=headers)


def _init(client, token=None):
    return _rpc(client, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                       "clientInfo": {"name": "t", "version": "0"}}, token)


def test_remote_mcp_is_read_only_without_token(store, monkeypatch):
    from fastapi.testclient import TestClient

    from why.server import api
    from why.server.app import create_app

    _setup(store)
    monkeypatch.delenv("WHY_MCP_TOKEN", raising=False)
    api.registry.repos.clear()
    with TestClient(create_app(local_root=store.root), base_url="http://localhost") as client:
        assert _init(client).status_code == 200
        tools = _rpc(client, "tools/list", rid=2).json()["result"]["tools"]
        assert [t["name"] for t in tools] == ["why_decisions_for"]
        res = _rpc(client, "tools/call", {"name": "why_decisions_for", "arguments": {"path": "larkspur/checkout.py"}},
                   rid=3).json()["result"]
        assert "double-bills" in json.dumps(res)


def test_remote_mcp_with_token_has_all_tools(store, monkeypatch):
    from fastapi.testclient import TestClient

    from why.server import api
    from why.server.app import create_app

    _setup(store)
    monkeypatch.setenv("WHY_MCP_TOKEN", "s3cret")
    api.registry.repos.clear()
    with TestClient(create_app(local_root=store.root), base_url="http://localhost") as client:
        assert _init(client).status_code == 401
        assert _init(client, token="s3cret").status_code == 200
        tools = _rpc(client, "tools/list", token="s3cret", rid=2).json()["result"]["tools"]
        assert sorted(t["name"] for t in tools) == ["why_decisions_for", "why_propose_decision", "why_run_guards"]
