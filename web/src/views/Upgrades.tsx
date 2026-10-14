import { useApp } from "../ctx";
import type { Break, UpgradeReport } from "../types";
import { Chip, Empty, LogBlock, Mono, Ticks, ValueDiff } from "../components/ui";
import { ago, classNames, fmtArgs, fullTime, sha } from "../util";

export function Upgrades() {
  const { snap } = useApp();
  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h2>Upgrades</h2>
          <p className="muted">
            Before a dependency moves, Why runs every guard on the old and the new version and traces what changed underneath.
          </p>
        </div>
      </div>
      {snap.upgrades.length === 0 ? (
        <Empty title="No upgrades checked yet" cmd="why upgrade <package>==<version>">
          Why installs the new version in a sandbox, re-runs your guards, and pins any break to the exact API whose behaviour changed.
        </Empty>
      ) : (
        <div className="upgrades">
          {snap.upgrades.map((u) => (
            <Upgrade key={u.id} u={u} />
          ))}
        </div>
      )}
    </div>
  );
}

function Upgrade({ u }: { u: UpgradeReport }) {
  const { nowMs } = useApp();
  const blocked = u.verdict === "blocked";
  return (
    <article className={classNames("upgrade", `upgrade-${u.verdict}`)}>
      <header className="upgrade-head">
        <div className="upgrade-pkg">
          <Mono className="pkg-name">{u.package}</Mono>
          <div className="pkg-vers mono">
            <span className="v-from">{u.from_version || "?"}</span>
            <svg width="22" height="12" viewBox="0 0 22 12" aria-hidden>
              <path d="M1 6h19M15 1.5L20 6l-5 4.5" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
            <span className="v-to">{u.to_version}</span>
          </div>
        </div>
        <div className={classNames("verdict-big", `verdict-${u.verdict}`)}>
          {blocked ? (
            <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden>
              <circle cx="8" cy="8" r="6.2" fill="none" stroke="currentColor" strokeWidth="1.6" />
              <path d="M3.8 12.2l8.4-8.4" stroke="currentColor" strokeWidth="1.6" />
            </svg>
          ) : u.verdict === "approved" ? (
            <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden>
              <path d="M3.5 8.5l3 3 6-7" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          ) : null}
          {blocked ? "Upgrade blocked" : u.verdict === "approved" ? "Safe to upgrade" : "Could not evaluate"}
        </div>
      </header>

      <div className="upgrade-meta">
        <time title={fullTime(u.created_at)}>{ago(u.created_at, nowMs)}</time>
        {u.commit && (
          <>
            <span>·</span>
            <span>
              at <Mono>{sha(u.commit)}</Mono>
            </span>
          </>
        )}
        <span>·</span>
        <span>
          {u.breaks.length} {u.breaks.length === 1 ? "decision" : "decisions"} broken
        </span>
      </div>

      {u.verdict !== "error" && (
        <div className="guardbars">
          <GuardBar label={`Before · ${u.from_version}`} passed={u.passed_before} total={u.guards_run} />
          <GuardBar label={`After · ${u.to_version}`} passed={u.passed_after} total={u.guards_run} />
        </div>
      )}

      {u.error && <pre className="log">{u.error}</pre>}

      {u.breaks.map((b) => (
        <BreakCard key={b.decision_id} b={b} u={u} />
      ))}

      {!!u.guarantees?.length && (
        <div className="guarantees">
          <span className="muted small">Published guarantees from {u.package} also run:</span>
          {u.guarantees.map((g) => (
            <Chip key={g}>
              <Mono>{g.split("/").pop()}</Mono>
            </Chip>
          ))}
        </div>
      )}
    </article>
  );
}

function GuardBar({ label, passed, total }: { label: string; passed: number; total: number }) {
  const failed = Math.max(0, total - passed);
  return (
    <div className="gbar">
      <div className="gbar-label">
        <span className="mono">{label}</span>
        <span>
          <strong>{passed}</strong>
          <span className="muted">/{total} guards pass</span>
        </span>
      </div>
      <div className="gbar-track">
        {Array.from({ length: total }).map((_, i) => (
          <span key={i} className={classNames("gbar-seg", i < passed ? "pass" : "fail")} />
        ))}
        {total === 0 && <span className="gbar-seg empty" />}
      </div>
      {failed > 0 && <span className="sr-only">{failed} failing</span>}
    </div>
  );
}

function BreakCard({ b, u }: { b: Break; u: UpgradeReport }) {
  const { openDecision, snap } = useApp();
  const rc = b.root_cause;
  const hasDiff = rc && rc.before != null && rc.after != null;
  const isGuarantee = b.decision_id.startsWith("guarantee:");
  return (
    <div className="break">
      <div className="break-head">
        <span className="tag tag-red">broken</span>
        {isGuarantee ? (
          <Mono className="did">{b.decision_id}</Mono>
        ) : (
          <button className="dlink mono did" onClick={() => openDecision(b.decision_id)}>
            {b.decision_id}
          </button>
        )}
        <span className="break-title">
          <Ticks text={b.title} />
        </span>
      </div>
      {b.reason && (
        <div className="item-why">
          <span className="why-label">Why</span> {b.reason}
        </div>
      )}

      {rc && (
        <div className="rootcause">
          <div className="rc-head">
            <span className="label">Root cause</span>
            <code className="rc-call">
              <span className="rc-api">{rc.api}</span>
              {rc.args != null && <span className="rc-args">({fmtArgs(rc.args)})</span>}
            </code>
          </div>
          {hasDiff ? (
            <ValueDiff
              before={String(rc.before)}
              after={String(rc.after)}
              beforeLabel={`${u.package} ${u.from_version}`}
              afterLabel={`${u.package} ${u.to_version}`}
            />
          ) : (
            rc.note && <div className="note">{rc.note}</div>
          )}
          {rc.candidates && rc.candidates.length > 1 && (
            <div className="muted small">Other changed calls: {rc.candidates.slice(1).join(", ")}</div>
          )}
        </div>
      )}

      {b.explanation && (
        <div className="diagnosis">
          <div className="diag-label">
            <span className="spark" aria-hidden>
              ✦
            </span>
            Diagnosis{snap.models?.live ? " · Nemotron Ultra" : ""}
          </div>
          <p>
            <Ticks text={b.explanation} />
          </p>
        </div>
      )}
      <LogBlock text={b.log} />
    </div>
  );
}
