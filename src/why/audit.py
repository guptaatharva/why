"""Audit export: what the system was supposed to do, who decided, and the evidence it kept doing it.

    why audit                       JSON to stdout
    why audit --format csv  --out decisions.csv
    why audit --format html --out audit.html     a self-contained report for reviewers
"""

from __future__ import annotations

import csv
import html
import io
import json
from collections import Counter
from typing import Any

from .gitutil import github_slug, head
from .models import now
from .store import Store


def build(store: Store, since: str | None = None) -> dict[str, Any]:
    evidence = store.evidence()
    log = store.log()
    if since:
        evidence = [e for e in evidence if e["timestamp"] >= since]
        log = [r for r in log if r["ts"] >= since]
    rows = []
    for d in store.decisions():
        ev = [e for e in evidence if e["decision_id"] == d.id]
        runs = [e for e in ev if e["context"] not in ("assumption",)]
        fails = [e for e in runs if e["result"] in ("fail", "error")]
        rows.append({
            "id": d.id, "title": d.title, "rule": d.rule, "reason": d.reason, "status": d.status,
            "kind": d.kind,
            "source": {"type": d.source.type, "ref": d.source.ref, "quote": d.source.quote, "link": d.source.link,
                       "author": d.source.author, "date": d.source.date},
            "guard": {"path": d.guard.path, "generated_by": d.guard.generated_by, "framework": d.guard.framework,
                      "flaky": d.guard.flaky} if d.guard else None,
            "supersedes": d.supersedes, "superseded_by": d.superseded_by,
            "assumption": {"claim": d.assumption.claim, "verdict": d.assumption.verdict,
                           "checked_at": d.assumption.checked_at, "urls": d.assumption.urls} if d.assumption else None,
            "created_at": d.created_at,
            "history": [{"at": r["ts"], "from": r["from"], "to": r["to"], "by": r["actor"], "note": r["note"]}
                        for r in log if r["decision_id"] == d.id],
            "evidence": {
                "runs": len(runs),
                "by_context": dict(Counter(e["context"] for e in runs)),
                "by_result": dict(Counter(e["result"] for e in runs)),
                "last": ({k: runs[-1][k] for k in ("timestamp", "result", "context", "commit")} if runs else None),
                "last_failure": ({k: fails[-1][k] for k in ("timestamp", "context", "commit")} |
                                 {"log": fails[-1]["log"][-600:]} if fails else None),
            },
        })
    return {
        "repo": {"root": store.root.name, "github": github_slug(store.root), "head": head(store.root)},
        "generated_at": now(), "since": since,
        "summary": {"decisions": len(rows), "by_status": dict(Counter(r["status"] for r in rows)),
                    "guard_runs": sum(r["evidence"]["runs"] for r in rows),
                    "violations_recorded": sum(r["evidence"]["by_result"].get("fail", 0) for r in rows)},
        "decisions": rows,
    }


def to_csv(data: dict[str, Any]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["id", "status", "title", "rule", "reason", "source_type", "source_ref", "source_link", "decided_by",
                "decided_at", "guard", "guard_generated_by", "runs", "passes", "failures", "last_result",
                "last_run_at", "last_failure_at", "assumption_verdict", "superseded_by"])
    for r in data["decisions"]:
        ev = r["evidence"]
        w.writerow([r["id"], r["status"], r["title"], r["rule"], r["reason"], r["source"]["type"], r["source"]["ref"],
                    r["source"]["link"], r["source"]["author"], r["source"]["date"] or r["created_at"],
                    (r["guard"] or {}).get("path", ""), (r["guard"] or {}).get("generated_by", ""), ev["runs"],
                    ev["by_result"].get("pass", 0), ev["by_result"].get("fail", 0) + ev["by_result"].get("error", 0),
                    (ev["last"] or {}).get("result", ""), (ev["last"] or {}).get("timestamp", ""),
                    (ev["last_failure"] or {}).get("timestamp", ""), (r["assumption"] or {}).get("verdict", ""),
                    r["superseded_by"] or ""])
    return buf.getvalue()


def to_html(data: dict[str, Any]) -> str:
    e = html.escape
    color = {"active": "#1a7f37", "violated": "#cf222e", "proposed": "#9a6700", "superseded": "#8250df",
             "retired": "#6e7781"}
    cards = []
    for r in data["decisions"]:
        ev = r["evidence"]
        hist = "".join(f"<li><time>{e(h['at'])}</time> {e(h['from'] or '∅')} → <b>{e(h['to'])}</b> "
                       f"<span class=m>by {e(h['by'])}</span> {e(h['note'] or '')}</li>" for h in r["history"])
        fail = ""
        if ev["last_failure"]:
            f = ev["last_failure"]
            fail = (f"<details><summary>Last failure {e(f['timestamp'])} ({e(f['context'])} "
                    f"{e(f['commit'][:10])})</summary><pre>{e(f['log'])}</pre></details>")
        src = r["source"]
        link = f' · <a href="{e(src["link"])}">source</a>' if src["link"].startswith("http") else ""
        cards.append(f"""<section>
<h2><code>{e(r['id'])}</code> {e(r['title'])} <span class=s style="color:{color.get(r['status'], '#333')}">{e(r['status'])}</span></h2>
<p><b>Rule:</b> {e(r['rule'])}<br><b>Why:</b> {e(r['reason'] or '—')}</p>
<p class=m>Decided in {e(src['type'])} {e(src['ref'][:12])} by {e(src['author'] or 'unknown')} {e(src['date'] or '')}{link}</p>
{f'<blockquote>{e(src["quote"])}</blockquote>' if src['quote'] else ''}
<p><b>Guard:</b> {e((r['guard'] or {}).get('path', 'none'))} {e('(' + r['guard']['generated_by'] + ')') if r['guard'] else ''}
 · <b>Runs:</b> {ev['runs']} ({ev['by_result'].get('pass', 0)} pass, {ev['by_result'].get('fail', 0)} fail)
 · <b>Last:</b> {e((ev['last'] or {}).get('result', '—'))} {e((ev['last'] or {}).get('timestamp', ''))}</p>
{fail}<ol class=h>{hist}</ol></section>""")
    s = data["summary"]
    status = " · ".join(f"{v} {k}" for k, v in sorted(s["by_status"].items()))
    return f"""<!doctype html><html lang=en><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>Why audit · {e(data['repo']['root'])}</title>
<style>body{{font:15px/1.55 system-ui,sans-serif;max-width:900px;margin:40px auto;padding:0 16px;color:#1f2328}}
h1{{margin-bottom:4px}}section{{border:1px solid #d0d7de;border-radius:8px;padding:14px 18px;margin:16px 0}}
h2{{font-size:17px;margin:0 0 8px}}.s{{font-size:12px;text-transform:uppercase;margin-left:6px}}.m{{color:#656d76;font-size:13px}}
blockquote{{margin:8px 0;padding-left:12px;border-left:3px solid #d0d7de;color:#424a53}}pre{{white-space:pre-wrap;font-size:12px;background:#f6f8fa;padding:8px}}
ol.h{{font-size:13px;color:#424a53;padding-left:18px}}time{{font-family:ui-monospace,monospace;font-size:12px}}
@media (prefers-color-scheme:dark){{body{{background:#0d1117;color:#e6edf3}}section{{border-color:#30363d}}pre{{background:#161b22}}}}</style>
<h1>Decision audit: {e(data['repo']['root'])}</h1>
<p class=m>Generated {e(data['generated_at'])} at {e((data['repo']['head'] or '')[:10])}{' · since ' + e(data['since']) if data['since'] else ''}
 · {s['decisions']} decisions ({e(status)}) · {s['guard_runs']} guard runs · {s['violations_recorded']} violations recorded</p>
{''.join(cards)}</html>"""


def export(store: Store, fmt: str = "json", since: str | None = None) -> str:
    data = build(store, since)
    if fmt == "csv":
        return to_csv(data)
    if fmt == "html":
        return to_html(data)
    return json.dumps(data, indent=2)
