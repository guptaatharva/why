export function parseTs(ts?: string | null): number {
  if (!ts) return NaN;
  // Python isoformat may omit the zone; treat bare timestamps as UTC.
  const hasZone = /[zZ]|[+-]\d\d:?\d\d$/.test(ts);
  return Date.parse(hasZone ? ts : ts + "Z");
}

export function ago(ts?: string | null, nowMs: number = Date.now()): string {
  const t = parseTs(ts);
  if (Number.isNaN(t)) return "never";
  const s = Math.max(0, Math.round((nowMs - t) / 1000));
  if (s < 45) return "just now";
  const m = Math.round(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.round(h / 24);
  if (d < 30) return `${d}d ago`;
  return new Date(t).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}

export function fullTime(ts?: string | null): string {
  const t = parseTs(ts);
  if (Number.isNaN(t)) return "";
  return new Date(t).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function sha(s?: string | null, n = 7): string {
  if (!s) return "";
  if (/^[0-9a-f]{8,}$/i.test(s)) return s.slice(0, n);
  return s.length > 24 ? s.slice(0, 24) + "…" : s;
}

export function host(url: string): string {
  try {
    return new URL(url).host.replace(/^www\./, "");
  } catch {
    return url;
  }
}

export function commitUrl(github: string | null | undefined, commit?: string): string | null {
  if (!github || !commit || !/^[0-9a-f]{7,40}$/i.test(commit)) return null;
  return `https://github.com/${github}/commit/${commit}`;
}

export const SOURCE_LABEL: Record<string, string> = {
  commit: "Commit",
  pr_comment: "PR comment",
  pr_body: "PR description",
  agent_log: "Agent log",
  entire_checkpoint: "Agent checkpoint",
  manual: "Manual",
};

export const KIND_LABEL: Record<string, string> = {
  call_count: "call count",
  returns: "return value",
  forbid_pattern: "forbidden pattern",
  custom: "custom",
};

export const CONTEXT_LABEL: Record<string, string> = {
  local: "Local",
  pr: "Pull request",
  merge: "Merge",
  upgrade: "Upgrade",
  stability: "Stability",
  dashboard: "Dashboard",
  assumption: "Assumption",
  mcp: "Agent self-check",
  push: "Push",
};

/** Split two strings into common prefix, differing middles and common suffix. */
export function splitDiff(a: string, b: string): { pre: string; a: string; b: string; post: string } {
  let i = 0;
  while (i < a.length && i < b.length && a[i] === b[i]) i++;
  let j = 0;
  while (j < a.length - i && j < b.length - i && a[a.length - 1 - j] === b[b.length - 1 - j]) j++;
  return { pre: a.slice(0, i), a: a.slice(i, a.length - j), b: b.slice(i, b.length - j), post: a.slice(a.length - j) };
}

export function fmtArgs(args: unknown): string {
  if (args == null) return "";
  if (Array.isArray(args)) return args.map((x) => (typeof x === "string" ? x : JSON.stringify(x))).join(", ");
  if (typeof args === "object") {
    const o = args as Record<string, unknown>;
    const pos = Array.isArray(o.args) ? (o.args as unknown[]) : [];
    const kw = o.kwargs && typeof o.kwargs === "object" ? Object.entries(o.kwargs as Record<string, unknown>) : [];
    if (pos.length || kw.length) {
      return [
        ...pos.map((x) => (typeof x === "string" ? x : JSON.stringify(x))),
        ...kw.map(([k, v]) => `${k}=${typeof v === "string" ? v : JSON.stringify(v)}`),
      ].join(", ");
    }
    return JSON.stringify(args);
  }
  return String(args);
}

// ---- tiny syntax highlighter (python / js), returns token spans ----------------

export type Tok = { t: "kw" | "str" | "com" | "num" | "fn" | "dec" | "plain"; v: string };

const PY_KW = new Set(
  "def class return if elif else for while in not and or is None True False import from as with try except finally raise assert lambda yield pass break continue global nonlocal async await const let var function new typeof".split(
    " ",
  ),
);

export function highlight(src: string): Tok[] {
  const out: Tok[] = [];
  const re =
    /("""[\s\S]*?"""|'''[\s\S]*?'''|f?"(?:\\.|[^"\\\n])*"|f?'(?:\\.|[^'\\\n])*'|`(?:\\.|[^`\\])*`)|(#[^\n]*|\/\/[^\n]*)|(@[\w.]+)|(\b\d+(?:\.\d+)?\b)|([A-Za-z_]\w*)(?=\s*\()|([A-Za-z_]\w*)/g;
  let last = 0;
  let m: RegExpExecArray | null;
  while ((m = re.exec(src))) {
    if (m.index > last) out.push({ t: "plain", v: src.slice(last, m.index) });
    if (m[1]) out.push({ t: "str", v: m[1] });
    else if (m[2]) out.push({ t: "com", v: m[2] });
    else if (m[3]) out.push({ t: "dec", v: m[3] });
    else if (m[4]) out.push({ t: "num", v: m[4] });
    else if (m[5]) out.push({ t: PY_KW.has(m[5]) ? "kw" : "fn", v: m[5] });
    else if (m[6]) out.push({ t: PY_KW.has(m[6]) ? "kw" : "plain", v: m[6] });
    last = re.lastIndex;
  }
  if (last < src.length) out.push({ t: "plain", v: src.slice(last) });
  return out;
}

export function classNames(...xs: (string | false | null | undefined)[]): string {
  return xs.filter(Boolean).join(" ");
}
