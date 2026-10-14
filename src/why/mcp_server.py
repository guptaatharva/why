"""Agent context feed (F8): Why over MCP, so agents consult decisions *before* they break them.

Tools (names per the PRD):
    why_decisions_for(path, line?)     the decisions that govern a file or line, with their reasons
    why_propose_decision(rule, reason) log a choice as it is made; it lands as `proposed`, never active
    why_run_guards(diff?)              self-check: run every active guard on the working tree (+ a diff)

Run it for the current repo over stdio:
    why mcp
    claude mcp add why -- why -C /path/to/repo mcp
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .mine import _similar, compile_rule, index_for
from .models import Decision, Source, now
from .ops import blame, check
from .store import Store

INSTRUCTIONS = """Why holds the decisions behind this codebase: rules the code must keep, each with the reason
it was made and an executable guard that fails the build when the rule is broken.
- Before editing a file, call why_decisions_for(path) and keep every rule it returns.
- When you make a lasting choice (an invariant, a "never do X because Y"), record it with why_propose_decision.
- Before opening a PR, call why_run_guards() and fix any violation.
If a rule must change, do not work around its guard: say so to the human and propose a new decision with the reason."""

SHOWN = ("active", "violated", "proposed")


def _rel(store: Store, path: str) -> str:
    p = Path(path)
    if p.is_absolute():
        try:
            p = p.resolve().relative_to(store.root)
        except ValueError:
            pass
    return p.as_posix().lstrip("./") or "."


def _brief(store: Store, d: Decision) -> dict[str, Any]:
    last = next((e for e in reversed(store.evidence(d.id)) if e["context"] != "assumption"), None)
    out: dict[str, Any] = {
        "id": d.id,
        "title": d.title,
        "rule": d.rule,
        "reason": d.reason,
        "status": d.status if d.status != "proposed" else "proposed (not yet enforced)",
        "source": {"type": d.source.type, "ref": d.source.ref, "quote": d.source.quote, "link": d.source.link},
        "guard": d.guard.path if d.guard else None,
        "last_result": last["result"] if last else None,
    }
    if d.assumption:
        out["assumption"] = {"claim": d.assumption.claim, "verdict": d.assumption.verdict}
    return out


def _consult(store: Store, tool: str, args: dict[str, Any], ids: list[str]) -> None:
    """Log what agents asked, so the dashboard can show prevention as well as enforcement."""
    rec = {"ts": now(), "tool": tool, "args": args, "decisions": ids, "agent": os.environ.get("WHY_AGENT", "agent")}
    with open(store.dir / "consults.jsonl", "a") as f:
        f.write(json.dumps(rec) + "\n")


# ---- tool implementations (plain functions, so they are testable without a transport) ----------


def decisions_for(store: Store, path: str, line: int | None = None) -> dict[str, Any]:
    rel = _rel(store, path)
    target = f"{rel}:{line}" if line else rel
    found = [d for d in blame(store, target) if d.status in SHOWN]
    if rel in (".", "") or (store.root / rel).is_dir():
        prefix = "" if rel in (".", "") else rel.rstrip("/") + "/"
        found += [d for d in store.decisions(SHOWN) if d not in found and
                  any(f.startswith(prefix) for f in d.scope.get("files", []))]
    found.sort(key=lambda d: (d.status != "violated", d.status != "active", d.id))
    _consult(store, "why_decisions_for", {"path": rel, "line": line}, [d.id for d in found])
    if not found:
        return {"path": target, "decisions": [],
                "guidance": "No recorded decisions govern this code. If you make a lasting choice here, "
                            "record it with why_propose_decision."}
    return {
        "path": target,
        "decisions": [_brief(store, d) for d in found],
        "guidance": "Keep every active rule above; each has a guard that will fail the PR. If one must change, "
                    "stop and tell the human why, and record the new rule with why_propose_decision.",
    }


def propose_decision(store: Store, rule: str, reason: str, title: str | None = None,
                     files: list[str] | None = None, agent: str | None = None) -> dict[str, Any]:
    rule, reason = rule.strip(), reason.strip()
    if not rule or not reason:
        return {"ok": False, "message": "Both rule and reason are required: a decision without a reason is a guess."}
    for d in store.decisions(("proposed", "active", "violated")):
        if _similar(rule, d.rule):
            _consult(store, "why_propose_decision", {"rule": rule}, [d.id])
            return {"ok": True, "duplicate_of": d.id, "decision": _brief(store, d),
                    "message": f"Already recorded as {d.id}."}
    index = index_for(store)
    kind, params, funcs = compile_rule(index, rule, f"{rule}. {reason}")
    files = sorted({*(files or []), *(f.file for f in funcs)})
    who = agent or os.environ.get("WHY_AGENT", "agent")
    d = Decision(id="", title=(title or rule)[:72], rule=rule, reason=reason, kind=kind, params=params,
                 scope={"files": files, "symbols": [f.ref for f in funcs]},
                 source=Source(type="agent_mcp", ref=who, quote=f"{rule}: {reason}", author=who, date=now()),
                 confidence=0.7)
    store.add(d, actor=f"{who} via MCP")
    _consult(store, "why_propose_decision", {"rule": rule}, [d.id])
    return {"ok": True, "decision": _brief(store, d),
            "message": f"Recorded as {d.id} (proposed). A human approves it with `why approve {d.id}`; "
                       "only then does its guard start failing builds."}


def run_guards(store: Store, diff: str = "") -> dict[str, Any]:
    try:
        report = check(store, context="mcp", patch=diff or None, apply_transitions=False,
                       extra={"agent": os.environ.get("WHY_AGENT", "agent"), "with_diff": bool(diff)})
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    _consult(store, "why_run_guards", {"diff_lines": len(diff.splitlines())},
             [i.decision_id for i in report.violations])
    if not report.items:
        return {"ok": True, "message": "No active decisions yet; nothing to check."}
    violations = []
    for i in report.violations:
        d = store.get(i.decision_id)
        first = next((ln.strip() for ln in i.log.splitlines() if "Violates" in ln or "Error" in ln), "")
        violations.append({"id": d.id, "title": d.title, "rule": d.rule, "reason": d.reason,
                           "source": {"type": d.source.type, "ref": d.source.ref, "quote": d.source.quote},
                           "failure": first[:400] or i.log[-400:]})
    return {
        "ok": report.ok,
        "checked": len(report.items),
        "violations": violations,
        "passed": [i.decision_id for i in report.items if i.result == "pass"],
        "message": ("All decisions hold." if report.ok else
                    f"{len(violations)} decision(s) would be violated. Change the code to keep them, or ask the "
                    "human whether the decision should be superseded."),
    }


# ---- MCP wiring ----------------------------------------------------------------------------


def build_server(store: Store, read_only: bool = False):
    """read_only: only why_decisions_for (for unauthenticated remote access; the other tools write or run code)."""
    from mcp.server.mcpserver import MCPServer

    server = MCPServer(name="why", title="Why", instructions=INSTRUCTIONS,
                       description="Decisions behind this codebase, with executable guards.")

    @server.tool(title="Decisions for a file")
    def why_decisions_for(path: str, line: int | None = None) -> dict[str, Any]:
        """Call before editing a file. Returns the decisions that govern `path` (or one `line` in it):
        the rule, why it was made, where it came from, and whether its guard is passing."""
        return decisions_for(store, path, line)

    if read_only:
        return server

    @server.tool(title="Propose a decision")
    def why_propose_decision(rule: str, reason: str, title: str | None = None,
                             files: list[str] | None = None) -> dict[str, Any]:
        """Record a lasting choice as you make it, e.g. rule="Never retry charge()",
        reason="the gateway double-bills on retry". It is stored as proposed until a human approves it."""
        return propose_decision(store, rule, reason, title, files)

    @server.tool(title="Run guards")
    def why_run_guards(diff: str = "") -> dict[str, Any]:
        """Self-check before a PR: runs every active guard in a sandbox against the current working tree,
        plus `diff` (a unified diff) if given. Your files are not modified."""
        return run_guards(store, diff)

    return server


def serve_stdio(store: Store) -> None:
    build_server(store).run("stdio")


def http_app(store: Store):
    """Remote MCP (streamable HTTP) for the hosted server, mounted at /mcp.

    Without WHY_MCP_TOKEN it is a public, read-only feed (why_decisions_for). With a token, every
    request must send `Authorization: Bearer <token>` and all three tools are available.
    """
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.responses import JSONResponse

    token = os.environ.get("WHY_MCP_TOKEN", "")
    server = build_server(store, read_only=not token)
    app = server.streamable_http_app(streamable_http_path="/", stateless_http=True, json_response=True,
                                     host=os.environ.get("WHY_PUBLIC_HOST", "0.0.0.0"))

    if token:
        import hmac

        class BearerAuth(BaseHTTPMiddleware):
            async def dispatch(self, request, call_next):
                got = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
                if not hmac.compare_digest(got, token):
                    return JSONResponse({"error": "missing or wrong bearer token"}, status_code=401)
                return await call_next(request)

        app.add_middleware(BearerAuth)
    return app
