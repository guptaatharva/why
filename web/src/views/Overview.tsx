import type { ReactNode } from "react";
import { useApp } from "../ctx";
import type { Decision, Evidence, LogEntry } from "../types";
import { Chip, Empty, ExternalLink, LogBlock, Mono, ResultIcon, StatusPill, Ticks, VerdictPill } from "../components/ui";
import { ago, classNames, commitUrl, CONTEXT_LABEL, fullTime, parseTs, sha, SOURCE_LABEL } from "../util";

export function Overview() {
  const { snap, nowMs, go } = useApp();
  const c = snap.counts;
  const guardRuns = snap.evidence.filter((e) => e.context !== "assumption").length;
  const lastCheck = snap.checks[0];

  if (snap.decisions.length === 0) {
    return (
      <div className="page">
        <Hero />
        <Empty title="No decisions yet" cmd="why mine">
          Why reads your commits, PR threads and agent logs and proposes the decisions hiding in them. Review them here, then
          each approved decision gets an executable guard.
        </Empty>
      </div>
    );
  }

  return (
    <div className="page">
      <Hero />

      <div className="stats">
        <Stat label="Active" value={c.active ?? 0} tone="green" onClick={() => go("decisions", "status=active")} hint="guarded and holding" />
        <Stat label="Violated" value={c.violated ?? 0} tone="red" onClick={() => go("decisions", "status=violated")} hint="guard failing now" />
        <Stat label="Proposed" value={c.proposed ?? 0} tone="amber" onClick={() => go("decisions", "status=proposed")} hint="awaiting review" />
        <Stat label="Guard runs" value={guardRuns} onClick={() => go("checks")} hint={`across ${snap.checks.length} checks`} />
        <Stat
          label="Last check"
          value={lastCheck ? (lastCheck.ok ? "Passing" : "Failing") : "None"}
          tone={lastCheck ? (lastCheck.ok ? "green" : "red") : undefined}
          small
          onClick={() => go("checks")}
          hint={
            lastCheck
              ? `${lastCheck.pr ? `PR #${lastCheck.pr}` : CONTEXT_LABEL[lastCheck.context] ?? lastCheck.context} · ${ago(lastCheck.created_at, nowMs)}`
              : "run why check"
          }
        />
      </div>

      <div className="grid-2">
        <div className="col">
          <LatestViolation />
          <Activity />
        </div>
        <div className="col col-side">
          <ReviewQueue />
          <UpgradeMini />
          <AssumptionMini />
        </div>
      </div>
    </div>
  );
}

function Hero() {
  const { snap } = useApp();
  return (
    <div className="hero">
      <div className="hero-top">
        <h1>{snap.repo.name}</h1>
        {snap.repo.head && (
          <ExternalLink href={commitUrl(snap.repo.github, snap.repo.head ?? undefined)} className="hero-sha">
            <Mono>HEAD {sha(snap.repo.head)}</Mono>
          </ExternalLink>
        )}
      </div>
      <p className="hero-tag">
        Git remembers what changed. <strong>Why remembers what must stay true</strong>, and proves it on every change.
      </p>
    </div>
  );
}

function Stat({
  label,
  value,
  tone,
  hint,
  small,
  onClick,
}: {
  label: string;
  value: ReactNode;
  tone?: "green" | "red" | "amber";
  hint?: string;
  small?: boolean;
  onClick?: () => void;
}) {
  return (
    <button className={classNames("stat", tone && `stat-${tone}`)} onClick={onClick}>
      <div className="stat-label">
        {tone && <span className={`dot dot-${tone}`} />}
        {label}
      </div>
      <div className={classNames("stat-value", small && "stat-value-sm")}>{value}</div>
      {hint && <div className="stat-hint">{hint}</div>}
    </button>
  );
}

function LatestViolation() {
  const { snap, openDecision, nowMs } = useApp();
  const violated = snap.decisions
    .filter((d) => d.status === "violated")
    .sort((a, b) => (parseTs(b.last_evidence_at) || 0) - (parseTs(a.last_evidence_at) || 0));
  const d = violated[0];
  if (!d) {
    const active = snap.counts.active ?? 0;
    return (
      <div className="card ok-card">
        <div className="ok-icon">
          <ResultIcon result="pass" size={18} />
        </div>
        <div>
          <div className="ok-title">Every guarded decision holds</div>
          <div className="muted">
            {active} active {active === 1 ? "decision" : "decisions"} passed on the last run. Why runs them again on every PR, merge and
            dependency upgrade.
          </div>
        </div>
      </div>
    );
  }
  const fail = [...snap.evidence].reverse().find((e) => e.decision_id === d.id && (e.result === "fail" || e.result === "error"));
  const run = fail ? snap.checks.find((c) => c.run_id === fail.run_id) : undefined;
  const commit = run?.head_sha || fail?.commit;
  const item = run?.items.find((i) => i.decision_id === d.id);
  const log = item?.log || fail?.log || "";

  return (
    <div className="card violation">
      <div className="violation-bar" />
      <div className="violation-head">
        <span className="eyebrow red">
          <span className="pulse" /> Latest violation
        </span>
        <span className="muted small">{ago(fail?.timestamp ?? d.last_evidence_at, nowMs)}</span>
      </div>
      <button className="violation-title" onClick={() => openDecision(d.id)}>
        <Mono className="did">{d.id}</Mono>
        <span>
          <Ticks text={d.title} />
        </span>
      </button>
      <div className="why-line">
        <span className="why-label">Why</span>
        <span className="why-text">{d.reason || "No reason recorded."}</span>
      </div>
      {d.source?.quote && (
        <blockquote className="quote">
          <p>{d.source.quote}</p>
          <footer>
            {SOURCE_LABEL[d.source.type] ?? d.source.type}
            {d.source.ref && <Mono> {d.source.ref}</Mono>}
            {d.source.author && <> · {d.source.author}</>}
            {d.source.link && (
              <>
                {" · "}
                <ExternalLink href={d.source.link}>view source</ExternalLink>
              </>
            )}
          </footer>
        </blockquote>
      )}
      <div className="violation-meta">
        {run?.pr ? (
          <Chip>
            <ExternalLink href={run.pr_url}>PR #{run.pr}</ExternalLink>
          </Chip>
        ) : (
          fail && <Chip>{CONTEXT_LABEL[fail.context] ?? fail.context}</Chip>
        )}
        {commit && (
          <Chip title={commit}>
            failing commit{" "}
            <ExternalLink href={commitUrl(snap.repo.github, commit)}>
              <Mono>{sha(commit)}</Mono>
            </ExternalLink>
          </Chip>
        )}
        {d.guard && (
          <Chip title={d.guard.path}>
            guard <Mono>{d.guard.path.split("/").pop()}</Mono>
          </Chip>
        )}
        <button className="btn btn-ghost btn-sm push-right" onClick={() => openDecision(d.id)}>
          Open decision →
        </button>
      </div>
      {log && <LogBlock text={log} />}
      {violated.length > 1 && (
        <div className="muted small more">
          +{violated.length - 1} more violated: {violated.slice(1).map((x) => x.id).join(", ")}
        </div>
      )}
    </div>
  );
}

type Item = { ts: string; key: string; node: ReactNode; tone: "red" | "green" | "amber" | "violet" | "gray" | "blue" };

function Activity() {
  const { snap, nowMs, openDecision, byId } = useApp();
  const items: Item[] = [];

  snap.log.forEach((l: LogEntry, i) => items.push(logItem(l, i, byId, openDecision)));

  // Fold evidence into one item per run so a check of 12 guards is one line, not twelve.
  const runs = new Map<string, Evidence[]>();
  snap.evidence.forEach((e, i) => {
    if (e.context === "mcp") return; // shown via consults (one line per self-check)
    const k = e.context === "stability" ? `stability-${e.decision_id}` : e.run_id || `${e.context}-${e.timestamp}-${i}`;
    if (!runs.has(k)) runs.set(k, []);
    runs.get(k)!.push(e);
  });
  runs.forEach((evs, k) => {
    const last = evs[evs.length - 1];
    const failed = evs.filter((e) => e.result === "fail" || e.result === "error");
    const passed = evs.filter((e) => e.result === "pass").length;
    const ctx = last.context;
    const run = snap.checks.find((c) => c.run_id === last.run_id);
    let label: ReactNode;
    if (ctx === "assumption") {
      const e = evs[0];
      label = (
        <>
          Assumption for <DLink id={e.decision_id} onClick={openDecision} /> {e.result === "fail" ? "is broken" : e.result === "pass" ? "still holds" : "is unclear"}
        </>
      );
    } else if (ctx === "upgrade") {
      const det = (last.detail ?? {}) as Record<string, string>;
      label = (
        <>
          Upgrade <Mono>{det.package ?? "dependency"} {det.from ? `${det.from} → ${det.to}` : ""}</Mono> broke{" "}
          {failed.map((e, i) => (
            <span key={e.decision_id}>
              {i > 0 && ", "}
              <DLink id={e.decision_id} onClick={openDecision} />
            </span>
          ))}
        </>
      );
    } else if (ctx === "stability") {
      label = (
        <>
          Stability runs for <DLink id={last.decision_id} onClick={openDecision} />: {passed}/{evs.length} passed
        </>
      );
    } else {
      const where = run?.pr ? `PR #${run.pr}` : CONTEXT_LABEL[ctx] ?? ctx;
      label = (
        <>
          {where} check · {passed} passed
          {failed.length > 0 && (
            <>
              , <span className="text-red">{failed.length} failed</span> (
              {failed.map((e, i) => (
                <span key={e.decision_id}>
                  {i > 0 && ", "}
                  <DLink id={e.decision_id} onClick={openDecision} />
                </span>
              ))}
              )
            </>
          )}
          {last.commit && (
            <>
              {" "}
              at <Mono>{sha(last.commit)}</Mono>
            </>
          )}
        </>
      );
    }
    items.push({
      ts: last.timestamp,
      key: `run-${k}`,
      node: label,
      tone: failed.length ? "red" : ctx === "assumption" ? "gray" : "green",
    });
  });

  // Prevention: agents consulting Why over MCP before they edit.
  (snap.consults ?? []).forEach((c, i) => {
    const ids = (
      <>
        {c.decisions.map((id, j) => (
          <span key={id}>
            {j > 0 && ", "}
            <DLink id={id} onClick={openDecision} />
          </span>
        ))}
      </>
    );
    let node: ReactNode = null;
    let tone: Item["tone"] = "blue";
    if (c.tool === "why_decisions_for") {
      node = (
        <>
          Agent asked Why before editing <Mono>{String(c.args.path ?? "")}{c.args.line ? `:${c.args.line}` : ""}</Mono>
          {c.decisions.length ? <> · shown {ids}</> : " · no decisions"}
        </>
      );
    } else if (c.tool === "why_propose_decision") {
      node = <>Agent proposed {ids} over MCP</>;
      tone = "amber";
    } else if (c.tool === "why_run_guards") {
      tone = c.decisions.length ? "red" : "green";
      node = c.decisions.length ? (
        <>Agent self-check caught {ids} before a PR</>
      ) : (
        <>Agent self-check passed</>
      );
    }
    if (node) items.push({ ts: c.ts, key: `mcp-${i}`, node, tone });
  });

  items.sort((a, b) => (parseTs(b.ts) || 0) - (parseTs(a.ts) || 0));
  const shown = items.slice(0, 14);

  return (
    <div className="card">
      <div className="card-head">
        <h3>Recent activity</h3>
        <span className="muted small">{items.length} events</span>
      </div>
      {shown.length === 0 ? (
        <Empty title="Nothing has happened yet" cmd="why check" />
      ) : (
        <ol className="feed">
          {shown.map((it) => (
            <li key={it.key} className="feed-item">
              <span className={`feed-dot dot-${it.tone}`} />
              <div className="feed-body">{it.node}</div>
              <time className="feed-time" title={fullTime(it.ts)}>
                {ago(it.ts, nowMs)}
              </time>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}

function logItem(l: LogEntry, i: number, byId: Map<string, Decision>, open: (id: string) => void): Item {
  const tone: Item["tone"] =
    l.to === "violated" ? "red" : l.to === "active" ? "green" : l.to === "proposed" ? "amber" : l.to === "superseded" ? "violet" : "gray";
  let verb: ReactNode;
  if (l.from === null || l.from === undefined) verb = <>proposed {l.note ? <span className="muted">({l.note})</span> : null}</>;
  else if (l.from === l.to) verb = <span className="muted">{l.note}</span>;
  else
    verb = (
      <>
        <span className="muted">{l.from}</span> → <StatusPill status={l.to} />
      </>
    );
  const title = byId.get(l.decision_id)?.title;
  return {
    ts: l.ts,
    key: `log-${i}`,
    tone,
    node: (
      <>
        <DLink id={l.decision_id} onClick={open} />
        {title && (
          <span className="feed-title">
            {" "}
            <Ticks text={title} />
          </span>
        )} {verb}
        <span className="muted small"> · {l.actor}</span>
      </>
    ),
  };
}

function DLink({ id, onClick }: { id: string; onClick: (id: string) => void }) {
  return (
    <button className="dlink mono" onClick={() => onClick(id)}>
      {id}
    </button>
  );
}

function ReviewQueue() {
  const { snap, openDecision, go } = useApp();
  const proposed = snap.decisions.filter((d) => d.status === "proposed");
  if (!proposed.length) return null;
  return (
    <div className="card">
      <div className="card-head">
        <h3>Waiting for review</h3>
        <button className="linkbtn" onClick={() => go("decisions", "status=proposed")}>
          Review all →
        </button>
      </div>
      <p className="muted small mb">Mined from history. Approving one compiles its guard and runs it.</p>
      <ul className="mini-list">
        {proposed.slice(0, 4).map((d) => (
          <li key={d.id}>
            <button onClick={() => openDecision(d.id)}>
              <Mono className="did">{d.id}</Mono>
              <span className="mini-title">
                <Ticks text={d.title} />
              </span>
              <span className="conf">{Math.round((d.confidence ?? 0) * 100)}%</span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

function UpgradeMini() {
  const { snap, go, nowMs } = useApp();
  const u = snap.upgrades[0];
  if (!u) return null;
  return (
    <button className="card card-btn" onClick={() => go("upgrades")}>
      <div className="card-head">
        <h3>Latest upgrade</h3>
        <span className={classNames("verdict", `verdict-${u.verdict}`)}>{u.verdict}</span>
      </div>
      <div className="upmini">
        <Mono className="up-pkg">{u.package}</Mono>
        <div className="up-vers mono">
          <span>{u.from_version}</span>
          <span className="arrow">→</span>
          <span className={u.verdict === "blocked" ? "text-red" : "text-green"}>{u.to_version}</span>
        </div>
      </div>
      <div className="muted small">
        {u.breaks.length ? `${u.breaks.length} decision${u.breaks.length > 1 ? "s" : ""} broken · ` : "No decisions broken · "}
        {u.passed_before}/{u.guards_run} → {u.passed_after}/{u.guards_run} guards · {ago(u.created_at, nowMs)}
      </div>
    </button>
  );
}

function AssumptionMini() {
  const { snap, go } = useApp();
  const withA = snap.decisions.filter((d) => d.assumption);
  if (!withA.length) return null;
  const order = { broken: 0, unclear: 1, unchecked: 2, holds: 3 } as Record<string, number>;
  withA.sort((a, b) => (order[a.assumption!.verdict] ?? 9) - (order[b.assumption!.verdict] ?? 9));
  return (
    <button className="card card-btn" onClick={() => go("assumptions")}>
      <div className="card-head">
        <h3>Outside assumptions</h3>
      </div>
      <ul className="mini-list static">
        {withA.slice(0, 3).map((d) => (
          <li key={d.id}>
            <div className="mini-row">
              <VerdictPill verdict={d.assumption!.verdict} />
              <span className="mini-title">{d.assumption!.claim}</span>
            </div>
          </li>
        ))}
      </ul>
    </button>
  );
}
