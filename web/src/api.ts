import type { DecisionDetail, GuaranteeBundle, RegistryPackage, RepoListItem, Snapshot } from "./types";

const TOKEN_KEY = "why.token";

/** Member token for teams-mode dashboards. Per-viewer convenience only, so storage failures are ignored. */
export function getToken(): string {
  try {
    return window.localStorage.getItem(TOKEN_KEY) ?? "";
  } catch {
    return "";
  }
}

export function setToken(tok: string) {
  try {
    if (tok) window.localStorage.setItem(TOKEN_KEY, tok);
    else window.localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* private window: the token lasts for this page only */
  }
  memToken = tok;
}

let memToken = "";

function authHeaders(): Record<string, string> {
  const t = getToken() || memToken;
  return t ? { Authorization: `Bearer ${t}` } : {};
}

export type Mode =
  | { kind: "live"; repoId: string; repos: RepoListItem[] }
  | { kind: "static"; url: string; reason: string };

async function getJSON<T>(url: string, init?: RequestInit): Promise<T> {
  const r = await fetch(url, { cache: "no-store", ...init });
  if (!r.ok) {
    let msg = `${r.status} ${r.statusText}`;
    try {
      const body = await r.json();
      if (body && typeof body.detail === "string") msg = body.detail;
    } catch {
      /* not json */
    }
    throw new Error(msg);
  }
  return (await r.json()) as T;
}

function snapshotParam(): string | null {
  try {
    return new URLSearchParams(window.location.search).get("snapshot");
  } catch {
    return null;
  }
}

/** Decide where data comes from: the live API, or a static snapshot (fallback / ?snapshot=). */
export async function resolveMode(preferredRepo: string | null): Promise<Mode> {
  const forced = snapshotParam();
  if (forced) return { kind: "static", url: forced, reason: "Loaded from ?snapshot=" };
  try {
    const repos = await getJSON<RepoListItem[]>("/api/repos");
    if (!Array.isArray(repos) || repos.length === 0) throw new Error("no repos");
    const repoId = repos.find((r) => r.id === preferredRepo)?.id ?? repos[0].id;
    return { kind: "live", repoId, repos };
  } catch {
    return { kind: "static", url: "./snapshot.json", reason: "API unreachable, showing the bundled snapshot" };
  }
}

export function loadSnapshot(mode: Mode): Promise<Snapshot> {
  if (mode.kind === "static") return getJSON<Snapshot>(mode.url);
  return getJSON<Snapshot>(`/api/repos/${encodeURIComponent(mode.repoId)}`);
}

export async function loadDecision(mode: Mode, snap: Snapshot, id: string): Promise<DecisionDetail | null> {
  if (mode.kind === "live") {
    try {
      return await getJSON<DecisionDetail>(
        `/api/repos/${encodeURIComponent(mode.repoId)}/decisions/${encodeURIComponent(id)}`,
      );
    } catch {
      /* fall through to snapshot-built detail */
    }
  }
  return detailFromSnapshot(snap, id);
}

export function detailFromSnapshot(snap: Snapshot, id: string): DecisionDetail | null {
  const decision = snap.decisions.find((d) => d.id === id);
  if (!decision) return null;
  return {
    decision,
    guard_source: snap.guard_sources?.[id] ?? null,
    evidence: snap.evidence.filter((e) => e.decision_id === id).slice(-50),
    log: snap.log.filter((l) => l.decision_id === id),
  };
}

export type Action = "approve" | "reject" | "retire" | "supersede";

export async function act(
  repoId: string,
  id: string,
  action: Action,
  body: Record<string, unknown> = {},
): Promise<{ ok: boolean; pending?: boolean; message?: string }> {
  return getJSON(`/api/repos/${encodeURIComponent(repoId)}/decisions/${encodeURIComponent(id)}/${action}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify(body),
  });
}

export async function runCheck(repoId: string): Promise<{ ok: boolean; violations: string[] }> {
  return getJSON(`/api/repos/${encodeURIComponent(repoId)}/check`, { method: "POST", headers: authHeaders() });
}

export function whoami(): Promise<{ user: string | null; auth_required: boolean; write_enabled: boolean }> {
  return getJSON("/api/whoami", { headers: authHeaders() });
}

export function loadRegistry(): Promise<{ packages: RegistryPackage[] }> {
  return getJSON("/api/registry");
}

export function loadBundle(ecosystem: string, pkg: string, version: string): Promise<GuaranteeBundle> {
  return getJSON(`/api/registry/${ecosystem}/${pkg.split("/").map(encodeURIComponent).join("/")}/${encodeURIComponent(version)}`);
}
