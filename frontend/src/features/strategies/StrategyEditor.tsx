// UI-05 Strategy editor — M2.3 (spec 05-strategies/04 « Écrans »). Typed form + equivalent JSON;
// every change is validated by the backend (/validate) and errors are shown on their field.
import { useCallback, useEffect, useRef, useState } from "react";
import { Navigate, useParams } from "react-router-dom";
import { ApiError, fieldErrors, getJson, sendJson } from "../shared/client";
import { useTradingAccess } from "../shared/hooks";
import type { FeatureInfo, PolicyVersionView, StrategyCondition, StrategyConfig, StrategyVersionView } from "../shared/types";
import "../terminal/terminal.css";
import "./versions.css";

const OPS: StrategyCondition["op"][] = ["gte", "gt", "lte", "lt"];
const asError = (e: unknown) => (e instanceof ApiError ? e : new ApiError(0, "NETWORK", String(e), null));

export default function StrategyEditor({ validateDelayMs = 400 }: { validateDelayMs?: number }) {
  const { vid } = useParams();
  const access = useTradingAccess();
  const [version, setVersion] = useState<StrategyVersionView | null>(null);
  const [cfg, setCfg] = useState<StrategyConfig | null>(null);
  const [features, setFeatures] = useState<FeatureInfo[]>([]);
  const [policies, setPolicies] = useState<string[]>([]);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [banner, setBanner] = useState<{ kind: "error" | "provenance"; text: string } | null>(null);
  const [tab, setTab] = useState<"form" | "json">("form");
  const [jsonText, setJsonText] = useState("");
  const [dirty, setDirty] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const load = useCallback(async () => {
    try {
      const [list, feats, pols] = await Promise.all([
        getJson<{ items: StrategyVersionView[] }>("/api/v1/strategies"),
        getJson<{ items: FeatureInfo[] }>("/api/v1/features"),
        getJson<{ items: PolicyVersionView[] }>("/api/v1/risk-policies"),
      ]);
      const v = list.items.find((x) => x.id === vid) ?? null;
      setVersion(v);
      setCfg(v ? structuredClone(v.config) : null);
      setFeatures(feats.items);
      setPolicies([...new Set(pols.items.map((p) => p.policy_id))]);
      if (!v) setBanner({ kind: "error", text: "NOT_FOUND — unknown strategy version" });
    } catch (e) {
      const err = asError(e);
      setBanner({ kind: "error", text: `${err.code} — ${err.detail}` });
    }
  }, [vid]);

  useEffect(() => {
    if (access.kind === "ready") void load();
  }, [access.kind, load]);

  const editable = !!version && version.status === "DRAFT" && access.kind === "ready" && access.roles.includes("ANALYST");

  function change(next: StrategyConfig) {
    setCfg(next);
    setDirty(true);
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => {
      sendJson<{ valid: boolean; errors: { path: string; message: string }[] }>("POST", "/api/v1/strategies/versions/validate", { config: next })
        .then((r) => setErrors(Object.fromEntries(r.errors.map((x) => [x.path, x.message]))))
        .catch(() => undefined);
    }, validateDelayMs);
  }

  if (access.kind === "unauthenticated") return <Navigate to="/login" replace />;
  if (access.kind === "loading") return <div className="hl-state-box">loading…</div>;
  if (access.kind === "forbidden") return <div className="hl-state-box" role="alert">A trading role is required.</div>;
  if (access.kind === "error") return <div className="hl-state-box" role="alert">{access.error.code}</div>;
  if (!cfg || !version) {
    return <div className="hl-page">{banner ? <div className="hl-banner error" role="alert">{banner.text}</div> : <div className="hl-state-box">loading strategy…</div>}</div>;
  }

  const unitOf = (name: string) => features.find((f) => f.name === name)?.unit ?? "";
  const set = (patch: Partial<StrategyConfig>) => change({ ...cfg, ...patch });
  const setExit = (patch: Partial<StrategyConfig["exit"]>) => {
    const exit = { ...cfg.exit, ...patch };
    for (const k of Object.keys(exit) as (keyof typeof exit)[]) if (exit[k] === undefined || exit[k] === "") delete exit[k];
    set({ exit });
  };
  const setCond = (i: number, patch: Partial<StrategyCondition>) => {
    const conditions = cfg.entry.conditions.map((c, j) => (j === i ? { ...c, ...patch } : c));
    set({ entry: { ...cfg.entry, conditions } });
  };
  const err = (path: string) => errors[path];
  const cls = (path: string) => `hl-field${err(path) ? " invalid" : ""}`;

  async function save() {
    if (!cfg || !version) return;
    try {
      const saved = await sendJson<StrategyVersionView>("PUT", `/api/v1/strategies/versions/${version.id}`, { config: cfg },
        { "If-Match": `"${version.config_sha256}"` });
      setVersion(saved);
      setCfg(structuredClone(saved.config));
      setDirty(false);
      setErrors({});
      setBanner({ kind: "provenance", text: `draft saved (hash ${saved.config_sha256.slice(0, 10)})` });
    } catch (e) {
      const ae = asError(e);
      setErrors(fieldErrors(ae));
      setBanner({ kind: "error", text: ae.code === "PRECONDITION_FAILED"
        ? "PRECONDITION_FAILED — this draft was changed elsewhere; reload before saving"
        : `${ae.code} — ${ae.detail}` });
    }
  }

  async function duplicate() {
    try {
      const created = await sendJson<StrategyVersionView>("POST", "/api/v1/strategies/versions",
        { from_version_id: version!.id, version: `${version!.version}-draft` });
      window.location.assign(`/strategies/${created.id}`);
    } catch (e) {
      const ae = asError(e);
      setBanner({ kind: "error", text: `${ae.code} — ${ae.detail}` });
    }
  }

  const holdMin = cfg.exit.max_hold_seconds ? String(cfg.exit.max_hold_seconds / 60) : "";
  const cooldownMin = cfg.cooldown_seconds !== undefined ? String(cfg.cooldown_seconds / 60) : "";
  const universe = cfg.universe as { top_n_by_volume?: number; min_volume_24h_usd?: string };

  return (
    <div className="hl-page">
      <h1 className="hl-title">{cfg.strategy_id} v{version.version} <span className={`hl-status ${version.status.toLowerCase()}`}>{version.status}</span></h1>
      <div className="hl-section">
        <a href="/strategies">← strategies</a>
        {!editable && (
          <p className="hl-readonly-note" data-testid="readonly">
            {version.status === "DRAFT" ? "Read only: editing a draft requires the ANALYST role." : "Read only: a published version never changes. Duplicate it to make a new draft."}
          </p>
        )}
        {banner && <div className={`hl-banner ${banner.kind}`} role={banner.kind === "error" ? "alert" : "status"}>{banner.text}</div>}
        <div className="hl-tabs" role="tablist">
          <button type="button" role="tab" className="hl-btn" aria-selected={tab === "form"} onClick={() => setTab("form")}>Form</button>
          <button type="button" role="tab" className="hl-btn" aria-selected={tab === "json"}
            onClick={() => { setJsonText(JSON.stringify(cfg, null, 2)); setTab("json"); }}>JSON</button>
        </div>

        {tab === "form" ? (
          <fieldset disabled={!editable} style={{ border: 0, padding: 0 }}>
            <h2>Universe</h2>
            <div className="hl-form">
              <label className={cls("universe.top_n_by_volume")}>top pairs by 24 h volume
                <input type="number" min={1} value={universe.top_n_by_volume ?? ""}
                  onChange={(e) => set({ universe: { ...cfg.universe, top_n_by_volume: e.target.value ? Number(e.target.value) : undefined } })} />
              </label>
              <label className={cls("universe.min_volume_24h_usd")}>minimum 24 h volume (USD)
                <input value={universe.min_volume_24h_usd ?? ""}
                  onChange={(e) => set({ universe: { ...cfg.universe, min_volume_24h_usd: e.target.value || undefined } })} />
              </label>
            </div>

            <h2>Entry</h2>
            <label className="hl-field">all conditions or any
              <select value={cfg.entry.operator} onChange={(e) => set({ entry: { ...cfg.entry, operator: e.target.value as "AND" | "OR" } })}>
                <option value="AND">AND — all must pass</option>
                <option value="OR">OR — one is enough</option>
              </select>
            </label>
            <table className="hl-grid" data-testid="conditions">
              <thead><tr><th>feature</th><th>operator</th><th>threshold</th><th>unit</th><th>window (s)</th><th></th></tr></thead>
              <tbody>
                {cfg.entry.conditions.map((c, i) => {
                  const p = `entry.conditions[${i}]`;
                  const rowErr = err(`${p}.feature`) || err(`${p}.unit`) || err(`${p}.threshold`);
                  return (
                    <tr key={i} data-testid={`condition-${i}`}>
                      <td>
                        <select aria-label={`feature ${i + 1}`} className={err(`${p}.feature`) ? "invalid" : ""} value={c.feature}
                          onChange={(e) => setCond(i, { feature: e.target.value, unit: unitOf(e.target.value) })}>
                          {!features.some((f) => f.name === c.feature) && <option value={c.feature}>{c.feature} (unknown)</option>}
                          {features.map((f) => <option key={f.name} value={f.name} title={f.description}>{f.name}</option>)}
                        </select>
                      </td>
                      <td>
                        <select aria-label={`operator ${i + 1}`} value={c.op} onChange={(e) => setCond(i, { op: e.target.value as StrategyCondition["op"] })}>
                          {OPS.map((o) => <option key={o} value={o}>{{ gte: "≥", gt: ">", lte: "≤", lt: "<" }[o]}</option>)}
                        </select>
                      </td>
                      <td><input aria-label={`threshold ${i + 1}`} value={c.threshold} onChange={(e) => setCond(i, { threshold: e.target.value })} /></td>
                      <td><span data-testid={`unit-${i}`} className={err(`${p}.unit`) ? "hl-field-error" : ""}>{c.unit}</span></td>
                      <td><input aria-label={`window ${i + 1}`} type="number" min={1} value={c.window_seconds}
                        onChange={(e) => setCond(i, { window_seconds: Number(e.target.value) })} /></td>
                      <td>
                        <button type="button" className="hl-btn" aria-label={`remove condition ${i + 1}`}
                          onClick={() => set({ entry: { ...cfg.entry, conditions: cfg.entry.conditions.filter((_, j) => j !== i) } })}>×</button>
                        {rowErr && <div className="hl-field-error" role="alert">{rowErr}</div>}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            <button type="button" className="hl-btn" onClick={() => {
              const f = features[0];
              set({ entry: { ...cfg.entry, conditions: [...cfg.entry.conditions,
                { feature: f?.name ?? "", op: "gte", threshold: "0", unit: f?.unit ?? "", window_seconds: 300 }] } });
            }}>+ condition</button>

            <h2>Exit</h2>
            <div className="hl-form">
              <Field label="stop loss (%)" path="exit.initial_stop_pct" err={err} value={cfg.exit.initial_stop_pct}
                onChange={(v) => setExit({ initial_stop_pct: v })} />
              <Field label="take profit (R multiple, empty = none)" path="exit.take_profit_r" err={err} value={cfg.exit.take_profit_r ?? ""}
                onChange={(v) => setExit({ take_profit_r: v || undefined })} />
              <Field label="maximum hold (minutes)" path="exit.max_hold_seconds" err={err} value={holdMin}
                onChange={(v) => setExit({ max_hold_seconds: v ? Math.round(Number(v) * 60) : undefined })} />
              <label className="hl-field">exit when the pair status changes
                <input type="checkbox" checked={!!cfg.exit.exit_on_status_change}
                  onChange={(e) => setExit({ exit_on_status_change: e.target.checked })} />
              </label>
            </div>

            <h2>Other</h2>
            <div className="hl-form">
              <Field label="cooldown per pair (minutes)" path="cooldown_seconds" err={err} value={cooldownMin}
                onChange={(v) => set({ cooldown_seconds: v ? Math.round(Number(v) * 60) : undefined })} />
              <label className={cls("risk_policy_id")}>risk policy
                <select value={cfg.risk_policy_id} onChange={(e) => set({ risk_policy_id: e.target.value })}>
                  {!policies.includes(cfg.risk_policy_id) && <option value={cfg.risk_policy_id}>{cfg.risk_policy_id} (unknown)</option>}
                  {policies.map((p) => <option key={p} value={p}>{p}</option>)}
                </select>
                {err("risk_policy_id") && <span className="hl-field-error">{err("risk_policy_id")}</span>}
              </label>
            </div>
          </fieldset>
        ) : (
          <textarea className="hl-json" aria-label="JSON" readOnly={!editable} value={jsonText}
            onChange={(e) => setJsonText(e.target.value)}
            onBlur={() => {
              if (!editable) return;
              try {
                change(JSON.parse(jsonText) as StrategyConfig);
                setBanner(null);
              } catch (e) {
                setBanner({ kind: "error", text: `invalid JSON — ${String(e)}` });
              }
            }} />
        )}
        {err("") && <div className="hl-banner error" role="alert">{err("")}</div>}

        <div className="hl-actions" style={{ marginTop: 12 }}>
          {editable && <button type="button" className="hl-btn" disabled={!dirty} onClick={() => void save()}>Save draft</button>}
          {!editable && access.roles.includes("ANALYST") && <button type="button" className="hl-btn" onClick={() => void duplicate()}>Duplicate</button>}
        </div>
      </div>
    </div>
  );
}

function Field({ label, path, value, onChange, err }: {
  label: string; path: string; value: string; onChange: (v: string) => void; err: (p: string) => string | undefined;
}) {
  return (
    <label className={`hl-field${err(path) ? " invalid" : ""}`}>{label}
      <input value={value} onChange={(e) => onChange(e.target.value)} />
      {err(path) && <span className="hl-field-error">{err(path)}</span>}
    </label>
  );
}
