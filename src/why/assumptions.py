"""External assumption check (F9): decisions that rely on outside facts get re-verified with Tavily.

A decision like "batch SMS sends to 100/min: Fernwave's API rate limit is 100 requests per
minute" is only right while the vendor's limit still is. Why searches for the current fact and
asks a model (or a numeric heuristic, offline) whether the claim still holds.
"""

from __future__ import annotations

import os
import re
from typing import Any

import httpx

from . import llm
from .models import Evidence, now
from .store import Store

TAVILY_URL = "https://api.tavily.com/search"


def tavily_key() -> str | None:
    return os.environ.get("TAVILY_API_KEY")


def search(query: str, max_results: int = 5) -> dict[str, Any]:
    key = tavily_key()
    if not key:
        raise RuntimeError("TAVILY_API_KEY is not set")
    r = httpx.post(
        TAVILY_URL,
        headers={"Authorization": f"Bearer {key}"},
        json={"query": query, "search_depth": "advanced", "include_answer": True, "max_results": max_results},
        timeout=60,
    )
    r.raise_for_status()
    return r.json()


NUM_RE = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(%|requests?|req|calls?|messages?|per|/|rps|qps|ms|seconds?|minutes?|"
                    r"hours?|days?|usd|\$|mb|gb|kb)", re.I)


def _numbers(text: str) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for n, unit in NUM_RE.findall(text):
        u = unit.lower().rstrip("s")
        out.setdefault(u, set()).add(n.replace(",", ""))
    return out


CHANGE_RE = re.compile(r"\b(lowered|reduced|raised|increased|changed|no longer|deprecated|now (?:limited|capped|"
                       r"requires|only)|is now|are now|effective|as of)\b", re.I)


def _u(unit: str) -> str:
    return unit + "s" if unit.isalpha() and len(unit) > 2 else unit


def heuristic_verdict(claim: str, evidence: str) -> tuple[str, str]:
    """Offline judge: compare the figures the claim depends on with the figures in the search results."""
    cn, en = _numbers(claim), _numbers(evidence)
    shared = sorted(set(cn) & set(en))
    if not shared:
        return "unclear", "No comparable figures found in the search results."
    for unit in shared:
        same, other = cn[unit] & en[unit], en[unit] - cn[unit]
        claimed = sorted(cn[unit])[0]
        if other and (not same or CHANGE_RE.search(evidence)):
            return "broken", (f"The decision assumes {claimed} {_u(unit)}, but current sources report "
                              f"{', '.join(sorted(other))} {_u(unit)}.")
    unit = shared[0]
    if sorted(cn[unit] & en[unit]):
        return "holds", f"Current sources still state {sorted(cn[unit] & en[unit])[0]} {_u(unit)}."
    return "unclear", "Search results are inconclusive."


JUDGE_SYSTEM = """You check whether an engineering decision's assumption about the outside world still holds.
Given the assumption and fresh web search results, answer with JSON:
{"verdict": "holds"|"broken"|"unclear", "explanation": "<one or two sentences citing the figure>"}"""


def judge(claim: str, results: dict[str, Any]) -> tuple[str, str]:
    snippets = "\n".join(f"- {r.get('title')}: {r.get('content', '')[:500]} ({r.get('url')})"
                         for r in results.get("results", []))
    evidence = f"{results.get('answer') or ''}\n{snippets}"
    if llm.available():
        try:
            out = llm.chat_json("nano", JUDGE_SYSTEM, f"ASSUMPTION: {claim}\n\nSEARCH RESULTS:\n{evidence}")
            v = str(out.get("verdict", "unclear"))
            if v in ("holds", "broken", "unclear"):
                return v, str(out.get("explanation", ""))
        except (llm.LLMError, AttributeError):
            pass
    return heuristic_verdict(claim, evidence)


def check_assumptions(store: Store, decision_ids: list[str] | None = None,
                      search_fn=search) -> list[dict[str, Any]]:
    out = []
    for d in store.decisions(("proposed", "active", "violated")):
        if not d.assumption or (decision_ids and d.id not in decision_ids):
            continue
        a = d.assumption
        query = a.query or a.claim
        try:
            results = search_fn(query)
        except Exception as e:  # noqa: BLE001
            out.append({"decision_id": d.id, "verdict": "unchecked", "explanation": str(e)})
            continue
        verdict, explanation = judge(a.claim, results)
        a.verdict, a.explanation, a.checked_at = verdict, explanation, now()
        a.urls = [r.get("url", "") for r in results.get("results", [])][:5]
        store.save(d)
        store.add_evidence(Evidence(d.id, {"holds": "pass", "broken": "fail"}.get(verdict, "skipped"), "assumption",
                                    log=explanation, detail={"claim": a.claim, "urls": a.urls}))
        if verdict == "broken":
            store.log_event(d.id, d.status, d.status, "why assume", note=f"assumption broken: {explanation}")
        out.append({"decision_id": d.id, "title": d.title, "claim": a.claim, "verdict": verdict,
                    "explanation": explanation, "urls": a.urls})
    store.save_report("assumptions", now().replace(":", ""), {"created_at": now(), "results": out})
    return out
