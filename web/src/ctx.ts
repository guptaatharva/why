import { createContext, useContext } from "react";
import type { Mode } from "./api";
import type { Decision, Snapshot } from "./types";

export type View = "overview" | "review" | "decisions" | "checks" | "upgrades" | "assumptions" | "registry";
export const VIEWS: View[] = ["overview", "review", "decisions", "checks", "upgrades", "assumptions", "registry"];

export interface AppCtx {
  snap: Snapshot;
  mode: Mode;
  nowMs: number;
  byId: Map<string, Decision>;
  openDecision: (id: string) => void;
  go: (view: View, query?: string) => void;
  refresh: () => Promise<void>;
  toast: (msg: string, tone?: "ok" | "err") => void;
  writable: boolean;
  /** Signed-in team member (teams mode with tokens), "dashboard" when no sign-in is needed, else null. */
  user: string | null;
  signIn: (token: string) => Promise<boolean>;
  signOut: () => void;
}

export const Ctx = createContext<AppCtx | null>(null);

export function useApp(): AppCtx {
  const c = useContext(Ctx);
  if (!c) throw new Error("no app context");
  return c;
}
