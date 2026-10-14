import { useApp } from "../ctx";
import { Empty, ExternalLink, Mono, StatusPill, Ticks, VerdictPill } from "../components/ui";
import { ago, fullTime, host } from "../util";

const ORDER: Record<string, number> = { broken: 0, unclear: 1, unchecked: 2, holds: 3 };

export function Assumptions() {
  const { snap, openDecision, nowMs } = useApp();
  const rows = snap.decisions.filter((d) => d.assumption);
  rows.sort((a, b) => (ORDER[a.assumption!.verdict] ?? 9) - (ORDER[b.assumption!.verdict] ?? 9));
  const lastRun = snap.assumptions[0]?.created_at;

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h2>Assumptions</h2>
          <p className="muted">
            Some decisions rest on facts outside the repo: a vendor's rate limit, an API's contract. Why re-checks them on the web.
          </p>
        </div>
        {lastRun && <span className="muted small">last checked {ago(lastRun, nowMs)}</span>}
      </div>

      {rows.length === 0 ? (
        <Empty title="No outside assumptions recorded" cmd="why assume">
          When a mined decision depends on an external fact, Why records it as an assumption and checks it with web search.
        </Empty>
      ) : (
        <div className="assumptions">
          {rows.map((d) => {
            const a = d.assumption!;
            return (
              <article key={d.id} className={`acard acard-${a.verdict}`}>
                <div className="acard-head">
                  <VerdictPill verdict={a.verdict} />
                  <span className="muted small" title={fullTime(a.checked_at)}>
                    {a.checked_at ? `checked ${ago(a.checked_at, nowMs)}` : "never checked"}
                  </span>
                </div>
                <div className="acard-claim">“{a.claim}”</div>
                {a.explanation && <p className="acard-expl">{a.explanation}</p>}
                {!!a.urls?.length && (
                  <div className="urls">
                    {a.urls.map((u) => (
                      <ExternalLink key={u} href={u}>
                        {host(u)}
                      </ExternalLink>
                    ))}
                  </div>
                )}
                <button className="acard-decision" onClick={() => openDecision(d.id)}>
                  <span className="muted small">Supports</span>
                  <Mono className="did">{d.id}</Mono>
                  <span className="acard-dtitle">
                    <Ticks text={d.title} />
                  </span>
                  <StatusPill status={d.status} />
                </button>
              </article>
            );
          })}
        </div>
      )}
    </div>
  );
}
