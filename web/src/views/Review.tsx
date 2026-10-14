import { useState } from "react";
import { act, type Action } from "../api";
import { useApp } from "../ctx";
import { Empty, Mono, Ticks } from "../components/ui";
import type { ReviewRow } from "../types";
import { ago, classNames, SOURCE_LABEL } from "../util";

export function Review() {
  const { snap, user } = useApp();
  const team = snap.team ?? null;
  const rows: ReviewRow[] =
    snap.review_queue ??
    snap.decisions
      .filter((d) => d.status === "proposed")
      .map((d) => ({
        id: d.id,
        title: d.title,
        rule: d.rule,
        confidence: d.confidence ?? 0,
        source: d.source.type,
        source_ref: d.source.ref ?? "",
        created_at: d.created_at ?? "",
        approvals: (d.approvals ?? []).map((a) => a.by),
        needed: 1,
        owners: [],
        assigned: [],
        waiting_on: [],
      }));
  const [mine, setMine] = useState(false);
  const shown = mine && user ? rows.filter((r) => r.assigned.includes(user)) : rows;

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h2>Review queue</h2>
          <p className="muted">
            Decisions mined from history wait here until people approve them. Strongest evidence first.
            {team
              ? ` ${team.required_approvals} approval${team.required_approvals > 1 ? "s" : ""} activate a decision; code owners must sign off on their paths.`
              : " Solo mode: one approval activates a decision."}
          </p>
        </div>
        {team && user && (
          <div className="seg">
            <button className={classNames("seg-item", !mine && "on")} onClick={() => setMine(false)}>
              All <span className="seg-count">{rows.length}</span>
            </button>
            <button className={classNames("seg-item", mine && "on")} onClick={() => setMine(true)}>
              Assigned to me <span className="seg-count">{rows.filter((r) => r.assigned.includes(user)).length}</span>
            </button>
          </div>
        )}
      </div>

      <SignIn />

      {shown.length === 0 ? (
        <Empty title={mine ? "Nothing assigned to you" : "Nothing waiting for review"} cmd="why mine">
          New decisions show up here after Why mines commits, PR discussions and agent sessions, or when an agent proposes one over MCP.
        </Empty>
      ) : (
        <div className="review-list">
          {shown.map((r) => (
            <ReviewCard key={r.id} row={r} team={!!team} />
          ))}
        </div>
      )}

      {team && <TeamCard />}
    </div>
  );
}

function ReviewCard({ row, team }: { row: ReviewRow; team: boolean }) {
  const { openDecision, writable, mode, refresh, toast, user, nowMs } = useApp();
  const [busy, setBusy] = useState<Action | null>(null);
  const [notice, setNotice] = useState<{ tone: "ok" | "err"; msg: string } | null>(null);
  const mineToApprove = !!user && row.assigned.includes(user);
  const approvedByMe = !!user && row.approvals.includes(user);

  const run = async (action: Action) => {
    if (mode.kind !== "live") return;
    setBusy(action);
    setNotice(null);
    try {
      const r = await act(mode.repoId, row.id, action, action === "reject" ? { note: "rejected in review" } : {});
      if (r.ok === false && !r.pending) setNotice({ tone: "err", msg: r.message || `${action} failed` });
      else {
        setNotice({ tone: "ok", msg: r.message || `${row.id} ${action}d` });
        toast(r.message || `${row.id} ${action}d`);
      }
      await refresh();
    } catch (e) {
      setNotice({ tone: "err", msg: e instanceof Error ? e.message : String(e) });
    } finally {
      setBusy(null);
    }
  };

  return (
    <article className={classNames("review-card", mineToApprove && "review-mine")}>
      <div className="review-main">
        <button className="review-title" onClick={() => openDecision(row.id)}>
          <Mono className="did">{row.id}</Mono>
          <span>
            <Ticks text={row.title} />
          </span>
        </button>
        <div className="review-rule">
          <Ticks text={row.rule} />
        </div>
        <div className="review-meta muted small">
          <span>{SOURCE_LABEL[row.source] ?? row.source}</span>
          {row.source_ref && <Mono>{row.source_ref.slice(0, 12)}</Mono>}
          <span>confidence {Math.round(row.confidence * 100)}%</span>
          {row.created_at && <span>{ago(row.created_at, nowMs)}</span>}
        </div>
        {notice && <div className={`notice notice-${notice.tone}`}>{notice.msg}</div>}
      </div>
      <div className="review-side">
        {team && (
          <>
            <Progress have={row.approvals.length} need={row.needed} />
            <div className="review-people small">
              {row.approvals.length > 0 && (
                <div>
                  <span className="muted">Approved by </span>
                  {row.approvals.join(", ")}
                </div>
              )}
              {row.owners.length > 0 && (
                <div>
                  <span className="muted">Owners </span>
                  {row.owners.join(", ")}
                </div>
              )}
              {row.waiting_on.length > 0 && (
                <div className="amber-text">Waiting on {row.waiting_on.join(" and ")}</div>
              )}
            </div>
          </>
        )}
        {writable && (
          <div className="review-actions">
            <button className="btn btn-approve btn-sm" disabled={!!busy || approvedByMe} onClick={() => run("approve")}>
              {busy === "approve" ? <span className="spinner" /> : null}
              {approvedByMe ? "Approved" : busy === "approve" ? "Running guard…" : "Approve"}
            </button>
            <button className="btn btn-ghost btn-sm" disabled={!!busy} onClick={() => run("reject")}>
              Reject
            </button>
          </div>
        )}
      </div>
    </article>
  );
}

function Progress({ have, need }: { have: number; need: number }) {
  return (
    <div className="approvals" title={`${have} of ${need} approvals`}>
      {Array.from({ length: Math.max(need, have) }).map((_, i) => (
        <span key={i} className={classNames("approval-pip", i < have && "on")} />
      ))}
      <span className="small muted">
        {have}/{need}
      </span>
    </div>
  );
}

function SignIn() {
  const { snap, mode, user, signIn } = useApp();
  const [tok, setTok] = useState("");
  const [err, setErr] = useState<string | null>(null);
  if (mode.kind !== "live" || !snap.auth_required || user) return null;
  return (
    <form
      className="signin"
      onSubmit={async (e) => {
        e.preventDefault();
        setErr(null);
        if (!(await signIn(tok.trim()))) setErr("That token does not match a team member.");
      }}
    >
      <div>
        <strong>Sign in to review</strong>
        <p className="muted small">This dashboard is in teams mode. Paste the member token your admin set in WHY_DASHBOARD_TOKENS.</p>
      </div>
      <input
        type="password"
        className="input"
        placeholder="member token"
        value={tok}
        onChange={(e) => setTok(e.target.value)}
        autoComplete="off"
      />
      <button className="btn btn-primary" disabled={!tok.trim()}>
        Sign in
      </button>
      {err && <div className="notice notice-err">{err}</div>}
    </form>
  );
}

function TeamCard() {
  const { snap, user } = useApp();
  const team = snap.team!;
  return (
    <section className="team-card">
      <h3>Team</h3>
      <div className="team-grid">
        <div>
          {Object.entries(team.members).map(([who, role]) => (
            <div key={who} className="team-row">
              <span className="user-avatar">{who.slice(0, 1).toUpperCase()}</span>
              <span>{who}</span>
              {who === user && <span className="muted small">you</span>}
              <span className={`role role-${role}`}>{role}</span>
            </div>
          ))}
        </div>
        {Object.keys(team.owners).length > 0 && (
          <div>
            <div className="muted small team-sub">Code owners</div>
            {Object.entries(team.owners).map(([path, people]) => (
              <div key={path} className="team-row">
                <Mono>{path}</Mono>
                <span className="muted small">{people.join(", ")}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </section>
  );
}
