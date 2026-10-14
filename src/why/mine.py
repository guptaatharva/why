"""Decision mining (F1): read history, propose decisions with source links.

Two passes:
  1. A cheap rule-based scan finds candidate sentences ("never", "always", "must"...)
     and, where it can, compiles them into a structured guard kind.
  2. If a model is available, Nemotron Nano reads the candidate snippets and returns
     structured decisions. Its output is validated against the code index.

Nothing mined is ever activated automatically: every decision starts as `proposed`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from . import llm
from .codeindex import CodeIndex, Func
from .models import Assumption, Decision, Source
from .sources import Snippet, collect
from .store import Store

RULE_RE = re.compile(
    r"\b(never|always|must not|mustn't|must|do not|don't|should never|shouldn't ever|at most once|"
    r"only once|exactly once|no retr(?:y|ies)|not allowed|forbidden)\b",
    re.I,
)
REASON_RE = re.compile(r"\b(because|since|otherwise|or else|so that|due to|as the|the reason)\b|[:—–]| - ", re.I)
SYMBOL_RE = re.compile(r"`([A-Za-z_][\w.]*)(\([^`]*\))?`")
CALL_EXAMPLE_RE = re.compile(r"`([A-Za-z_][\w.]*\([^`]*\))`")
ASSUMPTION_RE = re.compile(
    r"\b(rate limit|requests? (?:per|/) ?(?:second|minute|hour|day)|\d+\s*(?:req|rps|qps)|pricing|"
    r"quota|per[- ]minute|vendor|provider|the [\w-]+ API (?:allows|caps|limits))\b",
    re.I,
)
EXC_HINTS = [
    (re.compile(r"time ?out", re.I), "TimeoutError"),
    (re.compile(r"connection|network", re.I), "ConnectionError"),
]


@dataclass
class Candidate:
    snippet: Snippet
    sentence: str
    context: str


# ---- sentence-level scanning ---------------------------------------------------


def _sentences(text: str) -> list[str]:
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    parts: list[str] = []
    for para in re.split(r"\n\s*\n|\n[-*] ", text):
        para = " ".join(para.split())
        parts += [s.strip() for s in re.split(r"(?<=[.!?])\s+(?=[A-Z`])", para) if s.strip()]
    return parts


def candidates(snippets: Iterable[Snippet]) -> list[Candidate]:
    out = []
    for sn in snippets:
        sents = _sentences(sn.text)
        for i, s in enumerate(sents):
            if not RULE_RE.search(s) or len(s) < 15 or len(s) > 400:
                continue
            ctx = " ".join(sents[max(0, i - 1) : i + 2])
            out.append(Candidate(sn, s, ctx))
    return out


# ---- rule-based compilation ------------------------------------------------------


SEPARATORS = (": ", " because ", " since ", " — ", " – ", " so that ", " otherwise ")


def split_rule(sentence: str, context: str) -> tuple[str, str]:
    """Split "Never retry X: the gateway double-bills" into (rule, reason)."""
    s = sentence.strip()
    cuts = sorted((s.find(sep), sep) for sep in SEPARATORS if s.find(sep) > 0)
    for idx, sep in cuts:
        head, tail = s[:idx], s[idx + len(sep):]
        if RULE_RE.search(head) and len(tail) > 8:
            if sep.strip() in ("because", "since", "so that", "otherwise"):
                tail = f"{sep.strip()} {tail}"
            return head.strip(" .,;"), tail.strip(" .,;")
    rest = context.replace(sentence, "").strip()
    return s.strip(" .,;"), (rest[:240] if REASON_RE.search(rest) else "")


def _title(rule: str) -> str:
    # `parse_date('2026-03-01T10:00:00+05:30')` reads as noise in a title: keep the function, drop the example.
    t = re.sub(r"`([A-Za-z_][\w.]*)\([^`]+\)`", r"`\1()`", rule)
    t = re.sub(r"^(we|you|agents?|the code|code)\s+(should|must|will)\s+", "", t, flags=re.I)
    t = t[0].upper() + t[1:] if t else t
    return t if len(t) <= 72 else t[:69].rsplit(" ", 1)[0] + "…"


def _exception_for(text: str) -> str:
    for rx, exc in EXC_HINTS:
        if rx.search(text):
            return exc
    return "Exception"


def _resolve(index: CodeIndex, text: str) -> list[Func]:
    found: list[Func] = []
    for m in SYMBOL_RE.finditer(text):
        for f in index.find(m.group(1)):
            if f not in found:
                found.append(f)
    return found


# "returns" predicates, per language of the function they constrain: (pattern in the rule, python, js)
PREDICATES: list[tuple[str, str, str]] = [
    (r"\butc\b|timezone-aware|tz-aware",
     "result.tzinfo is not None and result.utcoffset().total_seconds() == 0",
     "typeof result === 'string' ? /(Z|[+-]00:?00)$/.test(result) : result instanceof Date && !isNaN(result.getTime())"),
    (r"never returns? (none|null|undefined)|not (be )?(none|null)",
     "result is not None", "result !== null && result !== undefined"),
    (r"\bsorted\b",
     "list(result) == sorted(result)",
     "JSON.stringify(result) === JSON.stringify([...result].sort((a, b) => (a > b ? 1 : a < b ? -1 : 0)))"),
    (r"non-?negative|never negative", "result >= 0", "result >= 0"),
    (r"\binteger\b|\bwhole (number|cents)\b|never floats?",
     "isinstance(result, int) and not isinstance(result, bool)", "Number.isInteger(result)"),
    (r"lower ?case", "result == result.lower()", "result === result.toLowerCase()"),
]


def _predicate(rule_text: str, lang: str) -> str | None:
    for rx, py, js in PREDICATES:
        if re.search(rx, rule_text):
            return js if lang == "js" else py
    return None


def compile_rule(index: CodeIndex, sentence: str, context: str) -> tuple[str, dict[str, Any], list[Func]]:
    """Return (kind, params, resolved functions) for a rule sentence."""
    funcs = _resolve(index, sentence) or _resolve(index, context)
    s = sentence.lower()

    # "never retry `charge()`", "call `charge()` at most once"
    if funcs and re.search(r"retr(y|ies|ied)|at most once|only once|exactly once|more than once|twice", s):
        target = funcs[0]
        callers = index.callers(target)
        if target.lang == "js":
            # module mocks only intercept calls that cross a module boundary
            callers = [c for c in callers if c.exported and c.module != target.module]
        if callers:
            entry = callers[0]
            return "call_count", {
                "target": target.ref,
                "entry": entry.ref,
                "raise": _exception_for(context),
                "max_calls": 1,
            }, [target, entry]

    # "`parse_date('…')` always returns UTC"
    ex = CALL_EXAMPLE_RE.search(sentence) or CALL_EXAMPLE_RE.search(context)
    if ex and re.search(r"returns?|return value|result|\bin\b|\bas\b|always|never", s):
        call = ex.group(1)
        fname = call.split("(")[0]
        hits = [h for h in index.find(fname) if h.exported]
        pred = _predicate(s, hits[0].lang) if hits else None
        if hits and pred:
            f = hits[0]
            args = call[len(fname.split(".")[-1]) + (len(fname) - len(fname.split(".")[-1])):]
            return "returns", {"target": f.ref, "call": f"{f.name}{args}", "predicate": pred}, [f]

    # "never import `requests` in …", "never call `time.sleep`", "don't use `eval`"
    m = re.search(r"\b(?:never|don't|do not|must not|should never)\s+(import|use|call|print|log)\b", s)
    sym = SYMBOL_RE.search(sentence)
    if m and sym:
        name = sym.group(1)
        verb = m.group(1)
        if verb == "import":
            n = re.escape(name)
            pattern = (rf"^\s*(import\s+{n}\b|from\s+{n}\b)|from\s+['\"]{n}['\"]|require\(['\"]{n}['\"]\)|"
                       rf"import\(['\"]{n}['\"]\)")
        else:
            pattern = rf"\b{re.escape(name)}\s*\("
        paths = re.findall(r"`?([\w/]+/)`?", sentence)
        glob = "*.py" if index.language == "python" else "*"
        return "forbid_pattern", {"pattern": pattern, "paths": paths or ["."], "glob": glob}, funcs

    return "custom", {}, funcs


HEDGE_RE = re.compile(r"\b(probably|maybe|perhaps|lol|haha|i guess|kinda|might want)\b", re.I)


def _confidence(sn: Snippet, reason: str, kind: str, funcs: list[Func], text: str = "",
                grounded: bool = True) -> float:
    c = 0.35
    # A rule from a chat that names nothing in the repo is usually process talk, not a code decision.
    c -= 0.25 if not funcs and not grounded and sn.type in ("agent_log", "entire_checkpoint") else 0
    c -= 0.2 if HEDGE_RE.search(text) else 0
    c += 0.2 if reason else 0
    c += 0.15 if funcs else 0
    c += 0.15 if kind != "custom" else 0
    c += {"pr_comment": 0.1, "pr_body": 0.1, "agent_log": 0.1, "entire_checkpoint": 0.1, "commit": 0.05}.get(sn.type, 0)
    return round(min(c, 0.98), 2)


def _assumption(text: str) -> Assumption | None:
    if ASSUMPTION_RE.search(text):
        return Assumption(claim=text.strip()[:300], query=text.strip()[:200])
    return None


def heuristic(index: CodeIndex, cands: list[Candidate]) -> list[Decision]:
    out = []
    for c in cands:
        rule, reason = split_rule(c.sentence, c.context)
        kind, params, funcs = compile_rule(index, c.sentence, c.context)
        sn = c.snippet
        out.append(
            Decision(
                id="",
                title=_title(rule),
                rule=rule,
                reason=reason,
                kind=kind,
                params=params,
                scope={"files": sorted({f.file for f in funcs}), "symbols": [f.ref for f in funcs]},
                source=Source(type=sn.type, ref=sn.ref, quote=c.sentence, link=sn.link, author=sn.author, date=sn.date),
                confidence=_confidence(sn, reason, kind, funcs, c.sentence, index.mentions(c.context)),
                assumption=_assumption(c.sentence),
            )
        )
    return out


# ---- model pass (Nemotron Nano) ------------------------------------------------------

NANO_SYSTEM = """You extract engineering DECISIONS from a software repo's history.
A decision is a rule the code must keep, with a reason (e.g. "Never retry charge(): the gateway double-bills").
Ignore TODOs, style nits, temporary notes and anything without a lasting rule.
For each decision return an object:
  {"snippet": <index of the snippet>, "quote": <exact sentence from the snippet>,
   "title": <short imperative title>, "rule": <the rule>, "reason": <why, from the text, or "">,
   "symbols": [<function names it governs, as they appear in the code index>],
   "kind": "call_count"|"returns"|"forbid_pattern"|"custom",
   "params": {...}, "assumption": <outside fact it relies on (vendor limit, pricing) or null>}
params by kind:
  call_count: {"target": "module:func", "entry": "module:caller", "raise": "TimeoutError", "max_calls": 1}
  returns: {"target": "module:func", "call": "func(<literal args>)", "predicate": <python expr on `result`>}
  forbid_pattern: {"pattern": <python regex>, "paths": ["dir/"], "glob": "*.py"}
Return a JSON list (possibly empty)."""


def model_pass(index: CodeIndex, cands: list[Candidate], batch: int = 12) -> list[Decision]:
    by_snippet: dict[int, Snippet] = {}
    order: list[Snippet] = []
    for c in cands:
        if id(c.snippet) not in by_snippet:
            by_snippet[id(c.snippet)] = c.snippet
            order.append(c.snippet)
    out: list[Decision] = []
    for start in range(0, len(order), batch):
        chunk = order[start : start + batch]
        listing = "\n\n".join(f"[{i}] ({s.type} {s.ref})\n{s.text[:2500]}" for i, s in enumerate(chunk))
        user = f"CODE INDEX:\n{index.summary(200)}\n\nSNIPPETS:\n{listing}"
        try:
            items = llm.chat_json("nano", NANO_SYSTEM, user, max_tokens=4000)
        except llm.LLMError:
            continue
        for it in items if isinstance(items, list) else []:
            try:
                sn = chunk[int(it.get("snippet", 0))]
            except (ValueError, IndexError, TypeError):
                continue
            quote = " ".join(str(it.get("quote") or "").split())
            if not quote or quote.strip("`.\"' ").lower() not in " ".join(sn.text.split()).lower():
                continue  # every decision must be grounded in a real quote from its source
            funcs: list[Func] = []
            for s in it.get("symbols") or []:
                funcs += [f for f in index.find(str(s)) if f not in funcs]
            kind = it.get("kind") if it.get("kind") in ("call_count", "returns", "forbid_pattern") else "custom"
            params = it.get("params") if isinstance(it.get("params"), dict) else {}
            if kind in ("call_count", "returns") and not index.find(str(params.get("target", ""))):
                kind, params = "custom", {}
            reason = str(it.get("reason") or "")
            assumption = it.get("assumption")
            out.append(
                Decision(
                    id="",
                    title=_title(str(it.get("title") or it.get("rule") or "")),
                    rule=str(it.get("rule") or it.get("title") or ""),
                    reason=reason,
                    kind=kind,
                    params=params,
                    scope={"files": sorted({f.file for f in funcs}), "symbols": [f.ref for f in funcs]},
                    source=Source(type=sn.type, ref=sn.ref, quote=str(it.get("quote") or "")[:400], link=sn.link,
                                  author=sn.author, date=sn.date),
                    confidence=_confidence(sn, reason, kind, funcs, str(it.get("quote") or ""),
                                           index.mentions(str(it.get("quote") or "") + " " + str(it.get("rule") or ""))),
                    assumption=Assumption(claim=str(assumption), query=str(assumption)) if assumption else None,
                )
            )
    return out


# ---- dedup + ranking ---------------------------------------------------------------


def _tokens(s: str) -> set[str]:
    return set(re.findall(r"[a-z_]{3,}", s.lower()))


def _similar(a: str, b: str) -> bool:
    ta, tb = _tokens(a), _tokens(b)
    return bool(ta and tb) and len(ta & tb) / len(ta | tb) >= 0.6


def dedupe(found: list[Decision], existing: list[Decision], max_per_source: int, min_conf: float) -> list[Decision]:
    kept: list[Decision] = []
    per_source: dict[str, int] = {}
    for d in sorted(found, key=lambda d: d.confidence, reverse=True):
        if d.confidence < min_conf or not d.rule:
            continue
        if any(_similar(d.rule, e.rule) for e in existing + kept):
            continue
        key = f"{d.source.type}:{d.source.ref}"
        if per_source.get(key, 0) >= max_per_source:
            continue
        per_source[key] = per_source.get(key, 0) + 1
        kept.append(d)
    return kept


def mine(store: Store, days: int | None = None, use_model: bool | None = None, github: bool = True,
         dry_run: bool = False) -> list[Decision]:
    cfg = store.config
    mcfg = cfg.get("mining", {})
    days = days or mcfg.get("days", 90)
    index = CodeIndex(store.root, cfg.get("python_path"), exclude=[cfg["guards_dir"]], language=cfg["language"])
    snippets = list(collect(store.root, cfg, days, github=github))
    cands = candidates(snippets)
    use_model = llm.available() if use_model is None else use_model
    found = model_pass(index, cands) if use_model and cands else []
    found += heuristic(index, cands)  # rule-based results fill gaps and act as fallback
    new = dedupe(found, store.decisions(), mcfg.get("max_per_source", 3), mcfg.get("min_confidence", 0.45))
    if not dry_run:
        for d in new:
            store.add(d, actor="why mine")
    return new


def index_for(store: Store) -> CodeIndex:
    cfg = store.config
    return CodeIndex(store.root, cfg.get("python_path"), exclude=[cfg["guards_dir"]], language=cfg["language"])


__all__ = ["mine", "candidates", "heuristic", "compile_rule", "index_for", "Path"]
