import { useEffect, useState, type ReactNode } from "react";
import { act, detailFromSnapshot, loadDecision, type Action } from "../api";
import { useApp } from "../ctx";
import type { DecisionDetail } from "../types";
import { Chip, Code, ExternalLink, LogBlock, Mono, ResultIcon, StatusPill, Ticks, VerdictPill } from "../components/ui";
import { ago, classNames, commitUrl, CONTEXT_LABEL, fullTime, host, KIND_LABEL, sha, SOURCE_LABEL } from "../util";

const GEN_LABEL: Record<string, string> = {
  template: "Template",
  "nemotron-super": "Nemotron Super",
  human: "Human",
};

export function DecisionDrawer({ id, onClose }: { id: string; onClose: () => void }) {
  const { snap, mode, writable, refresh, toast, openDecision, nowMs } = useApp();
  const [detail, setDetail] = useState<DecisionDetail | null>(() => detailFromSnapshot(snap, id));
  const [busy, setBusy] = useState<Action | null>(null);
  // Failures that need the human to act (no model key, guard fails on current code) stay visible.
  const [notice, setNotice] = useState<{ tone: "ok" | "err"; msg: string } | null>(null);
  useEffect(() => setNotice(null), [id]);
  const [supersedeOpen, setSupersedeOpen] = useState(false);
  const [reason, setReason] = useState("");
  const [rule, setRule] = useState("");

  // Reload detail whenever the decision or the snapshot changes (live polling).
  useEffect(() => {
    let alive = true;
    loadDecision(mode, snap, id).then((d) => {
      if (alive && d) setDetail(d);
    });
    return () => {
      alive = false;
    };
  }, [id, mode, snap]);

  useEffect(() => {
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = prev;
    };
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const d = detail?.decision;
  // Prefer the snapshot row for status/last_result so the drawer moves with the live poll.
  const row = snap.decisions.find((x) => x.id === id);

  const run = async (action: Action, body: Record<string, unknown> = {}) => {
    if (mode.kind !== "live") return;
    setBusy(action);
    try {
      setNotice(null);
      const r = await act(mode.repoId, id, action, body);
      if (r.ok === false && !r.pending) {
        setNotice({ tone: "err", msg: r.message || `${action} failed` });
        toast(`${id}: ${action} did not complete`, "err");
      } else toast(r.message || `${id} ${action === "approve" ? "approved" : action + "d"}`, "ok");
      setSupersedeOpen(false);
      await refresh();
    } catch (e) {
      const msg = `${action} failed: ${e instanceof Error ? e.message : e}`;
      setNotice({ tone: "err", msg });
      toast(msg, "err");
    } finally {
      setBusy(null);
    }
  };

  return (
    <>
      <div className="scrim" onClick={onClose} />
      <aside className="drawer" role="dialog" aria-label={`Decision ${id}`}>
        {!d ? (
          <div className="drawer-body">
            <div className="drawer-top">
              <Mono className="did">{id}</Mono>
              <CloseBtn onClose={onClose} />
            </div>
            <p className="muted">This decision is not in the current snapshot.</p>
          </div>
        ) : (
          <div className="drawer-body">
            <div className="drawer-top">
              <Mono className="did did-lg">{d.id}</Mono>
              <StatusPill status={row?.status ?? d.status} />
              <span className="muted small">updated {ago(d.updated_at, nowMs)}</span>
              <CloseBtn onClose={onClose} />
            </div>
            <h2 className="drawer-title">
              <Ticks text={d.title} />
            </h2>

            {notice && (
              <div className={`notice notice-${notice.tone}`} role="status">
                <Ticks text={notice.msg} />
                <button className="notice-x" aria-label="Dismiss" onClick={() => setNotice(null)}>×</button>
              </div>
            )}
            {writable && (
              <div className="actions">
                {(row?.status ?? d.status) === "proposed" && (
                  <>
                    <button className="btn btn-approve" disabled={!!busy} onClick={() => run("approve")}>
                      {busy === "approve" ? <span className="spinner" /> : <ResultIcon result="pass" />}
                      {busy === "approve" ? "Compiling guard…" : "Approve & guard"}
                    </button>
                    <button className="btn btn-ghost" disabled={!!busy} onClick={() => run("reject")}>
                      Reject
                    </button>
                  </>
                )}
                {["active", "violated"].includes(row?.status ?? d.status) && (
                  <>
                    <button className="btn" disabled={!!busy} onClick={() => setSupersedeOpen(!supersedeOpen)}>
                      Supersede…
                    </button>
                    <button className="btn btn-ghost" disabled={!!busy} onClick={() => run("retire")}>
                      Retire
                    </button>
                  </>
                )}
              </div>
            )}
            {supersedeOpen && (
              <form
                className="supersede"
                onSubmit={(e) => {
                  e.preventDefault();
                  if (reason.trim()) run("supersede", { reason: reason.trim(), ...(rule.trim() ? { rule: rule.trim() } : {}) });
                }}
              >
                <label>
                  Why is this changing?
                  <input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="The gateway now supports idempotency keys" autoFocus />
                </label>
                <label>
                  New rule <span className="muted">(optional)</span>
                  <input value={rule} onChange={(e) => setRule(e.target.value)} placeholder={d.rule} />
                </label>
                <div className="actions">
                  <button className="btn btn-primary" disabled={!reason.trim() || !!busy}>
                    {busy === "supersede" ? "Superseding…" : "Supersede"}
                  </button>
                  <button type="button" className="btn btn-ghost" onClick={() => setSupersedeOpen(false)}>
                    Cancel
                  </button>
                </div>
              </form>
            )}

            {(d.supersedes || d.superseded_by) && (
              <div className="lineage">
                {d.supersedes && (
                  <span>
                    Supersedes{" "}
                    <button className="dlink mono" onClick={() => openDecision(d.supersedes!)}>
                      {d.supersedes}
                    </button>
                  </span>
                )}
                {d.superseded_by && (
                  <span>
                    Superseded by{" "}
                    <button className="dlink mono" onClick={() => openDecision(d.superseded_by!)}>
                      {d.superseded_by}
                    </button>
                  </span>
                )}
              </div>
            )}

            <div className="rule-box">
              <div className="label">Rule</div>
              <div className="rule-text">{d.rule}</div>
            </div>

            <div className="kv-why">
              <div className="label">Why</div>
              <div className="why-big">{d.reason || <span className="muted">No reason recorded.</span>}</div>
            </div>

            <DSection title="Source">
              <div className="source-meta">
                <Chip>{SOURCE_LABEL[d.source.type] ?? d.source.type}</Chip>
                {d.source.ref && <Mono className="muted">{d.source.ref.length > 20 ? sha(d.source.ref) : d.source.ref}</Mono>}
                {d.source.author && <span className="muted">{d.source.author}</span>}
                {d.source.date && <span className="muted">{fullTime(d.source.date)}</span>}
                {d.source.link && (
                  <ExternalLink href={d.source.link} className="push-right">
                    Open source
                  </ExternalLink>
                )}
              </div>
              {d.source.quote && (
                <blockquote className="quote">
                  <p>{d.source.quote}</p>
                </blockquote>
              )}
              {typeof d.confidence === "number" && (
                <div className="conf-row">
                  <span className="muted small">Mining confidence</span>
                  <div className="meter">
                    <div className="meter-fill" style={{ width: `${Math.round(d.confidence * 100)}%` }} />
                  </div>
                  <Mono className="small">{Math.round(d.confidence * 100)}%</Mono>
                </div>
              )}
            </DSection>

            {(d.scope?.symbols?.length || d.scope?.files?.length) ? (
              <DSection title="Scope">
                <div className="chips">
                  {(d.scope?.symbols ?? []).map((s) => (
                    <Chip key={s}>
                      <Mono>{s}</Mono>
                    </Chip>
                  ))}
                  {(d.scope?.files ?? []).map((s) => (
                    <Chip key={s}>
                      <Mono className="muted">{s}</Mono>
                    </Chip>
                  ))}
                </div>
              </DSection>
            ) : null}

            <DSection title="Guard">
              {d.guard ? (
                <>
                  <div className="guard-meta">
                    <Mono className="guard-path">{d.guard.path}</Mono>
                    <span className="kind">{KIND_LABEL[d.kind ?? "custom"] ?? d.kind}</span>
                    <span className={classNames("gen", d.guard.generated_by === "nemotron-super" && "gen-model")}>
                      written by {GEN_LABEL[d.guard.generated_by ?? "template"] ?? d.guard.generated_by}
                    </span>
                    {d.guard.framework && <span className="muted small">{d.guard.framework}</span>}
                    {!!d.guard.stability_runs && <span className="muted small">{d.guard.stability_runs}× stable</span>}
                    {d.guard.flaky && <span className="tag tag-amber">flaky, skipped</span>}
                  </div>
                  {detail?.guard_source ? (
                    <Code src={detail.guard_source} maxHeight={360} />
                  ) : (
                    <div className="note">
                      Guard source is not available in {mode.kind === "static" ? "a static snapshot" : "this view"}. Run{" "}
                      <Mono>why show {d.id}</Mono> to print it.
                    </div>
                  )}
                </>
              ) : (
                <div className="note">
                  {d.status === "proposed" ? (
                    <>
                      No guard yet. Approving compiles one{d.kind === "custom" ? " with Nemotron Super" : " from a template"}:{" "}
                      <Mono>why approve {d.id}</Mono>
                    </>
                  ) : (
                    <>No guard recorded for this decision.</>
                  )}
                </div>
              )}
            </DSection>

            {d.assumption && (
              <DSection title="Outside assumption">
                <div className="assume">
                  <div className="assume-head">
                    <VerdictPill verdict={d.assumption.verdict} />
                    {d.assumption.checked_at && <span className="muted small">checked {ago(d.assumption.checked_at, nowMs)}</span>}
                  </div>
                  <div className="assume-claim">{d.assumption.claim}</div>
                  {d.assumption.explanation && <p className="assume-expl">{d.assumption.explanation}</p>}
                  {!!d.assumption.urls?.length && (
                    <div className="urls">
                      {d.assumption.urls.map((u) => (
                        <ExternalLink key={u} href={u}>
                          {host(u)}
                        </ExternalLink>
                      ))}
                    </div>
                  )}
                </div>
              </DSection>
            )}

            <DSection title="History">
              {!detail?.log.length ? (
                <div className="note">No transitions recorded.</div>
              ) : (
                <>
                  <div className="states">
                    {stateTrail(detail.log).map((s, i, arr) => (
                      <span key={i} className="state-step">
                        <StatusPill status={s} />
                        {i < arr.length - 1 && <span className="state-arrow">→</span>}
                      </span>
                    ))}
                  </div>
                  <ol className="history">
                    {[...detail.log].reverse().map((l, i) => (
                      <li key={i}>
                        <span className={`dot dot-s-${l.to}`} />
                        <div>
                          <div>
                            {l.from ? (
                              <>
                                <span className="muted">{l.from}</span> → <strong>{l.to}</strong>
                              </>
                            ) : (
                              <strong>created as {l.to}</strong>
                            )}
                            <span className="muted"> by {l.actor}</span>
                          </div>
                          {l.note && <div className="muted small">{l.note}</div>}
                        </div>
                        <time className="muted small" title={fullTime(l.ts)}>
                          {ago(l.ts, nowMs)}
                        </time>
                      </li>
                    ))}
                  </ol>
                </>
              )}
            </DSection>
            <DSection title={`Evidence${detail?.evidence.length ? ` · ${detail.evidence.length}` : ""}`}>
              {!detail?.evidence.length ? (
                <div className="note">No guard runs yet.</div>
              ) : (
                <ol className="timeline">
                  {[...detail.evidence].reverse().map((e, i) => (
                    <li key={`${e.run_id}-${i}`} className={`tl-item tl-${e.result}`}>
                      <span className="tl-icon">
                        <ResultIcon result={e.result} />
                      </span>
                      <div className="tl-body">
                        <div className="tl-line">
                          <span className={`result-word r-${e.result}`}>{e.result}</span>
                          <span className="muted">{CONTEXT_LABEL[e.context] ?? e.context}</span>
                          {e.commit && (
                            <ExternalLink href={commitUrl(snap.repo.github, e.commit)}>
                              <Mono>{sha(e.commit)}</Mono>
                            </ExternalLink>
                          )}
                          {e.context === "upgrade" && e.detail && (
                            <Mono className="muted">
                              {String((e.detail as Record<string, unknown>).package ?? "")} {String((e.detail as Record<string, unknown>).to ?? "")}
                            </Mono>
                          )}
                          <time className="push-right muted small" title={fullTime(e.timestamp)}>
                            {ago(e.timestamp, nowMs)}
                          </time>
                        </div>
                        {(e.result === "fail" || e.result === "error") && e.log && <LogBlock text={e.log} />}
                      </div>
                    </li>
                  ))}
                </ol>
              )}
            </DSection>

          </div>
        )}
      </aside>
    </>
  );
}

function stateTrail(log: { from: string | null; to: string }[]): string[] {
  const out: string[] = [];
  for (const l of log) {
    if (!out.length && l.from) out.push(l.from);
    if (out[out.length - 1] !== l.to) out.push(l.to);
  }
  return out;
}

function DSection({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="dsection">
      <h4>{title}</h4>
      {children}
    </section>
  );
}

function CloseBtn({ onClose }: { onClose: () => void }) {
  return (
    <button className="icon-btn push-right" onClick={onClose} aria-label="Close">
      <svg width="14" height="14" viewBox="0 0 16 16" aria-hidden>
        <path d="M4 4l8 8M12 4l-8 8" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
      </svg>
    </button>
  );
}
