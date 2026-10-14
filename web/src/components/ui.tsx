import { useState, type ReactNode } from "react";
import { classNames, highlight, splitDiff } from "../util";

export function StatusPill({ status, size }: { status: string; size?: "sm" | "lg" }) {
  return (
    <span className={classNames("pill", `s-${status}`, size === "lg" && "pill-lg")}>
      <span className="pill-dot" />
      {status}
    </span>
  );
}

export function ResultBadge({ result }: { result?: string | null }) {
  if (!result) return <span className="result r-none">no runs</span>;
  const label = { pass: "pass", fail: "fail", error: "error", skipped: "skipped" }[result] ?? result;
  return (
    <span className={classNames("result", `r-${result}`)}>
      <ResultIcon result={result} />
      {label}
    </span>
  );
}

export function ResultIcon({ result, size = 12 }: { result: string; size?: number }) {
  if (result === "pass")
    return (
      <svg width={size} height={size} viewBox="0 0 16 16" aria-hidden>
        <path d="M3.5 8.5l3 3 6-7" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    );
  if (result === "fail" || result === "error")
    return (
      <svg width={size} height={size} viewBox="0 0 16 16" aria-hidden>
        <path d="M4.5 4.5l7 7M11.5 4.5l-7 7" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
      </svg>
    );
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" aria-hidden>
      <path d="M4 8h8" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
    </svg>
  );
}

export function VerdictPill({ verdict }: { verdict: string }) {
  return (
    <span className={classNames("pill", `v-${verdict}`)}>
      <span className="pill-dot" />
      {verdict}
    </span>
  );
}

export function Mono({ children, className }: { children: ReactNode; className?: string }) {
  return <span className={classNames("mono", className)}>{children}</span>;
}

export function Chip({ children, title }: { children: ReactNode; title?: string }) {
  return (
    <span className="chip" title={title}>
      {children}
    </span>
  );
}

export function Code({ src, maxHeight }: { src: string; maxHeight?: number }) {
  const toks = highlight(src);
  return (
    <pre className="code" style={maxHeight ? { maxHeight } : undefined}>
      <code>
        {toks.map((t, i) =>
          t.t === "plain" ? (
            t.v
          ) : (
            <span key={i} className={`tk-${t.t}`}>
              {t.v}
            </span>
          ),
        )}
      </code>
    </pre>
  );
}

export function LogBlock({ text, collapsed = true }: { text: string; collapsed?: boolean }) {
  const [open, setOpen] = useState(!collapsed);
  if (!text) return null;
  return (
    <div className="logblock">
      <button className="linkbtn" onClick={() => setOpen(!open)}>
        <Chevron open={open} /> {open ? "Hide" : "Show"} failure log
      </button>
      {open && <pre className="log">{text.trim()}</pre>}
    </div>
  );
}

export function Chevron({ open }: { open: boolean }) {
  return (
    <svg className={classNames("chev", open && "open")} width="12" height="12" viewBox="0 0 16 16" aria-hidden>
      <path d="M6 4l4 4-4 4" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export function Empty({ title, children, cmd }: { title: string; children?: ReactNode; cmd?: string }) {
  return (
    <div className="empty">
      <div className="empty-mark">
        <Logo size={22} />
      </div>
      <div className="empty-title">{title}</div>
      {children && <div className="empty-body">{children}</div>}
      {cmd && (
        <div className="empty-cmd">
          <span className="prompt">$</span> {cmd}
        </div>
      )}
    </div>
  );
}

export function Logo({ size = 20 }: { size?: number }) {
  // An open bracket holding a check: "what must stay true".
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden className="logo">
      <rect x="1" y="1" width="22" height="22" rx="6" className="logo-bg" />
      <path d="M7 12.5l3 3 7-7.5" fill="none" className="logo-fg" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export function ExternalLink({ href, children, className }: { href?: string | null; children: ReactNode; className?: string }) {
  if (!href) return <span className={className}>{children}</span>;
  return (
    <a href={href} target="_blank" rel="noreferrer" className={classNames("ext", className)}>
      {children}
      <svg width="11" height="11" viewBox="0 0 16 16" aria-hidden>
        <path d="M6 3h7v7M13 3L5 11" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    </a>
  );
}

/** Before/after values with the differing span highlighted. */
export function ValueDiff({
  before,
  after,
  beforeLabel,
  afterLabel,
}: {
  before: string;
  after: string;
  beforeLabel: string;
  afterLabel: string;
}) {
  const d = splitDiff(before, after);
  return (
    <div className="vdiff">
      <div className="vdiff-row del">
        <div className="vdiff-gutter" title="behaviour the decision relies on">✓</div>
        <div className="vdiff-label">{beforeLabel}</div>
        <code className="vdiff-val">
          {d.pre}
          <mark>{d.a}</mark>
          {d.post}
        </code>
      </div>
      <div className="vdiff-row add">
        <div className="vdiff-gutter" title="new behaviour">✕</div>
        <div className="vdiff-label">{afterLabel}</div>
        <code className="vdiff-val">
          {d.pre}
          <mark>{d.b}</mark>
          {d.post}
        </code>
      </div>
    </div>
  );
}

export function Section({ title, aside, children }: { title: ReactNode; aside?: ReactNode; children: ReactNode }) {
  return (
    <section className="section">
      <div className="section-head">
        <h3>{title}</h3>
        {aside}
      </div>
      {children}
    </section>
  );
}

/** Render `backticked` spans as inline code. */
export function Ticks({ text }: { text: string }) {
  if (!text || !text.includes("`")) return <>{text}</>;
  return (
    <>
      {text.split(/(`[^`]+`)/g).map((part, i) =>
        part.length > 2 && part.startsWith("`") && part.endsWith("`") ? (
          <code key={i} className="inline">
            {part.slice(1, -1)}
          </code>
        ) : (
          part
        ),
      )}
    </>
  );
}
