import { useMemo, useState } from "react";
import { useApp } from "../ctx";
import { STATUSES, type Decision, type Status } from "../types";
import { Empty, Mono, ResultBadge, StatusPill, Ticks } from "../components/ui";
import { ago, classNames, KIND_LABEL, SOURCE_LABEL } from "../util";

const GROUP_ORDER: Status[] = ["violated", "proposed", "active", "superseded", "retired"];

const GROUP_NOTE: Record<Status, string> = {
  violated: "A guard is failing. Fix the code, or supersede the decision if it no longer applies.",
  proposed: "Mined from history and awaiting review. Approving compiles and runs a guard.",
  active: "Guarded on every PR, merge and dependency upgrade.",
  superseded: "Replaced by a newer decision. Kept for the record.",
  retired: "No longer enforced.",
};

export function Decisions({ query }: { query: URLSearchParams }) {
  const { snap, go } = useApp();
  const status = (query.get("status") as Status | null) ?? null;
  const [q, setQ] = useState("");

  const filtered = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return snap.decisions.filter((d) => {
      if (status && d.status !== status) return false;
      if (!needle) return true;
      return [d.id, d.title, d.rule, d.reason, d.source?.quote, d.kind, ...(d.scope?.symbols ?? [])]
        .filter(Boolean)
        .some((s) => String(s).toLowerCase().includes(needle));
    });
  }, [snap.decisions, status, q]);

  const setStatus = (s: Status | null) => go("decisions", s ? `status=${s}` : undefined);

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h2>Decisions</h2>
          <p className="muted">Each decision is a rule, the reason behind it, where it came from, and the guard that proves it.</p>
        </div>
      </div>

      <div className="toolbar">
        <div className="seg" role="tablist">
          <button className={classNames("seg-item", !status && "on")} onClick={() => setStatus(null)}>
            All <span className="seg-count">{snap.decisions.length}</span>
          </button>
          {STATUSES.map((s) => (
            <button key={s} className={classNames("seg-item", status === s && "on")} onClick={() => setStatus(s)}>
              <span className={`dot dot-s-${s}`} />
              {s}
              <span className="seg-count">{snap.counts[s] ?? 0}</span>
            </button>
          ))}
        </div>
        <div className="search">
          <svg width="14" height="14" viewBox="0 0 16 16" aria-hidden>
            <circle cx="7" cy="7" r="4.5" fill="none" stroke="currentColor" strokeWidth="1.5" />
            <path d="M10.5 10.5L14 14" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
          </svg>
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search rules, reasons, symbols…" />
        </div>
      </div>

      {snap.decisions.length === 0 ? (
        <Empty title="No decisions yet" cmd="why mine">
          Run <Mono>why mine</Mono> to propose decisions from your commits, PR threads and agent logs, or add one by hand with{" "}
          <Mono>why add</Mono>.
        </Empty>
      ) : filtered.length === 0 ? (
        <Empty title={status ? `No ${status} decisions${q ? " match" : ""}` : "Nothing matches"}>
          {status === "proposed" ? (
            <>
              The review queue is clear. Run <Mono>why mine</Mono> to look for more.
            </>
          ) : (
            "Try a different filter or search."
          )}
        </Empty>
      ) : status ? (
        <Group status={status} rows={filtered} />
      ) : (
        GROUP_ORDER.map((s) => {
          const rows = filtered.filter((d) => d.status === s);
          return rows.length ? <Group key={s} status={s} rows={rows} /> : null;
        })
      )}
    </div>
  );
}

function Group({ status, rows }: { status: Status; rows: Decision[] }) {
  const { openDecision, nowMs } = useApp();
  return (
    <div className="group">
      <div className="group-head">
        <StatusPill status={status} />
        <span className="group-count">{rows.length}</span>
        <span className="muted small">{GROUP_NOTE[status]}</span>
      </div>
      <div className="table">
        <div className="tr th">
          <div>ID</div>
          <div>Decision</div>
          <div>Guard</div>
          <div>Source</div>
          <div>Last result</div>
        </div>
        {rows.map((d) => (
          <button key={d.id} className={classNames("tr", `row-${d.status}`)} onClick={() => openDecision(d.id)}>
            <div>
              <Mono className="did">{d.id}</Mono>
            </div>
            <div className="td-title">
              <div className="row-title">
                <Ticks text={d.title} />
                {d.assumption?.verdict === "broken" && <span className="tag tag-red">assumption broken</span>}
              </div>
              <div className="row-reason">{d.reason || d.rule}</div>
            </div>
            <div>
              <span className="kind">{KIND_LABEL[d.kind ?? "custom"] ?? d.kind}</span>
            </div>
            <div className="muted">{SOURCE_LABEL[d.source?.type] ?? d.source?.type}</div>
            <div className="td-result">
              <ResultBadge result={d.last_result} />
              {d.last_evidence_at && <span className="muted small">{ago(d.last_evidence_at, nowMs)}</span>}
            </div>
          </button>
        ))}
      </div>
    </div>
  );
}
