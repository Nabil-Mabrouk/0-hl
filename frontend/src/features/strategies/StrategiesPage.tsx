// UI-04 Strategies — M2.3 (spec 05-strategies/04 « Écrans »). The backend enforces roles and rules;
// buttons are only shown to the roles that may use them.
import { useCallback, useEffect, useState } from "react";
import { Navigate } from "react-router-dom";
import { ApiError, getJson, sendJson } from "../shared/client";
import { DASH } from "../shared/format";
import { useTradingAccess } from "../shared/hooks";
import type { Change, StrategyVersionView } from "../shared/types";
import "../terminal/terminal.css";
import "./versions.css";

type Dialog =
  | { kind: "duplicate"; v: StrategyVersionView }
  | { kind: "publish"; v: StrategyVersionView }
  | { kind: "deprecate"; v: StrategyVersionView };

export function nextVersion(version: string, taken: string[]): string {
  const m = /^(\d+)\.(\d+)\.(\d+)$/.exec(version);
  if (!m) return `${version}-2`;
  let minor = Number(m[2]) + 1;
  while (taken.includes(`${m[1]}.${minor}.0`)) minor += 1;
  return `${m[1]}.${minor}.0`;
}

const short = (sha: string) => sha.slice(0, 10);
const asError = (e: unknown) => (e instanceof ApiError ? e : new ApiError(0, "NETWORK", String(e), null));

export default function StrategiesPage() {
  const access = useTradingAccess();
  const [items, setItems] = useState<StrategyVersionView[] | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [dialog, setDialog] = useState<Dialog | null>(null);
  const [text, setText] = useState({ version: "", reason: "", backtest: "" });
  const [selected, setSelected] = useState<string[]>([]);
  const [diff, setDiff] = useState<{ a: string; b: string; changes: Change[] } | null>(null);

  const load = useCallback(async () => {
    try {
      setItems((await getJson<{ items: StrategyVersionView[] }>("/api/v1/strategies")).items);
    } catch (e) {
      setError(asError(e));
    }
  }, []);

  useEffect(() => {
    if (access.kind === "ready") void load();
  }, [access.kind, load]);

  if (access.kind === "unauthenticated") return <Navigate to="/login" replace />;
  if (access.kind === "loading") return <div className="hl-state-box">loading…</div>;
  if (access.kind === "forbidden") return <div className="hl-state-box" role="alert">A trading role is required.</div>;
  if (access.kind === "error") return <div className="hl-state-box" role="alert">{access.error.code}</div>;

  const canDraft = access.roles.includes("ANALYST");
  const canGovern = access.roles.includes("RISK_OFFICER");
  const groups = new Map<string, StrategyVersionView[]>();
  for (const v of items ?? []) groups.set(v.strategy_id, [...(groups.get(v.strategy_id) ?? []), v]);

  function open(d: Dialog) {
    const taken = (items ?? []).filter((x) => x.strategy_id === d.v.strategy_id).map((x) => x.version);
    setText({ version: d.kind === "duplicate" ? nextVersion(d.v.version, taken) : "", reason: "", backtest: "" });
    setError(null);
    setDialog(d);
  }

  async function submit() {
    if (!dialog) return;
    try {
      if (dialog.kind === "duplicate") {
        const created = await sendJson<StrategyVersionView>("POST", "/api/v1/strategies/versions",
          { from_version_id: dialog.v.id, version: text.version });
        window.location.assign(`/strategies/${created.id}`);
        return;
      }
      const body = dialog.kind === "publish"
        ? { reason: text.reason, ...(text.backtest ? { backtest_ref: text.backtest } : {}) }
        : { reason: text.reason };
      await sendJson("POST", `/api/v1/strategies/versions/${dialog.v.id}/${dialog.kind}`, body);
      setDialog(null);
      await load();
    } catch (e) {
      setError(asError(e));
    }
  }

  function toggle(id: string) {
    setDiff(null);
    setSelected((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s.slice(-1), id]));
  }

  async function compare() {
    const [a, b] = selected;
    try {
      const r = await getJson<{ changes: Change[] }>(`/api/v1/strategies/diff?a=${a}&b=${b}`);
      setDiff({ a, b, changes: r.changes });
    } catch (e) {
      setError(asError(e));
    }
  }

  const label = (id: string) => {
    const v = items?.find((x) => x.id === id);
    return v ? `v${v.version}` : id;
  };

  return (
    <div className="hl-page">
      <h1 className="hl-title">Strategies</h1>
      {error && !dialog && <div className="hl-banner error" role="alert">{error.code} — {error.detail}</div>}
      {!items && !error && <div className="hl-state-box">loading strategies…</div>}
      {items && items.length === 0 && <div className="hl-state-box">No strategy yet.</div>}
      {[...groups.entries()].map(([sid, versions]) => (
        <section key={sid} className="hl-section" data-testid={`strategy-${sid}`}>
          <h2>{sid}</h2>
          <table className="hl-table">
            <thead>
              <tr><th></th><th>version</th><th>status</th><th>hash</th><th>author</th><th>published</th><th>bots</th><th>actions</th></tr>
            </thead>
            <tbody>
              {versions.map((v) => (
                <tr key={v.id} data-testid={`version-${v.version}`}>
                  <td>
                    <input type="checkbox" aria-label={`compare v${v.version}`} checked={selected.includes(v.id)}
                      onChange={() => toggle(v.id)} />
                  </td>
                  <td><a href={`/strategies/${v.id}`}>v{v.version}</a></td>
                  <td><span className={`hl-status ${v.status.toLowerCase()}`}>{v.status}</span></td>
                  <td title={v.config_sha256}><code>{short(v.config_sha256)}</code></td>
                  <td>{v.created_by}</td>
                  <td>{v.published_at ? v.published_at.slice(0, 16).replace("T", " ") : DASH}</td>
                  <td>{v.bots.length ? v.bots.join(", ") : DASH}</td>
                  <td>
                    <div className="hl-actions">
                      {canDraft && <button type="button" className="hl-btn" onClick={() => open({ kind: "duplicate", v })}>Duplicate</button>}
                      {canGovern && v.status === "DRAFT" && (
                        <button type="button" className="hl-btn" onClick={() => open({ kind: "publish", v })}>Publish</button>)}
                      {canGovern && v.status === "PUBLISHED" && (
                        <button type="button" className="hl-btn danger" onClick={() => open({ kind: "deprecate", v })}>Deprecate</button>)}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      ))}
      {items && items.length > 1 && (
        <div className="hl-section">
          <button type="button" className="hl-btn" disabled={selected.length !== 2} onClick={() => void compare()}>
            Compare selected versions
          </button>
          {diff && (
            <table className="hl-table hl-diff" data-testid="diff">
              <thead><tr><th>field</th><th>{label(diff.a)}</th><th>{label(diff.b)}</th></tr></thead>
              <tbody>
                {diff.changes.length === 0 && <tr><td colSpan={3}>identical</td></tr>}
                {diff.changes.map((c) => (
                  <tr key={c.path}><td><code>{c.path}</code></td><td>{fmt(c.before)}</td><td className="changed">{fmt(c.after)}</td></tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}
      {dialog && (
        <div className="hl-dialog-backdrop">
          <div className="hl-dialog" role="dialog" aria-label={`${dialog.kind} strategy`}>
            <h3>{dialog.kind === "duplicate" ? "Duplicate" : dialog.kind === "publish" ? "Publish" : "Deprecate"} {dialog.v.strategy_id} v{dialog.v.version}</h3>
            {dialog.kind === "duplicate" ? (
              <label className="hl-field">new version
                <input value={text.version} onChange={(e) => setText({ ...text, version: e.target.value })} />
              </label>
            ) : (
              <>
                <p>{dialog.kind === "publish"
                  ? "A published version can never be changed again. Bots can then be attached to it."
                  : "Bots already attached keep running; no bot can be attached to it any more."}</p>
                <label className="hl-field">reason (required)
                  <input value={text.reason} onChange={(e) => setText({ ...text, reason: e.target.value })} />
                </label>
                {dialog.kind === "publish" && (
                  <label className="hl-field">backtest report (dataset sha, optional)
                    <input value={text.backtest} onChange={(e) => setText({ ...text, backtest: e.target.value })} />
                  </label>
                )}
              </>
            )}
            {error && <div className="hl-banner error" role="alert">{error.code} — {error.detail}</div>}
            <div className="hl-actions">
              <button type="button" className="hl-btn" onClick={() => setDialog(null)}>cancel</button>
              <button type="button" className={`hl-btn${dialog.kind === "deprecate" ? " danger" : ""}`}
                disabled={dialog.kind === "duplicate" ? !text.version.trim() : !text.reason.trim()}
                onClick={() => void submit()}>
                confirm {dialog.kind}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export function fmt(v: unknown): string {
  if (v === undefined || v === null) return DASH;
  return typeof v === "object" ? JSON.stringify(v) : String(v);
}
