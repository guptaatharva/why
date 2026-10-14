import { useState } from "react";
import { useApp } from "../ctx";
import type { CheckReport, MergeReport } from "../types";
import { Chevron, Empty, ExternalLink, LogBlock, Mono, ResultIcon, Ticks } from "../components/ui";
import { ago, classNames, commitUrl, CONTEXT_LABEL, fullTime, sha } from "../util";

export function Checks() {
  const { snap } = useApp();
  const firstFail = snap.checks.find((c) => !c.ok)?.run_id;
  const passing = snap.checks.filter((c) => c.ok).length;

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h2>Checks</h2>
          <p className="muted">Every run of the guards, locally, on pull requests and from this dashboard.</p>
        </div>
        {snap.checks.length > 0 && (
          <div className="head-stats">
            <span>
              <strong>{snap.checks.length}</strong> runs
            </span>
            <span className="text-green">
              <strong>{passing}</strong> passed
            </span>
            <span className="text-red">
              <strong>{snap.checks.length - passing}</strong> failed
            </span>
          </div>
        )}
      </div>

      {snap.checks.length === 0 ? (
        <Empty title="No checks yet" cmd="why check">
          Run every active guard against your working tree. Installed as a GitHub App, Why runs the same check on every pull request.
        </Empty>
      ) : (
        <div className="runs">
          {snap.checks.map((c) => (
            <Run key={c.run_id} c={c} defaultOpen={c.run_id === firstFail} />
          ))}
        </div>
      )}

      {snap.merges.length > 0 && (
        <>
          <h3 className="subhead">Merge checks</h3>
          <div className="runs">
            {snap.merges.map((m, i) => (
              <Merge key={m.id ?? i} m={m} />
            ))}
          </div>
        </>
      )}
    </div>
  );
}

function Run({ c, defaultOpen }: { c: CheckReport; defaultOpen: boolean }) {
  const { snap, nowMs, openDecision } = useApp();
  const [open, setOpen] = useState(defaultOpen);
  const failed = c.items.filter((i) => i.result === "fail" || i.result === "error");
  const passed = c.items.filter((i) => i.result === "pass").length;
  const commit = c.head_sha || c.commit;
  const title = typeof c.pr_title === "string" ? c.pr_title : null;

  return (
    <div className={classNames("run", c.ok ? "run-ok" : "run-fail", open && "open")}>
      <button className="run-head" onClick={() => setOpen(!open)}>
        <span className={classNames("run-icon", c.ok ? "ok" : "fail")}>
          <ResultIcon result={c.ok ? "pass" : "fail"} size={14} />
        </span>
        <div className="run-main">
          <div className="run-title">
            {c.pr ? (
              <>
                <span className="run-pr">PR #{c.pr}</span>
                {title && <span className="run-pr-title">{title}</span>}
              </>
            ) : (
              <span>{CONTEXT_LABEL[c.context] ?? c.context} check</span>
            )}
            <span className={classNames("ctx", `ctx-${c.context}`)}>{c.context}</span>
          </div>
          <div className="run-sub">
            <Mono>{sha(commit)}</Mono>
            <span>·</span>
            <time title={fullTime(c.created_at)}>{ago(c.created_at, nowMs)}</time>
            <span>·</span>
            <span>{c.duration_s ? `${c.duration_s.toFixed(1)}s` : "—"}</span>
          </div>
        </div>
        <div className="run-dots" aria-hidden>
          {c.items.map((i) => (
            <span key={i.decision_id} className={`rdot r-${i.result}`} title={`${i.decision_id} ${i.result}`} />
          ))}
        </div>
        <div className="run-summary">
          <span className="text-green">{passed} passed</span>
          {failed.length > 0 && <span className="text-red">{failed.length} failed</span>}
        </div>
        <Chevron open={open} />
      </button>
      {open && (
        <div className="run-body">
          <div className="run-links">
            {c.pr_url && <ExternalLink href={c.pr_url}>Pull request #{c.pr}</ExternalLink>}
            {commitUrl(snap.repo.github, commit) && <ExternalLink href={commitUrl(snap.repo.github, commit)}>Commit {sha(commit)}</ExternalLink>}
            <span className="muted small mono">run {c.run_id}</span>
          </div>
          <div className="items">
            {[...failed, ...c.items.filter((i) => !failed.includes(i))].map((i) => (
              <div key={i.decision_id} className={classNames("item", `item-${i.result}`)}>
                <div className="item-head">
                  <span className={`result-word r-${i.result}`}>
                    <ResultIcon result={i.result} /> {i.result}
                  </span>
                  <button className="dlink mono" onClick={() => openDecision(i.decision_id)}>
                    {i.decision_id}
                  </button>
                  <span className="item-title">
                    <Ticks text={i.title} />
                  </span>
                </div>
                {(i.result === "fail" || i.result === "error") && (
                  <>
                    {i.reason && (
                      <div className="item-why">
                        <span className="why-label">Why</span> {i.reason}
                      </div>
                    )}
                    <LogBlock text={i.log} collapsed={false} />
                  </>
                )}
              </div>
            ))}
            {c.items.length === 0 && <div className="note">No active guards were run.</div>}
          </div>
        </div>
      )}
    </div>
  );
}

function Merge({ m }: { m: MergeReport }) {
  const { nowMs, openDecision } = useApp();
  const [open, setOpen] = useState(!m.ok);
  const contradictions = m.contradictions ?? [];
  return (
    <div className={classNames("run", m.ok ? "run-ok" : "run-fail", open && "open")}>
      <button className="run-head" onClick={() => setOpen(!open)}>
        <span className={classNames("run-icon", m.ok ? "ok" : "fail")}>
          <ResultIcon result={m.ok ? "pass" : "fail"} size={14} />
        </span>
        <div className="run-main">
          <div className="run-title">
            <Mono>{m.other}</Mono>
            <span className="muted">into</span>
            <Mono>{m.base}</Mono>
            <span className="ctx ctx-merge">merge</span>
          </div>
          <div className="run-sub">
            <time title={fullTime(m.created_at)}>{ago(m.created_at, nowMs)}</time>
            {m.guards_run != null && (
              <>
                <span>·</span>
                <span>{m.guards_run} guards</span>
              </>
            )}
          </div>
        </div>
        <div className="run-summary">
          {m.ok ? (
            <span className="text-green">no contradictions</span>
          ) : m.text_conflict ? (
            <span className="text-red">text conflict</span>
          ) : m.error ? (
            <span className="text-red">error</span>
          ) : (
            <span className="text-red">{contradictions.length + (m.id_clashes?.length ?? 0)} contradictions</span>
          )}
        </div>
        <Chevron open={open} />
      </button>
      {open && (
        <div className="run-body">
          {m.error && <pre className="log">{m.error}</pre>}
          {m.detail && <pre className="log">{m.detail}</pre>}
          {!!m.conflicted_files?.length && (
            <div className="note">
              git conflict in <Mono>{m.conflicted_files.join(", ")}</Mono>
            </div>
          )}
          {!!m.id_clashes?.length && (
            <div className="note">
              Both sides created different decisions with the same id:{" "}
              {m.id_clashes.map((c, i) => (
                <span key={i}>
                  {i > 0 && "; "}
                  {typeof c === "string" ? (
                    <Mono>{c}</Mono>
                  ) : (
                    <>
                      <Mono>{c.decision_id}</Mono> on <Mono>{c.branch}</Mono> (“{c.rule}” vs “{c.other_rule}”)
                    </>
                  )}
                </span>
              ))}
              . Renumber one side before merging.
            </div>
          )}
          <div className="items">
            {contradictions.map((x) => (
              <div key={x.decision_id} className="item item-fail">
                <div className="item-head">
                  <span className="result-word r-fail">
                    <ResultIcon result="fail" /> fail
                  </span>
                  <button className="dlink mono" onClick={() => openDecision(x.decision_id)}>
                    {x.decision_id}
                  </button>
                  <span className="item-title">
                    <Ticks text={x.title} />
                  </span>
                  <span className="muted small push-right">
                    decided on <Mono>{x.from}</Mono>
                    {x.broken_by?.length ? (
                      <>
                        {" "}· broken by <Mono>{x.broken_by.join(", ")}</Mono>
                      </>
                    ) : null}
                  </span>
                </div>
                {x.reason && (
                  <div className="item-why">
                    <span className="why-label">Why</span> {x.reason}
                  </div>
                )}
                <LogBlock text={x.log} />
              </div>
            ))}
            {m.ok && <div className="note">Both branches' decisions hold on the merged tree.</div>}
          </div>
        </div>
      )}
    </div>
  );
}
