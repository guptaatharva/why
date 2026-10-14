#!/usr/bin/env python3
"""A second coding agent, scripted: it talks to `why mcp` over a real MCP stdio session.

Asked to "fix checkout timeouts", it first asks Why what governs checkout.py, then self-checks
its planned retry diff before opening a PR, and drops the retry. This is the PRD's
"Prevention" beat; the enforcement beat (the first agent's PR blocked) is `why check`.

    python demo/agent_consults.py <repo>
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

REPO = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
HERE = Path(__file__).resolve().parent


def say(who: str, text: str) -> None:
    color = {"agent": "\033[33m", "why": "\033[36m"}[who]
    print(f"{color}{who:>5}\033[0m │ {text}")


def payload(res) -> dict:
    if getattr(res, "structured_content", None):
        sc = res.structured_content
        return sc.get("result", sc) if isinstance(sc, dict) else sc
    return json.loads(res.content[0].text)


def planned_diff() -> str:
    """The retry the agent was about to write, as a diff (the working tree is left untouched)."""
    subprocess.run([sys.executable, str(HERE / "agent_retry_patch.py"), str(REPO)], check=True)
    diff = subprocess.run(["git", "diff"], cwd=REPO, capture_output=True, text=True).stdout
    subprocess.run(["git", "checkout", "--", "larkspur/checkout.py"], cwd=REPO, check=True)
    return diff


async def main() -> None:
    why = os.environ.get("WHY_BIN", "why").split()
    params = StdioServerParameters(command=why[0], args=[*why[1:], "-C", str(REPO), "mcp"],
                                   env={**os.environ, "WHY_AGENT": "agent-2 (checkout fix)"})
    async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
        await s.initialize()
        say("agent", "Task: checkout times out on ~0.3% of charges. Plan: retry charge() up to 3×.")
        say("agent", "→ why_decisions_for(\"larkspur/checkout.py\")")
        out = payload(await s.call_tool("why_decisions_for", {"path": "larkspur/checkout.py"}))
        for d in out["decisions"]:
            say("why", f"{d['id']} [{d['status']}] {d['rule']}")
            say("why", f"      reason: {d['reason']}")
            say("why", f"      from {d['source']['type']} {d['source']['ref'][:10]}: “{d['source']['quote'][:90]}…”")
        say("agent", "→ why_run_guards(diff=<my retry change>)  # self-check before opening a PR")
        res = payload(await s.call_tool("why_run_guards", {"diff": planned_diff()}))
        for v in res.get("violations", []):
            say("why", f"✗ {v['id']} would be violated: {v['failure'][:110]}")
        say("agent", "Dropping the retry. Keeping one charge() attempt; timeouts stay payment_pending and go to "
                     "reconciliation, per the decision. Adding a reconciliation alert instead.")


if __name__ == "__main__":
    asyncio.run(main())
