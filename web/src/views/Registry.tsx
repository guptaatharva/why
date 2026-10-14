import { useEffect, useState } from "react";
import { loadBundle, loadRegistry } from "../api";
import { useApp } from "../ctx";
import { Chevron, Code, Empty, Mono, Ticks } from "../components/ui";
import type { GuaranteeBundle, RegistryPackage } from "../types";
import { ago, classNames } from "../util";

export function Registry() {
  const { snap, mode } = useApp();
  const [pkgs, setPkgs] = useState<RegistryPackage[]>(snap.registry ?? []);
  const [sel, setSel] = useState<{ pkg: RegistryPackage; version: string } | null>(null);

  useEffect(() => {
    if (mode.kind !== "live") return;
    loadRegistry()
      .then((r) => setPkgs(r.packages))
      .catch(() => {
        /* keep the snapshot's listing */
      });
  }, [mode.kind]);

  useEffect(() => {
    if (!sel && pkgs.length) {
      const p = pkgs[0];
      setSel({ pkg: p, version: p.versions[p.versions.length - 1] });
    }
  }, [pkgs, sel]);

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h2>Guarantee registry</h2>
          <p className="muted">
            Libraries publish decisions as guarantees of a release. Before a bump merges, <code>why upgrade</code> pulls the
            guarantees for the version you are on and runs them against the new one.
          </p>
        </div>
      </div>

      {pkgs.length === 0 ? (
        <Empty title="No guarantees published yet" cmd="why publish D-0001 --registry https://…">
          A library maintainer publishes active decisions, with their guards, from the library's own repo. Downstream repos then
          get them on every <code>why upgrade</code>.
        </Empty>
      ) : (
        <div className="registry">
          <div className="registry-list">
            {pkgs.map((p) => (
              <div key={`${p.ecosystem}/${p.package}`} className="registry-pkg">
                <div className="registry-name">
                  <span className={`eco eco-${p.ecosystem}`}>{p.ecosystem}</span>
                  <Mono>{p.package}</Mono>
                </div>
                <div className="registry-versions">
                  {p.versions.map((v) => (
                    <button
                      key={v}
                      className={classNames("ver", sel?.pkg === p && sel.version === v && "on")}
                      onClick={() => setSel({ pkg: p, version: v })}
                    >
                      {v}
                    </button>
                  ))}
                </div>
              </div>
            ))}
          </div>
          {sel && <BundleView pkg={sel.pkg} version={sel.version} />}
        </div>
      )}
    </div>
  );
}

function BundleView({ pkg, version }: { pkg: RegistryPackage; version: string }) {
  const { mode, nowMs } = useApp();
  const [bundle, setBundle] = useState<GuaranteeBundle | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);

  useEffect(() => {
    setBundle(null);
    setErr(null);
    if (mode.kind !== "live") {
      setErr("Guarantee sources load from a live Why server.");
      return;
    }
    loadBundle(pkg.ecosystem, pkg.package, version)
      .then(setBundle)
      .catch((e) => setErr(e instanceof Error ? e.message : String(e)));
  }, [pkg, version, mode.kind]);

  return (
    <div className="bundle">
      <div className="bundle-head">
        <Mono>
          {pkg.package}@{version}
        </Mono>
        {bundle && (
          <span className="muted small">
            {bundle.guarantees.length} guarantee{bundle.guarantees.length === 1 ? "" : "s"} · published {ago(bundle.published_at, nowMs)}
          </span>
        )}
      </div>
      {err && <p className="muted small">{err}</p>}
      {!bundle && !err && <span className="spinner" />}
      {bundle?.guarantees.map((g) => (
        <article key={g.id} className="guarantee">
          <button className="guarantee-head" onClick={() => setOpen(open === g.id ? null : g.id)}>
            <Mono className="did">{g.id}</Mono>
            <span className="guarantee-title">
              <Ticks text={g.title} />
            </span>
            <span className="muted small">{g.framework}</span>
            <Chevron open={open === g.id} />
          </button>
          <div className="guarantee-rule">
            <Ticks text={g.rule} />
          </div>
          {g.reason && <p className="muted small">Why: {g.reason}</p>}
          {open === g.id && (
            <div className="guarantee-src">
              <div className="muted small">
                <Mono>{g.filename}</Mono> · from {g.decision} in the library's repo
              </div>
              <Code src={g.source} maxHeight={360} />
            </div>
          )}
        </article>
      ))}
    </div>
  );
}
