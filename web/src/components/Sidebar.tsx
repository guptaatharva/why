import type { ReactNode } from "react";
import { useApp, type View } from "../ctx";
import { classNames } from "../util";
import { Logo } from "./ui";

const ICONS: Record<View, ReactNode> = {
  overview: <path d="M2.5 2.5h4.5v4.5H2.5zM9 2.5h4.5v4.5H9zM2.5 9h4.5v4.5H2.5zM9 9h4.5v4.5H9z" />,
  review: <path d="M6 7.5a2.5 2.5 0 100-5 2.5 2.5 0 000 5zM1.5 13.5c.4-2.3 2.2-3.8 4.5-3.8s4.1 1.5 4.5 3.8M10.5 6.5l1.5 1.5 3-3" />,
  registry: <path d="M8 1.8l5.5 3.1v6.2L8 14.2l-5.5-3.1V4.9zM2.5 4.9L8 8l5.5-3.1M8 8v6.2" />,
  decisions: <path d="M3 3.5h10M3 8h10M3 12.5h6" />,
  checks: <path d="M2.5 8.5l3 3 8-8" />,
  upgrades: <path d="M8 13.5V3M3.5 7.5L8 3l4.5 4.5" />,
  assumptions: <path d="M8 14.5a6.5 6.5 0 100-13 6.5 6.5 0 000 13zM8 11.5v.1M6.2 6.2a1.9 1.9 0 113 1.5c-.7.4-1.2.9-1.2 1.8" />,
};

export function Sidebar({ view }: { view: View }) {
  const { snap, go, mode, user } = useApp();
  const c = snap.counts;
  const queue = snap.review_queue?.length ?? c.proposed ?? 0;
  const mine = user ? (snap.review_queue ?? []).filter((r) => r.assigned.includes(user)).length : 0;
  const assumptionCount = snap.decisions.filter((d) => d.assumption).length;
  const brokenAssumptions = snap.decisions.filter((d) => d.assumption?.verdict === "broken").length;
  const lastUpgrade = snap.upgrades[0];
  const lastCheck = snap.checks[0];

  const items: { v: View; label: string; badge?: ReactNode }[] = [
    { v: "overview", label: "Overview" },
    {
      v: "review",
      label: "Review",
      badge: queue ? <span className="nav-badge amber" title={mine ? `${mine} assigned to you` : "Awaiting review"}>{mine || queue}</span> : null,
    },
    {
      v: "decisions",
      label: "Decisions",
      badge: <span className="nav-count">{snap.decisions.length}</span>,
    },
    {
      v: "checks",
      label: "Checks",
      badge: lastCheck ? <span className={classNames("nav-dot", lastCheck.ok ? "green" : "red")} /> : null,
    },
    {
      v: "upgrades",
      label: "Upgrades",
      badge: lastUpgrade?.verdict === "blocked" ? <span className="nav-dot red" /> : snap.upgrades.length ? <span className="nav-count">{snap.upgrades.length}</span> : null,
    },
    {
      v: "assumptions",
      label: "Assumptions",
      badge: brokenAssumptions ? <span className="nav-badge red">{brokenAssumptions}</span> : assumptionCount ? <span className="nav-count">{assumptionCount}</span> : null,
    },
    {
      v: "registry",
      label: "Registry",
      badge: snap.registry?.length ? <span className="nav-count">{snap.registry.length}</span> : null,
    },
  ];

  return (
    <aside className="sidebar">
      <div className="brand">
        <Logo size={22} />
        <span className="wordmark">why</span>
      </div>

      <div className="repo-card">
        <div className="repo-avatar">{snap.repo.name.slice(0, 1).toUpperCase()}</div>
        <div className="repo-meta">
          <div className="repo-name">{snap.repo.name}</div>
          <div className="repo-sub mono">{snap.repo.github ?? (mode.kind === "static" ? "snapshot" : "local")}</div>
        </div>
      </div>
      {mode.kind === "live" && mode.repos.length > 1 && (
        <select
          className="repo-select"
          value={mode.repoId}
          onChange={(e) => {
            const u = new URL(window.location.href);
            u.searchParams.set("repo", e.target.value);
            window.location.href = u.toString();
          }}
        >
          {mode.repos.map((r) => (
            <option key={r.id} value={r.id}>
              {r.name}
            </option>
          ))}
        </select>
      )}

      <nav className="nav">
        {items.map((it) => (
          <button key={it.v} className={classNames("nav-item", view === it.v && "active")} onClick={() => go(it.v)}>
            <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
              {ICONS[it.v]}
            </svg>
            <span className="nav-label">{it.label}</span>
            {it.badge}
          </button>
        ))}
      </nav>

      <div className="sidebar-spacer" />
      <Engine />
    </aside>
  );
}

function Engine() {
  const { snap } = useApp();
  const m = snap.models;
  if (!m) return null;
  const rows: { role: string; job: string; id: string }[] = [
    { role: "Nano", job: "Mines decisions", id: m.nano },
    { role: "Super", job: "Writes guards", id: m.super },
    { role: "Ultra", job: "Explains breaks", id: m.ultra },
  ];
  return (
    <div className="engine">
      <div className="engine-head">
        <span>Engine</span>
        <span className={classNames("engine-status", m.live ? "on" : "off")}>
          <span className="live-dot" />
          {m.live ? "models live" : "offline templates"}
        </span>
      </div>
      {rows.map((r) => (
        <div key={r.role} className="engine-row" title={r.id}>
          <div className="engine-role">
            <span>{r.role}</span>
            <span className="engine-job">{r.job}</span>
          </div>
          <div className="engine-id mono">{shortModel(r.id)}</div>
        </div>
      ))}
      <div className="engine-row">
        <div className="engine-role">
          <span>Search</span>
          <span className="engine-job">Checks assumptions</span>
        </div>
        <div className={classNames("engine-id mono", !m.tavily && "dim")}>{m.tavily ? "Tavily · configured" : "Tavily · not set"}</div>
      </div>
    </div>
  );
}

function shortModel(id: string): string {
  return (id || "").split("/").pop() ?? id;
}
