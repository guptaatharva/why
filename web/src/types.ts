// Shapes mirror src/why/models.py and src/why/server/api.py.

export type Status = "proposed" | "active" | "violated" | "superseded" | "retired";
export const STATUSES: Status[] = ["proposed", "active", "violated", "superseded", "retired"];

export type Result = "pass" | "fail" | "error" | "skipped";
export type Verdict = "holds" | "broken" | "unclear" | "unchecked";

export interface Source {
  type: string;
  ref?: string;
  quote?: string;
  link?: string;
  author?: string;
  date?: string;
}

export interface Assumption {
  claim: string;
  query?: string;
  verdict: Verdict | string;
  checked_at?: string;
  explanation?: string;
  urls?: string[];
}

export interface GuardRef {
  path: string;
  language?: string;
  framework?: string;
  generated_by?: string;
  flaky?: boolean;
  stability_runs?: number;
}

export interface Decision {
  id: string;
  title: string;
  rule: string;
  reason?: string;
  status: Status;
  kind?: string;
  params?: Record<string, unknown>;
  scope?: { files?: string[]; symbols?: string[] };
  source: Source;
  confidence?: number;
  assumption?: Assumption;
  guard?: GuardRef;
  supersedes?: string;
  superseded_by?: string;
  approvals?: { by: string; at: string }[];
  created_at?: string;
  updated_at?: string;
  // added by the snapshot endpoint
  last_result?: Result | null;
  last_context?: string | null;
  last_evidence_at?: string | null;
  evidence_count?: number;
}

export interface Evidence {
  decision_id: string;
  result: Result | string;
  context: string;
  commit?: string;
  run_id?: string;
  log?: string;
  duration_s?: number;
  detail?: Record<string, unknown>;
  timestamp: string;
}

export interface LogEntry {
  ts: string;
  decision_id: string;
  from: string | null;
  to: string;
  actor: string;
  note?: string;
}

export interface CheckItem {
  decision_id: string;
  title: string;
  reason: string;
  result: Result | string;
  log: string;
  source_link?: string;
  source_ref?: string;
}

export interface CheckReport {
  run_id: string;
  context: string;
  commit: string;
  items: CheckItem[];
  duration_s: number;
  created_at: string;
  ok: boolean;
  violations: string[];
  pr?: number;
  pr_url?: string;
  head_sha?: string;
  [k: string]: unknown;
}

export interface RootCause {
  api: string;
  args?: unknown;
  before?: string;
  after?: string;
  note?: string;
  candidates?: string[];
}

export interface Break {
  decision_id: string;
  title: string;
  reason: string;
  log: string;
  root_cause: RootCause | null;
  explanation: string;
}

export interface UpgradeReport {
  id: string;
  package: string;
  from_version: string;
  to_version: string;
  verdict: "blocked" | "approved" | "error" | string;
  breaks: Break[];
  guards_run: number;
  passed_before: number;
  passed_after: number;
  guarantees?: string[];
  commit?: string;
  error?: string;
  created_at: string;
}

export interface MergeReport {
  id?: string;
  ok: boolean;
  base: string;
  other: string;
  guards_run?: number;
  branches?: string[];
  contradictions?: {
    decision_id: string;
    title: string;
    reason: string;
    from: string; // "base" or the branch that introduced the decision
    broken_by?: string[]; // the other branch(es) whose code breaks it
    log: string;
  }[];
  /** Older reports list bare ids; newer ones say which branch minted which rule. */
  id_clashes?: (string | { decision_id: string; branch: string; rule: string; other_rule: string })[];
  conflicted_files?: string[];
  text_conflict?: boolean;
  error?: string;
  detail?: string;
  created_at: string;
}

export interface AssumptionReport {
  created_at: string;
  results: { decision_id: string; title?: string; claim?: string; verdict: string; explanation?: string; urls?: string[] }[];
}

export interface Models {
  live: boolean;
  base_url?: string;
  nano: string;
  super: string;
  ultra: string;
  tavily: boolean;
}

export interface RepoInfo {
  id: string;
  name: string;
  github?: string | null;
  head?: string | null;
}

export interface Snapshot {
  repo: RepoInfo;
  generated_at: string;
  counts: Partial<Record<Status, number>>;
  decisions: Decision[];
  evidence: Evidence[];
  log: LogEntry[];
  checks: CheckReport[];
  upgrades: UpgradeReport[];
  merges: MergeReport[];
  assumptions: AssumptionReport[];
  models: Models;
  write_enabled: boolean;
  /** What coding agents asked Why over MCP (why_decisions_for / why_propose_decision / why_run_guards). */
  consults?: Consult[];
  /** Optional, static snapshots only: guard file contents keyed by decision id. */
  guard_sources?: Record<string, string>;
  /** Teams mode: null in solo mode. */
  team?: TeamInfo | null;
  review_queue?: ReviewRow[];
  /** Changes need a member token (WHY_DASHBOARD_TOKENS on the server). */
  auth_required?: boolean;
  /** Packages with published guarantees on this server's registry (F10). */
  registry?: RegistryPackage[];
}

export interface Consult {
  ts: string;
  tool: "why_decisions_for" | "why_propose_decision" | "why_run_guards" | string;
  args: Record<string, unknown>;
  decisions: string[];
  agent: string;
}

export interface RepoListItem {
  id: string;
  name: string;
  github?: string | null;
  counts: Partial<Record<Status, number>>;
  last_check: { ok: boolean; at: string; violations: string[] } | null;
}

export interface DecisionDetail {
  decision: Decision;
  guard_source: string | null;
  evidence: Evidence[];
  log: LogEntry[];
}

export interface ReviewRow {
  id: string;
  title: string;
  rule: string;
  confidence: number;
  source: string;
  source_ref: string;
  created_at: string;
  approvals: string[];
  needed: number;
  owners: string[];
  assigned: string[];
  waiting_on: string[];
}

export interface TeamInfo {
  members: Record<string, "viewer" | "reviewer" | "admin" | string>;
  owners: Record<string, string[]>;
  required_approvals: number;
}

export interface RegistryPackage {
  ecosystem: "pypi" | "npm" | string;
  package: string;
  versions: string[];
}

export interface Guarantee {
  id: string;
  decision: string;
  title: string;
  rule: string;
  reason: string;
  language: string;
  framework: string;
  filename: string;
  source: string;
}

export interface GuaranteeBundle {
  ecosystem: string;
  package: string;
  version: string;
  published_at: string;
  guarantees: Guarantee[];
}
