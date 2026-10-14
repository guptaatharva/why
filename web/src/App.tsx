import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { loadSnapshot, resolveMode, runCheck, setToken, whoami, type Mode } from "./api";
import { Ctx, VIEWS, type AppCtx, type View } from "./ctx";
import type { Snapshot } from "./types";
import { Sidebar } from "./components/Sidebar";
import { Logo } from "./components/ui";
import { Overview } from "./views/Overview";
import { Decisions } from "./views/Decisions";
import { Checks } from "./views/Checks";
import { Upgrades } from "./views/Upgrades";
import { Assumptions } from "./views/Assumptions";
import { Review } from "./views/Review";
import { Registry } from "./views/Registry";
import { DecisionDrawer } from "./views/DecisionDrawer";
import { ago, sha } from "./util";

const POLL_MS = 5000;

interface Route {
  view: View;
  decision: string | null;
  query: URLSearchParams;
}

function parseHash(): Route {
  const raw = window.location.hash.replace(/^#\/?/, "");
  const [path, qs] = raw.split("?");
  const [v, d] = path.split("/");
  const view = (VIEWS as string[]).includes(v) ? (v as View) : "overview";
  return { view, decision: d ? decodeURIComponent(d) : null, query: new URLSearchParams(qs ?? "") };
}

function setHash(view: View, decision: string | null, query?: string) {
  const q = query ? `?${query}` : "";
  window.location.hash = `#/${view}${decision ? `/${encodeURIComponent(decision)}` : ""}${q}`;
}

const TITLES: Record<View, string> = {
  overview: "Overview",
  review: "Review",
  decisions: "Decisions",
  checks: "Checks",
  upgrades: "Upgrades",
  assumptions: "Assumptions",
  registry: "Registry",
};

export default function App() {
  const [route, setRoute] = useState<Route>(parseHash);
  const [mode, setMode] = useState<Mode | null>(null);
  const [snap, setSnap] = useState<Snapshot | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [fetchedAt, setFetchedAt] = useState<number>(0);
  const [nowMs, setNowMs] = useState(Date.now());
  const [toasts, setToasts] = useState<{ id: number; msg: string; tone: "ok" | "err" }[]>([]);
  const [checking, setChecking] = useState(false);
  const [user, setUser] = useState<string | null>(null);
  const modeRef = useRef<Mode | null>(null);

  const identify = useCallback(async () => {
    if (modeRef.current?.kind !== "live") return setUser(null);
    try {
      setUser((await whoami()).user);
    } catch {
      setUser(null);
    }
  }, []);

  useEffect(() => {
    const onHash = () => setRoute(parseHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  useEffect(() => {
    const t = setInterval(() => setNowMs(Date.now()), 5000);
    return () => clearInterval(t);
  }, []);

  const refresh = useCallback(async () => {
    const m = modeRef.current;
    if (!m) return;
    try {
      const s = await loadSnapshot(m);
      setSnap(s);
      setError(null);
      setFetchedAt(Date.now());
    } catch (e) {
      // A live server that went away mid-demo: drop to the static snapshot rather than a blank screen.
      if (m.kind === "live") {
        const fallback: Mode = { kind: "static", url: "./snapshot.json", reason: "Lost the API, showing the bundled snapshot" };
        try {
          const s = await loadSnapshot(fallback);
          modeRef.current = fallback;
          setMode(fallback);
          setSnap(s);
          setError(null);
          setFetchedAt(Date.now());
          return;
        } catch {
          /* keep the error below */
        }
      }
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    let alive = true;
    (async () => {
      const repo = new URLSearchParams(window.location.search).get("repo");
      const m = await resolveMode(repo);
      if (!alive) return;
      modeRef.current = m;
      setMode(m);
      await refresh();
      await identify();
    })();
    const t = setInterval(() => {
      if (document.visibilityState === "visible") void refresh();
    }, POLL_MS);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, [refresh, identify]);

  const toast = useCallback((msg: string, tone: "ok" | "err" = "ok") => {
    const id = Date.now() + Math.random();
    setToasts((ts) => [...ts, { id, msg, tone }]);
    setTimeout(() => setToasts((ts) => ts.filter((t) => t.id !== id)), 4200);
  }, []);

  const ctx: AppCtx | null = useMemo(() => {
    if (!snap || !mode) return null;
    return {
      snap,
      mode,
      nowMs,
      byId: new Map(snap.decisions.map((d) => [d.id, d])),
      openDecision: (id: string) => setHash(route.view, id, route.query.toString()),
      go: (view: View, query?: string) => setHash(view, null, query),
      refresh,
      toast,
      writable: mode.kind === "live" && snap.write_enabled && (!snap.auth_required || !!user),
      user,
      signIn: async (tok: string) => {
        setToken(tok);
        const who = (await whoami().catch(() => null))?.user ?? null;
        if (!who) setToken("");
        setUser(who);
        return !!who;
      },
      signOut: () => {
        setToken("");
        setUser(null);
      },
    };
  }, [snap, mode, nowMs, route, refresh, toast, user]);

  if (!ctx) {
    return (
      <div className="boot">
        <Logo size={28} />
        <div className="boot-text">{error ? `Could not load data: ${error}` : "Loading decisions…"}</div>
      </div>
    );
  }

  const { view } = route;
  const closeDrawer = () => setHash(route.view, null, route.query.toString());

  const doCheck = async () => {
    if (mode?.kind !== "live") return;
    setChecking(true);
    try {
      const r = await runCheck(mode.repoId);
      toast(r.ok ? "Check passed. Every guard holds." : `Check failed: ${r.violations.join(", ")}`, r.ok ? "ok" : "err");
      await refresh();
    } catch (e) {
      toast(`Check failed to run: ${e instanceof Error ? e.message : e}`, "err");
    } finally {
      setChecking(false);
    }
  };

  return (
    <Ctx.Provider value={ctx}>
      <div className="shell">
        <Sidebar view={view} />
        <main className="main">
          <header className="topbar">
            <div className="crumbs">
              <span className="crumb-repo">{snap!.repo.name}</span>
              <span className="crumb-sep">/</span>
              <span className="crumb-view">{TITLES[view]}</span>
            </div>
            <div className="topbar-right">
              {snap!.repo.head && (
                <span className="head-chip" title={`HEAD ${snap!.repo.head}`}>
                  <svg width="12" height="12" viewBox="0 0 16 16" aria-hidden>
                    <circle cx="8" cy="8" r="2.6" fill="none" stroke="currentColor" strokeWidth="1.6" />
                    <path d="M1 8h4.4M10.6 8H15" stroke="currentColor" strokeWidth="1.6" />
                  </svg>
                  <span className="mono">{sha(snap!.repo.head)}</span>
                </span>
              )}
              <LiveIndicator mode={mode!} fetchedAt={fetchedAt} nowMs={nowMs} error={error} />
              {snap!.auth_required && mode?.kind === "live" && (
                user ? (
                  <button className="user-chip" onClick={ctx.signOut} title="Sign out">
                    <span className="user-avatar">{user.slice(0, 1).toUpperCase()}</span>
                    {user}
                    {snap!.team?.members[user] && <span className="muted small">{snap!.team.members[user]}</span>}
                  </button>
                ) : (
                  <button className="btn" onClick={() => setHash("review", null)}>Sign in</button>
                )
              )}
              {ctx.writable && (
                <button className="btn btn-primary" onClick={doCheck} disabled={checking}>
                  {checking ? <span className="spinner" /> : <PlayIcon />}
                  {checking ? "Running guards…" : "Run check"}
                </button>
              )}
            </div>
          </header>
          <div className="content" key={view}>
            {view === "overview" && <Overview />}
            {view === "decisions" && <Decisions query={route.query} />}
            {view === "checks" && <Checks />}
            {view === "upgrades" && <Upgrades />}
            {view === "assumptions" && <Assumptions />}
            {view === "review" && <Review />}
            {view === "registry" && <Registry />}
          </div>
        </main>
        {route.decision && <DecisionDrawer id={route.decision} onClose={closeDrawer} />}
        <div className="toasts">
          {toasts.map((t) => (
            <div key={t.id} className={`toast toast-${t.tone}`}>
              {t.msg}
            </div>
          ))}
        </div>
      </div>
    </Ctx.Provider>
  );
}

function PlayIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 16 16" aria-hidden>
      <path d="M5 3.5v9l7-4.5z" fill="currentColor" />
    </svg>
  );
}

function LiveIndicator({ mode, fetchedAt, nowMs, error }: { mode: Mode; fetchedAt: number; nowMs: number; error: string | null }) {
  if (mode.kind === "static") {
    return (
      <span className="live live-static" title={mode.reason}>
        <span className="live-dot" />
        Snapshot
      </span>
    );
  }
  const secs = Math.max(0, Math.round((nowMs - fetchedAt) / 1000));
  return (
    <span className={`live ${error ? "live-err" : ""}`} title={error ?? `Polling every ${POLL_MS / 1000}s`}>
      <span className="live-dot" />
      {error ? "Reconnecting" : secs < 8 ? "Live" : `Live · ${ago(new Date(fetchedAt).toISOString(), nowMs)}`}
    </span>
  );
}
