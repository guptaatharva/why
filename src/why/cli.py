"""`why` command line."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from . import __version__, llm
from .models import Decision, Source
from .store import Store, StoreError, find_root

USE_COLOR = sys.stdout.isatty() and not os.environ.get("NO_COLOR")


def c(text: str, code: str) -> str:
    return f"\033[{code}m{text}\033[0m" if USE_COLOR else text


def bold(t: str) -> str:
    return c(t, "1")


def dim(t: str) -> str:
    return c(t, "2")


STATUS_COLOR = {"proposed": "33", "active": "32", "violated": "31", "superseded": "35", "retired": "90"}
RESULT_COLOR = {"pass": "32", "fail": "31", "error": "31", "skipped": "90"}


def status(s: str) -> str:
    return c(s, STATUS_COLOR.get(s, "0"))


def _git_config(st: Store | None, key: str) -> str:
    try:
        return subprocess.run(["git", "config", key], cwd=st.root if st else None, capture_output=True,
                              text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return ""


def actor(st: Store | None = None) -> str:
    """Who is acting, for the decision log: $WHY_ACTOR, git user.name / user.email, else $USER.

    In teams mode the first of these that names a member wins, so permissions follow the person."""
    cands = [os.environ.get("WHY_ACTOR", ""), _git_config(st, "user.name"), _git_config(st, "user.email"),
             os.environ.get("USER", "")]
    cands = [x for x in cands if x]
    if st is not None and st.exists:
        from . import team

        if team.config(st):
            for x in cands:
                if team.resolve(st, x):
                    return team.resolve(st, x) or x
    return cands[0] if cands else "human"


def store_from(args) -> Store:
    return Store(find_root(Path(args.repo) if args.repo else None))


def print_decision_line(d: Decision) -> None:
    guard = "" if d.guard else dim(" (no guard)")
    flaky = c(" flaky", "33") if d.guard and d.guard.flaky else ""
    print(f"  {bold(d.id)}  {status(f'{d.status:<10}')} {d.title}{guard}{flaky}")


# ---- commands --------------------------------------------------------------------


def cmd_init(args) -> int:
    st = Store(find_root(Path(args.repo) if args.repo else None))
    st.init(language=args.language, python_path=args.python_path, sandbox=args.sandbox)
    from .guards import ensure_support

    if st.config["language"] == "python":
        ensure_support(st)
    print(f"Initialised Why in {st.root}/.why")
    print(dim("Next: `why mine` to propose decisions from history."))
    return 0


def cmd_mine(args) -> int:
    from .mine import mine

    st = store_from(args).require()
    using = "Nemotron Nano + rules" if (llm.available() and not args.no_model) else "rule-based miner (no model key)"
    print(dim(f"Mining the last {args.days or st.config['mining']['days']} days with {using}…"))
    new = mine(st, days=args.days, use_model=False if args.no_model else None, github=not args.no_github,
               dry_run=args.dry_run)
    if not new:
        print("No new decisions found.")
        return 0
    print(f"{'Would propose' if args.dry_run else 'Proposed'} {len(new)} decision(s):\n")
    for d in new:
        print(f"  {bold(d.id or '(new)')}  {d.title}")
        print(f"      {dim('reason:')} {d.reason or dim('—')}")
        print(f"      {dim('source:')} {d.source.type} {d.source.ref}  {dim(d.source.link)}")
        print(f"      {dim('quote:')}  \"{d.source.quote}\"")
        print(f"      {dim('guard:')}  {d.kind}  {dim(f'confidence {d.confidence:.2f}')}")
        if d.assumption:
            print(f"      {dim('assumes:')} {d.assumption.claim[:100]}")
        print()
    if not args.dry_run:
        print(dim("Review with `why review`, then `why approve <id>` or `why reject <id>`."))
    return 0


def cmd_list(args) -> int:
    st = store_from(args).require()
    ds = st.decisions(args.status)
    if not ds:
        print("No decisions." + ("" if args.status else " Run `why mine`."))
        return 0
    if args.json:
        print(json.dumps([d.to_dict() for d in ds], indent=2))
        return 0
    for s in ("violated", "active", "proposed", "superseded", "retired"):
        group = [d for d in ds if d.status == s]
        if group:
            print(bold(f"\n{s.capitalize()} ({len(group)})"))
            for d in group:
                print_decision_line(d)
    print()
    return 0


def cmd_show(args) -> int:
    st = store_from(args).require()
    d = st.get(args.id)
    if args.json:
        print(json.dumps(d.to_dict(), indent=2))
        return 0
    print(f"{bold(d.id)}  {status(d.status)}  {bold(d.title)}\n")
    print(f"  Rule:    {d.rule}")
    print(f"  Reason:  {d.reason or dim('—')}")
    print(f"  Source:  {d.source.type} {d.source.ref} {dim(d.source.link)}")
    if d.source.quote:
        print(f"  Quote:   \"{d.source.quote}\"")
    print(f"  Scope:   {', '.join(d.scope.get('symbols') or d.scope.get('files') or ['—'])}")
    print(f"  Guard:   {d.guard.path + ' (' + d.guard.generated_by + ')' if d.guard else dim('none yet')} [{d.kind}]")
    if d.assumption:
        print(f"  Assumes: {d.assumption.claim}  [{d.assumption.verdict}]")
    if d.supersedes:
        print(f"  Supersedes: {d.supersedes}")
    if d.superseded_by:
        print(f"  Superseded by: {d.superseded_by}")
    ev = st.evidence(d.id, limit=5)
    if ev:
        print(bold("\n  Recent evidence"))
        for e in ev:
            print(f"    {e['timestamp']}  {c(e['result'], RESULT_COLOR.get(e['result'], '0')):<6} "
                  f"{e['context']:<10} {e['commit'][:10]}")
    return 0


def cmd_approve(args) -> int:
    from .ops import approve

    st = store_from(args).require()
    ids = [d.id for d in st.decisions("proposed")] if args.all else args.ids
    if not ids:
        print("Nothing to approve.")
        return 0
    code = 0
    for i in ids:
        try:
            res = approve(st, i, actor=actor(st), use_model=False if args.no_model else None)
        except Exception as e:  # noqa: BLE001
            print(f"  {bold(i)}  {c('not approved', '31')}: {e}")
            code = 1
            continue
        mark = c("✓", "32") if res.activated else c("…", "36") if res.pending else c("•", "33")
        print(f"  {mark} {res.message}  {dim(' '.join(res.runs))}")
        if not res.activated and not res.pending:
            code = 1
    return code


def cmd_review(args) -> int:
    from . import team

    st = store_from(args).require()
    me = actor(st)
    rows = team.queue(st, me if args.mine else None)
    if args.json:
        print(json.dumps(rows, indent=2))
        return 0
    t = team.summary(st)
    head_ = f"Review queue · {len(rows)} proposed"
    if t:
        head_ += f" · {t['required_approvals']} approval(s) needed · you are {me} ({team.role(st, me)})"
    print(bold(head_) + "\n")
    if not rows:
        print("  Nothing waiting" + (" on you." if args.mine else "."))
        return 0
    for r in rows:
        conf = f"conf {r['confidence']:.2f}"
        print(f"  {bold(r['id'])}  {r['title'][:70]}  {dim(conf)}")
        print(f"        {dim('rule:')} {r['rule'][:110]}")
        print(f"        {dim('from:')} {r['source']} {r['source_ref'][:12]}")
        if t:
            got = ", ".join(r["approvals"]) or "none"
            print(f"        {dim('approvals:')} {got} ({len(r['approvals'])}/{r['needed']})"
                  + (f"  {dim('owners:')} {', '.join(r['owners'])}" if r["owners"] else "")
                  + (f"  {dim('waiting on:')} {' and '.join(r['waiting_on'])}" if r["waiting_on"] else ""))
            if r["assigned"]:
                print(f"        {dim('assigned:')} {', '.join(r['assigned'])}")
    print(dim("\n  why approve <id> · why reject <id> --note ... · why show <id>"))
    return 0


def cmd_team(args) -> int:
    from . import team

    st = store_from(args).require()
    me = actor(st)
    t = team.summary(st)
    if not t:
        print(f"Solo mode: no `team:` block in .why/config.yaml, so everyone can approve and one approval activates.")
        print(dim(f"You are {me}."))
        return 0
    print(bold(f"Team · {len(t['members'])} member(s) · {t['required_approvals']} approval(s) to activate"))
    for h, r in t["members"].items():
        mark = c(" ← you", "36") if h == me else ""
        print(f"  {h:<16} {r}{mark}")
    if t["owners"]:
        print(bold("\nOwners"))
        for pat, people in t["owners"].items():
            print(f"  {pat:<32} {', '.join(people)}")
    if team.resolve(st, me) is None:
        print(c(f"\nYou ({me}) are not a member: read-only. Set WHY_ACTOR or add an alias.", "33"))
    return 0


def cmd_reject(args) -> int:
    from .ops import reject

    st = store_from(args).require()
    for i in args.ids:
        d = reject(st, i, actor(st), note=args.note or "")
        print(f"  {d.id} rejected")
    return 0


def cmd_add(args) -> int:
    st = store_from(args).require()
    params = json.loads(args.params) if args.params else {}
    d = Decision(id="", title=args.title or args.rule[:72], rule=args.rule, reason=args.reason or "",
                 kind=args.kind, params=params, source=Source(type="manual", author=os.environ.get("USER", "")),
                 confidence=1.0,
                 scope={"files": args.file or [], "symbols": args.symbol or []})
    st.add(d, actor=actor(st))
    print(f"Added {d.id} (proposed). Approve it with `why approve {d.id}`.")
    return 0


def cmd_edit(args) -> int:
    st = store_from(args).require()
    d = st.get(args.id)
    editor = os.environ.get("EDITOR", "vi")
    path = st.dir / "decisions" / f"{d.id}.yaml"
    from . import team

    team.require(st, actor(st), "edit", d)
    subprocess.call([editor, str(path)])
    st.log_event(d.id, d.status, d.status, actor(st), "edited")
    return 0


def cmd_guard(args) -> int:
    from .guards import generate
    from .mine import index_for

    st = store_from(args).require()
    d = st.get(args.id)
    d.guard = generate(st, d, index_for(st), use_model=False if args.no_model else None)
    st.save(d)
    print(f"Wrote {d.guard.path} ({d.guard.generated_by})")
    return 0


def cmd_check(args) -> int:
    from .ops import check

    st = store_from(args).require()
    if args.no_cache:
        os.environ["WHY_NO_CACHE"] = "1"
    report = check(st, context=args.context, code_ref=args.ref, trusted_ref=args.base,
                   apply_transitions=not args.no_transitions)
    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
        return 0 if report.ok else 1
    if not report.items:
        print("No active guards yet. Approve decisions with `why approve`.")
        return 0
    cached = f" · {report.cached} cached" if report.cached else ""
    print(bold(f"Why check · {len(report.items)} guard(s) · {report.commit[:10]} · {report.duration_s}s{cached}\n"))
    for it in report.items:
        print(f"  {c(it.result.upper().ljust(5), RESULT_COLOR.get(it.result, '0'))} {bold(it.decision_id)} {it.title}")
        if it.result != "pass":
            print(f"        {dim('reason:')} {it.reason}")
            print(f"        {dim('source:')} {it.source_ref} {dim(it.source_link)}")
            first = next((ln for ln in it.log.splitlines() if "Violates" in ln or "assert" in ln.lower()),
                         it.log.splitlines()[0] if it.log else "")
            print(f"        {dim(first.strip()[:200])}")
    print()
    if report.ok:
        print(c("All decisions hold.", "32"))
        return 0
    print(c(f"{len(report.violations)} decision(s) violated.", "31") +
          dim(" Fix the code, or `why supersede <id> --reason ...`."))
    return 1


def cmd_log(args) -> int:
    st = store_from(args).require()
    rows = st.log(args.id)
    titles = {d.id: d.title for d in st.decisions()}
    for r in rows[-args.limit:]:
        arrow = f"{r['from'] or '∅'} → {r['to']}" if r["from"] != r["to"] else "note"
        print(f"{dim(r['ts'])}  {bold(r['decision_id'])}  {arrow:<24} {titles.get(r['decision_id'], '')[:50]}"
              f"  {dim(r['actor'])}  {r['note']}")
    return 0


def cmd_blame(args) -> int:
    from .ops import blame

    st = store_from(args).require()
    ds = blame(st, args.target)
    if not ds:
        print(f"No decisions govern {args.target}.")
        return 0
    print(bold(f"Decisions governing {args.target}:\n"))
    for d in ds:
        print_decision_line(d)
        print(f"      {dim('why:')} {d.reason or '—'}")
        print(f"      {dim('from:')} {d.source.type} {d.source.ref} {dim(d.source.link)}")
    return 0


def cmd_upgrade(args) -> int:
    from .upgrade import upgrade

    st = store_from(args).require()
    print(dim(f"Sandboxing {args.spec}: running guards before and after the bump…"))
    rep = upgrade(st, args.spec, find_links=args.find_links, from_version=args.from_version)
    if args.json:
        print(json.dumps(rep.to_dict(), indent=2))
        return 0 if rep.verdict == "approved" else 1
    if rep.error:
        print(c(f"Upgrade check failed to run: {rep.error}", "31"))
        return 2
    head = f"{rep.package} {rep.from_version} → {rep.to_version}"
    print(bold(f"\nUpgrade check · {head} · {rep.guards_run} guard(s)"
               f"{f' + {len(rep.guarantees)} published guarantee(s)' if rep.guarantees else ''}"))
    print(f"  passing before: {rep.passed_before}   passing after: {rep.passed_after}\n")
    for b in rep.breaks:
        print(f"  {c('BREAK', '31')} {bold(b.decision_id)} {b.title}")
        if b.root_cause:
            rc = b.root_cause
            print(f"        {dim('root cause:')} {rc.get('api')}")
            if "before" in rc:
                print(f"        {dim('before:')} {rc['before']}")
                print(f"        {dim('after:')}  {rc['after']}")
        print(f"        {b.explanation}\n")
    if rep.verdict == "blocked":
        print(c(f"Blocked: {len(rep.breaks)} decision(s) break under {rep.package} {rep.to_version}.", "31"))
        return 1
    print(c("Approved: every decision still holds.", "32"))
    return 0


def cmd_assume(args) -> int:
    from .assumptions import check_assumptions, tavily_key

    st = store_from(args).require()
    search_fn = None
    if args.fixture:
        recorded = json.loads(Path(args.fixture).read_text())
        search_fn = lambda q: recorded  # noqa: E731  (recorded Tavily response, for offline demos)
        print(dim(f"Using recorded search results from {args.fixture} (not a live Tavily call)."))
    elif not tavily_key():
        print(c("TAVILY_API_KEY is not set; cannot verify outside assumptions.", "33"))
        return 2
    rows = check_assumptions(st, args.ids or None, **({"search_fn": search_fn} if search_fn else {}))
    if not rows:
        print("No decisions carry outside assumptions.")
    for r in rows:
        col = {"holds": "32", "broken": "31"}.get(r["verdict"], "33")
        print(f"  {c(r['verdict'].upper().ljust(8), col)} {bold(r['decision_id'])} {r.get('title', '')}")
        print(f"        {r['explanation']}")
        for u in r.get("urls", [])[:3]:
            print(f"        {dim(u)}")
    return 1 if any(r["verdict"] == "broken" for r in rows) else 0


def cmd_merge(args) -> int:
    from .merge import merge_check

    st = store_from(args).require()
    rep = merge_check(st, args.branches, base=args.base)
    if args.json:
        print(json.dumps(rep, indent=2))
    elif rep.get("text_conflict"):
        print(c("git reports a textual conflict; resolve it first.", "33"))
        for k in rep.get("id_clashes", []):
            print(f"  {c('ID CLASH', '33')} both sides created {k['decision_id']}: \"{k['rule'][:50]}\" vs "
                  f"\"{k['other_rule'][:50]}\". Renumber one (move .why/decisions/{k['decision_id']}.yaml).")
    elif rep.get("error"):
        print(c(rep["error"], "31"))
    else:
        print(bold(f"Decision merge · {rep['base']} + {' + '.join(rep['branches'])} · {rep['guards_run']} guard(s)\n"))
        for x in rep["contradictions"]:
            who = "base" if x["from"] == "base" else x["from"]
            print(f"  {c('CONTRADICTION', '31')} {bold(x['decision_id'])} {x['title']}")
            print(f"        {dim('decided on:')} {who}   {dim('broken by:')} {', '.join(x['broken_by'])}")
            print(f"        {dim('reason:')} {x['reason']}")
        for k in rep["id_clashes"]:
            print(f"  {c('ID CLASH', '33')} {k['decision_id']} on {k['branch']} (\"{k['rule'][:50]}\") vs "
                  f"\"{k['other_rule'][:50]}\": renumber one side before merging")
        print(c("The branches agree on every decision.", "32") if rep["ok"] else "")
    return 0 if rep.get("ok") else 1


def cmd_supersede(args) -> int:
    from .ops import supersede

    st = store_from(args).require()
    new = supersede(st, args.id, args.reason, args.rule, actor=actor(st))
    print(f"{args.id} superseded by {new.id}" + (f". Approve the new rule with `why approve {new.id}`." if args.rule else "."))
    return 0


def cmd_retire(args) -> int:
    from .ops import retire

    st = store_from(args).require()
    d = retire(st, args.id, actor(st), note=args.note or "")
    print(f"{d.id} retired")
    return 0


def cmd_serve(args) -> int:
    import uvicorn

    from .server.app import create_app

    st = store_from(args)
    app = create_app(local_root=st.root if st.exists else None)
    print(f"Why dashboard on http://{args.host}:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


def cmd_export(args) -> int:
    from .server.api import repo_snapshot

    st = store_from(args).require()
    out = Path(args.out)
    out.write_text(json.dumps(repo_snapshot(st, name=args.name or st.root.name), indent=2))
    print(f"Wrote {out}")
    return 0


def cmd_mcp(args) -> int:
    from .mcp_server import serve_stdio

    st = store_from(args).require()
    serve_stdio(st)  # stdout is the MCP channel: print nothing else
    return 0


def cmd_doctor(args) -> int:
    from .doctor import run

    st = store_from(args)
    icon = {"ok": c("✓", "32"), "warn": c("!", "33"), "off": dim("○"), "fail": c("✗", "31")}
    area = None
    checks = run(st, live=not args.offline)
    for ch in checks:
        if ch.area != area:
            area = ch.area
            print(bold(f"\n{area}"))
        print(f"  {icon[ch.state]} {ch.name:<34} {ch.detail}")
        if ch.fix and ch.state != "ok":
            print(f"      {dim('→ ' + ch.fix)}")
    print()
    return 1 if any(ch.state == "fail" for ch in checks) else 0


def cmd_mcp_install(args) -> int:
    from .doctor import install_mcp

    st = store_from(args).require()
    p = install_mcp(st)
    print(f"Wrote {p}. Claude Code sessions in this repo can now call why_decisions_for, "
          f"why_propose_decision and why_run_guards.")
    return 0


def cmd_onboard(args) -> int:
    """Flow 1 of the PRD: init, mine the last N days, review, generate guards, open one PR."""
    from .mine import mine

    st = store_from(args)
    if not (st.dir / "config.yaml").exists():
        st.init(language=args.language)
        from .guards import ensure_support

        if st.config["language"] == "python":
            ensure_support(st)
        print(f"Initialised Why in {st.root}/.why ({st.config['language']})")
    if args.mcp:
        from .doctor import install_mcp

        install_mcp(st)
    print(dim(f"Mining the last {args.days} days…"))
    new = mine(st, days=args.days, github=not args.no_github)
    proposed = st.decisions("proposed")
    print(f"{len(new)} new decision(s); {len(proposed)} waiting for review.\n")
    for d in proposed:
        print_decision_line(d)
        print(f"      {dim('why:')} {d.reason or '—'}")
        print(f"      {dim('from:')} {d.source.type} {d.source.ref[:12]}  \"{d.source.quote[:90]}\"")
    print(bold("\nNext"))
    print("  why approve <id> …     generate + prove a guard for each decision you agree with")
    print("  why reject <id> …      drop the noise")
    print("  why add \"…\" --reason …  record a decision the history doesn't spell out")
    print("  why pr                 open one PR adding the decisions and their guards")
    return 0


def cmd_pr(args) -> int:
    """Commit .why/ and the guards on a branch and open one PR (via the gh CLI)."""
    from .gitutil import git

    st = store_from(args).require()
    active = st.decisions("active")
    if not active:
        print("No active decisions yet; approve some first.")
        return 1
    paths = [p for p in (".why", st.config["guards_dir"], ".mcp.json") if (st.root / p).exists()]
    status = git(st.root, "status", "--porcelain", "--", *paths).strip()
    if not status:
        print("Nothing to propose: .why/ and the guards are already committed.")
        return 0
    branch = args.branch
    title = f"Why: guard {len(active)} decision{'s' if len(active) != 1 else ''}"
    body = ("Why mined these decisions from this repo's history. Each now has a guard test that fails the build "
            "if the decision is broken.\n\n" +
            "\n".join(f"- **{d.id}** {d.title}: {d.reason or '—'} ({d.source.type} {d.source.ref[:10]})"
                      for d in active) +
            "\n\nReview the rules, not just the code: each one will block future PRs that break it.")
    if args.dry_run:
        print(bold(f"{title}  ({branch})\n"))
        print(status)
        print()
        print(body)
        return 0
    start = git(st.root, "rev-parse", "--abbrev-ref", "HEAD").strip()
    git(st.root, "checkout", "-q", "-b", branch)
    git(st.root, "add", "--", *paths)
    git(st.root, "commit", "-q", "-m", title)
    print(f"Committed on {branch}.")
    if args.no_push:
        return 0
    git(st.root, "push", "-q", "-u", "origin", branch)
    r = subprocess.run(["gh", "pr", "create", "--title", title, "--body", body, "--head", branch],
                       cwd=st.root, capture_output=True, text=True)
    print(r.stdout.strip() or r.stderr.strip())
    git(st.root, "checkout", "-q", start, check=False)
    return r.returncode


def cmd_publish(args) -> int:
    """Library maintainers: publish decisions as guarantees of a release (F10)."""
    from . import registry

    st = store_from(args).require()
    try:
        bundle = registry.build_bundle(st, args.ids, args.version)
    except registry.RegistryError as e:
        print(c(str(e), "31"))
        return 2
    head = f"{bundle['ecosystem']} {bundle['package']}@{bundle['version']}"
    print(bold(f"Guarantees for {head}:"))
    for g in bundle["guarantees"]:
        print(f"  {g['id']}  {g['title']}  {dim('(from ' + g['decision'] + ')')}")
    if args.dry_run:
        return 0
    if args.to_package:
        for p in registry.write_into_package(bundle, Path(args.to_package)):
            print(f"  wrote {p}")
        print(dim("They ship with the package; downstream `why upgrade` runs them automatically."))
    url = args.registry or registry.registry_url(st)
    if url:
        try:
            print(f"  published {registry.upload(bundle, url, os.environ.get('WHY_REGISTRY_TOKEN'))}")
        except registry.RegistryError as e:
            print(c(str(e), "31"))
            return 1
    elif not args.to_package:
        print(c("Nowhere to publish: pass --to-package <dir> or --registry <url> (or set WHY_REGISTRY_URL).", "33"))
        return 2
    return 0


def cmd_guarantees(args) -> int:
    from . import registry

    st = store_from(args)
    url = args.registry or (registry.registry_url(st) if st.exists else os.environ.get("WHY_REGISTRY_URL"))
    if not url:
        print("No registry configured (WHY_REGISTRY_URL or `registry:` in .why/config.yaml).")
        return 2
    eco = args.ecosystem or ("npm" if (st.root / "package.json").exists() else "pypi")
    bundle = registry.fetch(url, eco, args.package, args.version or "")
    if not bundle:
        print(f"No guarantees published for {eco} {args.package}{'@' + args.version if args.version else ''}.")
        return 1
    print(bold(f"{bundle['package']}@{bundle['version']} guarantees ({bundle['published_at']}):"))
    for g in bundle["guarantees"]:
        print(f"  {g['id']}  {g['title']}")
        print(f"        {dim('why:')} {g.get('reason') or '—'}")
    return 0


def cmd_audit(args) -> int:
    from .audit import export

    st = store_from(args).require()
    out = export(st, args.format, args.since)
    if args.out:
        Path(args.out).write_text(out)
        print(f"Wrote {args.out}")
    else:
        sys.stdout.write(out)
    return 0


def cmd_models(args) -> int:
    if not llm.api_key():
        print("NEBIUS_API_KEY is not set. Why will use its rule-based fallbacks.")
        return 2
    try:
        ids = llm.list_models()
    except Exception as e:  # noqa: BLE001
        print(c(f"Could not list models at {llm.base_url()}: {e}", "31"))
        return 1
    for role in ("nano", "super", "ultra"):
        print(f"  {role:<6} → {llm.model_for(role)}")
    if args.all:
        print(dim("\nAvailable:"))
        for i in ids:
            print(f"  {i}")
    return 0


# ---- parser ----------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="why", description="Git remembers what changed. Why remembers what must stay true.")
    p.add_argument("--version", action="version", version=f"why {__version__}")
    p.add_argument("-C", "--repo", help="repository path (default: current directory)")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init", help="create the .why/ store in this repo")
    s.add_argument("--language", choices=["python", "javascript"], default=None)
    s.add_argument("--python-path", nargs="*", default=None, help="import roots, e.g. src")
    s.add_argument("--sandbox", choices=["local", "nebius"], default=None)
    s.set_defaults(fn=cmd_init)

    s = sub.add_parser("mine", help="propose decisions from commits, PRs and agent logs")
    s.add_argument("--days", type=int)
    s.add_argument("--no-model", action="store_true", help="rule-based miner only")
    s.add_argument("--no-github", action="store_true")
    s.add_argument("--dry-run", action="store_true")
    s.set_defaults(fn=cmd_mine)

    s = sub.add_parser("list", aliases=["ls", "status"], help="list decisions")
    s.add_argument("--status", choices=["proposed", "active", "violated", "superseded", "retired"])
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_list)

    s = sub.add_parser("show", help="show one decision")
    s.add_argument("id")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_show)

    s = sub.add_parser("approve", help="generate a guard, prove it stable, activate")
    s.add_argument("ids", nargs="*")
    s.add_argument("--all", action="store_true", help="approve every proposed decision")
    s.add_argument("--no-model", action="store_true")
    s.set_defaults(fn=cmd_approve)

    s = sub.add_parser("reject", help="reject proposed decisions")
    s.add_argument("ids", nargs="+")
    s.add_argument("--note")
    s.set_defaults(fn=cmd_reject)

    s = sub.add_parser("add", help="record a decision by hand")
    s.add_argument("rule")
    s.add_argument("--title")
    s.add_argument("--reason")
    s.add_argument("--kind", default="custom", choices=["call_count", "returns", "forbid_pattern", "custom"])
    s.add_argument("--params", help="JSON params for the guard kind")
    s.add_argument("--file", action="append")
    s.add_argument("--symbol", action="append")
    s.set_defaults(fn=cmd_add)

    s = sub.add_parser("edit", help="edit a decision in $EDITOR")
    s.add_argument("id")
    s.set_defaults(fn=cmd_edit)

    s = sub.add_parser("guard", help="(re)generate the guard for a decision")
    s.add_argument("id")
    s.add_argument("--no-model", action="store_true")
    s.set_defaults(fn=cmd_guard)

    s = sub.add_parser("check", help="run every active guard; exit 1 on violations")
    s.add_argument("--ref", help="check this commit instead of the working tree")
    s.add_argument("--base", help="take guards from this trusted ref (e.g. main)")
    s.add_argument("--context", default="local")
    s.add_argument("--no-transitions", action="store_true", help="record evidence only")
    s.add_argument("--no-cache", action="store_true", help="rerun every guard even if this code tree was checked")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_check)

    s = sub.add_parser("review", help="the review queue: proposed decisions, who should look at them")
    s.add_argument("--mine", action="store_true", help="only decisions assigned to you")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_review)

    s = sub.add_parser("team", help="members, roles and owners (teams mode), and who you are")
    s.set_defaults(fn=cmd_team)

    s = sub.add_parser("log", help="history of decision state changes")
    s.add_argument("id", nargs="?")
    s.add_argument("--limit", type=int, default=50)
    s.set_defaults(fn=cmd_log)

    s = sub.add_parser("blame", help="which decisions govern file[:line]")
    s.add_argument("target")
    s.set_defaults(fn=cmd_blame)

    s = sub.add_parser("upgrade", help="check a dependency bump against every decision")
    s.add_argument("spec", help="pkg@version")
    s.add_argument("--find-links", help="extra directory/URL of wheels (private or local packages)")
    s.add_argument("--from", dest="from_version", help="pin the current version explicitly")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_upgrade)

    s = sub.add_parser("assume", help="re-verify outside assumptions with Tavily")
    s.add_argument("ids", nargs="*")
    s.add_argument("--fixture", help="use a recorded Tavily response (JSON) instead of a live search")
    s.set_defaults(fn=cmd_assume)

    s = sub.add_parser("merge", help="check parallel branches' decisions against their merged code")
    s.add_argument("branches", nargs="+", help="one or more branches to merge into --base")
    s.add_argument("--base", default="HEAD")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_merge)

    s = sub.add_parser("supersede", help="replace a decision, recording why")
    s.add_argument("id")
    s.add_argument("--reason", required=True)
    s.add_argument("--rule", help="the new rule, if there is one")
    s.set_defaults(fn=cmd_supersede)

    s = sub.add_parser("retire", help="intentionally remove a decision")
    s.add_argument("id")
    s.add_argument("--note")
    s.set_defaults(fn=cmd_retire)

    s = sub.add_parser("serve", help="run the web dashboard and GitHub App webhook")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=7357)
    s.set_defaults(fn=cmd_serve)

    s = sub.add_parser("export", help="write a static dashboard dataset (fallback for demos)")
    s.add_argument("--out", default="why-snapshot.json")
    s.add_argument("--name")
    s.set_defaults(fn=cmd_export)

    s = sub.add_parser("mcp", help="serve decisions to coding agents over MCP (stdio)")
    s.set_defaults(fn=cmd_mcp)

    s = sub.add_parser("mcp-install", help="write .mcp.json so agents in this repo get the Why tools")
    s.set_defaults(fn=cmd_mcp_install)

    s = sub.add_parser("doctor", help="check keys, models, Tavily, GitHub, sandbox and MCP setup")
    s.add_argument("--offline", action="store_true", help="don't call any service")
    s.set_defaults(fn=cmd_doctor)

    s = sub.add_parser("onboard", help="init + mine + review list: the first 10 minutes on a repo")
    s.add_argument("--days", type=int, default=90)
    s.add_argument("--language", choices=["python", "javascript"], default=None)
    s.add_argument("--no-github", action="store_true")
    s.add_argument("--mcp", action="store_true", help="also write .mcp.json")
    s.set_defaults(fn=cmd_onboard)

    s = sub.add_parser("pr", help="commit decisions + guards on a branch and open one PR")
    s.add_argument("--branch", default="why/guards")
    s.add_argument("--dry-run", action="store_true")
    s.add_argument("--no-push", action="store_true", help="commit only")
    s.set_defaults(fn=cmd_pr)

    s = sub.add_parser("publish", help="(library maintainers) publish decisions as guarantees of a release")
    s.add_argument("ids", nargs="+")
    s.add_argument("--version", help="release version (default: from package.json / pyproject.toml)")
    s.add_argument("--registry", help="Why registry URL (default: WHY_REGISTRY_URL)")
    s.add_argument("--to-package", help="also ship them inside the package, e.g. src/mylib")
    s.add_argument("--dry-run", action="store_true")
    s.set_defaults(fn=cmd_publish)

    s = sub.add_parser("guarantees", help="list the guarantees a package published")
    s.add_argument("package")
    s.add_argument("--version")
    s.add_argument("--ecosystem", choices=["pypi", "npm"])
    s.add_argument("--registry")
    s.set_defaults(fn=cmd_guarantees)

    s = sub.add_parser("audit", help="export decision history and evidence (json, csv or html)")
    s.add_argument("--format", choices=["json", "csv", "html"], default="json")
    s.add_argument("--out")
    s.add_argument("--since", help="ISO date, e.g. 2026-10-01")
    s.set_defaults(fn=cmd_audit)

    s = sub.add_parser("models", help="show which Nemotron model serves each role")
    s.add_argument("--all", action="store_true")
    s.set_defaults(fn=cmd_models)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.fn(args) or 0
    except StoreError as e:
        print(c(str(e), "31"), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
