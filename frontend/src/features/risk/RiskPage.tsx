// UI-06 Risk policies — M2.3 (spec 05-strategies/04 « Écrans », « Règles » 5). A loosened limit is
// flagged; its publication waits for the 10-minute review delay (ADR-006), shown as a countdown.
import { useCallback, useEffect, useState } from "react";
import { Navigate } from "react-router-dom";
import { ApiError, fieldErrors, getJson, sendJson } from "../shared/client";
import { DASH } from "../shared/format";
import { useTradingAccess } from "../shared/hooks";
import type { Change, PolicyVersionView } from "../shared/types";
import "../terminal/terminal.css";
import "../strategies/versions.css";

type Kind = "dec" | "int" | "flag";
export const POLICY_FIELDS: { key: string; label: string; unit: string; kind: Kind }[] = [
  { key: "max_trade_risk_pct", label: "risk per trade", unit: "% of equity", kind: "dec" },
  { key: "max_daily_loss_pct", label: "daily loss limit", unit: "%", kind: "dec" },
  { key: "max_drawdown_pct", label: "maximum drawdown", unit: "%", kind: "dec" },
  { key: "max_token_exposure_pct", label: "exposure per pair", unit: "%", kind: "dec" },
  { key: "max_portfolio_exposure_pct", label: "total exposure", unit: "%", kind: "dec" },
  { key: "max_open_positions", label: "open positions", unit: "count", kind: "int" },
  { key: "max_slippage_pct", label: "maximum slippage", unit: "%", kind: "dec" },
  { key: "max_spread_bps", label: "maximum spread", unit: "bps", kind: "dec" },
  { key: "max_abnormal_move_pct", label: "abnormal 5-min move", unit: "%", kind: "dec" },
  { key: "min_volume_24h_usd", label: "minimum 24 h volume", unit: "USD", kind: "dec" },
  { key: "min_liquidity_usd", label: "minimum liquidity", unit: "USD", kind: "dec" },
  { key: "cooldown_seconds", label: "cooldown per pair", unit: "s", kind: "int" },
  { key: "authorization_ttl_seconds", label: "sizing approval lifetime", unit: "s", kind: "int" },
  { key: "kill_switch", label: "kill switch (block all entries)", unit: "", kind: "flag" },
];

const asError = (e: unknown) => (e instanceof ApiError ? e : new ApiError(0, "NETWORK", String(e), null));
const show = (v: unknown) => (v === undefined || v === null || v === "" ? DASH : String(v));

export function remaining(confirmAfter: string | null | undefined, now: number): number {
  return confirmAfter ? Math.max(0, Math.ceil((Date.parse(confirmAfter) - now) / 1000)) : 0;
}

export default function RiskPage({ now: nowFn = () => Date.now() }: { now?: () => number }) {
  const access = useTradingAccess();
  const [items, setItems] = useState<PolicyVersionView[] | null>(null);
  const [current, setCurrent] = useState<PolicyVersionView | null>(null);
  const [cfg, setCfg] = useState<Record<string, unknown> | null>(null);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [banner, setBanner] = useState<{ kind: "error" | "provenance" | "stale"; text: string } | null>(null);
  const [reason, setReason] = useState("");
  const [newVersion, setNewVersion] = useState("");
  const [now, setNow] = useState(nowFn());

  useEffect(() => {
    const t = setInterval(() => setNow(nowFn()), 1000);
    return () => clearInterval(t);
  }, [nowFn]);

  const open = useCallback(async (v: { policy_id: string; version: string }) => {
    const d = await getJson<PolicyVersionView>(`/api/v1/risk-policies/${v.policy_id}/versions/${encodeURIComponent(v.version)}`);
    setCurrent(d);
    setCfg({ ...d.config });
    setErrors({});
  }, []);

  const load = useCallback(async (select?: { policy_id: string; version: string }) => {
    try {
      const list = (await getJson<{ items: PolicyVersionView[] }>("/api/v1/risk-policies")).items;
      setItems(list);
      const target = select ?? list.find((x) => x.status === "DRAFT") ?? list[list.length - 1];
      if (target) await open(target);
    } catch (e) {
      const ae = asError(e);
      setBanner({ kind: "error", text: `${ae.code} — ${ae.detail}` });
    }
  }, [open]);

  useEffect(() => {
    if (access.kind === "ready") void load();
  }, [access.kind, load]);

  if (access.kind === "unauthenticated") return <Navigate to="/login" replace />;
  if (access.kind === "loading") return <div className="hl-state-box">loading…</div>;
  if (access.kind === "forbidden") return <div className="hl-state-box" role="alert">A trading role is required.</div>;
  if (access.kind === "error") return <div className="hl-state-box" role="alert">{access.error.code}</div>;

  const canGovern = access.roles.includes("RISK_OFFICER");
  const editable = !!current && current.status === "DRAFT" && canGovern;
  const reference = current?.compared_to ? items?.find((x) => x.id === current.compared_to?.id) : null;
  const effects = new Map<string, Change>((current?.changes ?? []).map((c) => [c.path, c]));
  const left = remaining(current?.pending_publication?.confirm_after, now);

  async function act(fn: () => Promise<PolicyVersionView | void>, after?: { policy_id: string; version: string }) {
    try {
      setBanner(null);
      const r = await fn();
      await load(after ?? (r ? { policy_id: r.policy_id, version: r.version } : undefined));
      return r;
    } catch (e) {
      const ae = asError(e);
      setErrors(fieldErrors(ae));
      setBanner({ kind: "error", text: ae.code === "PRECONDITION_FAILED"
        ? "PRECONDITION_FAILED — this draft was changed elsewhere; reload before saving" : `${ae.code} — ${ae.detail}` });
    }
  }

  function setField(key: string, kind: Kind, raw: string | boolean) {
    const next = { ...(cfg ?? {}) };
    if (kind === "flag") next[key] = raw;
    else if (raw === "") delete next[key];
    else next[key] = kind === "int" ? Number(raw) : raw;
    setCfg(next);
  }

  return (
    <div className="hl-page">
      <h1 className="hl-title">Risk policies</h1>
      {banner && <div className={`hl-banner ${banner.kind}`} role={banner.kind === "error" ? "alert" : "status"}>{banner.text}</div>}
      {!items && !banner && <div className="hl-state-box">loading policies…</div>}
      {items && (
        <section className="hl-section">
          <table className="hl-table">
            <thead><tr><th>policy</th><th>version</th><th>status</th><th>published</th><th>bots</th></tr></thead>
            <tbody>
              {items.map((v) => (
                <tr key={v.id} data-testid={`policy-${v.policy_id}-${v.version}`} aria-selected={current?.id === v.id}>
                  <td>{v.policy_id}</td>
                  <td><button type="button" className="hl-btn" onClick={() => void act(() => open(v))}>v{v.version}</button></td>
                  <td><span className={`hl-status ${v.status.toLowerCase()}`}>{v.status}</span>{v.pending_publication && " · pending"}</td>
                  <td>{v.published_at ? v.published_at.slice(0, 16).replace("T", " ") : DASH}</td>
                  <td>{v.bots.length ? v.bots.join(", ") : DASH}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
      {current && cfg && (
        <section className="hl-section" data-testid="policy-editor">
          <h2>{current.policy_id} v{current.version} <span className={`hl-status ${current.status.toLowerCase()}`}>{current.status}</span></h2>
          {!editable && (
            <p className="hl-readonly-note">{current.status === "DRAFT" ? "Read only: policies are edited by the RISK_OFFICER role."
              : "Read only: a published policy never changes. Duplicate it to make a new draft."}</p>
          )}
          <table className="hl-grid">
            <thead><tr><th>limit</th><th>this version</th><th>unit</th><th>published {reference ? `v${reference.version}` : ""}</th><th>effect</th></tr></thead>
            <tbody>
              {POLICY_FIELDS.map((f) => {
                const eff = effects.get(f.key)?.effect;
                return (
                  <tr key={f.key} data-testid={`limit-${f.key}`} className={eff === "plus souple" ? "hl-effect-looser" : ""}>
                    <td>{f.label}</td>
                    <td>
                      {f.kind === "flag" ? (
                        <input type="checkbox" aria-label={f.label} disabled={!editable} checked={!!cfg[f.key]}
                          onChange={(e) => setField(f.key, f.kind, e.target.checked)} />
                      ) : (
                        <input aria-label={f.label} disabled={!editable} className={errors[f.key] ? "invalid" : ""}
                          value={cfg[f.key] === undefined ? "" : String(cfg[f.key])}
                          onChange={(e) => setField(f.key, f.kind, e.target.value)} />
                      )}
                    </td>
                    <td>{f.unit}</td>
                    <td>{show(reference?.config[f.key])}</td>
                    <td>{eff === "plus souple" ? "looser" : eff === "plus strict" ? <span className="hl-effect-stricter">stricter</span> : ""}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {errors[""] && <div className="hl-banner error" role="alert">{errors[""]}</div>}

          {current.pending_publication && (
            <div className="hl-banner stale" role="status" data-testid="pending">
              Loosened policy: publication requested by {current.pending_publication.requested_by}.{" "}
              {left > 0 ? `Confirmation possible in ${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")}.` : "Confirmation possible now."}
            </div>
          )}

          <div className="hl-actions" style={{ marginTop: 12 }}>
            {editable && (
              <button type="button" className="hl-btn" onClick={() => void act(() => sendJson<PolicyVersionView>("PUT",
                `/api/v1/risk-policies/versions/${current.id}`, { config: cfg }, { "If-Match": `"${current.config_sha256}"` }))}>
                Save draft
              </button>
            )}
            {editable && !current.pending_publication && (
              <>
                <input aria-label="reason" placeholder="reason (required to publish)" value={reason} onChange={(e) => setReason(e.target.value)} />
                <button type="button" className="hl-btn" disabled={!reason.trim()} onClick={() => void act(async () => {
                  const r = await sendJson<PolicyVersionView>("POST", `/api/v1/risk-policies/versions/${current.id}/publish`, { reason });
                  setReason("");
                  return r;
                })}>Publish</button>
              </>
            )}
            {editable && current.pending_publication && (
              <button type="button" className="hl-btn" disabled={left > 0} onClick={() => void act(() =>
                sendJson<PolicyVersionView>("POST", `/api/v1/risk-policies/versions/${current.id}/publish/confirm`))}>
                Confirm publication
              </button>
            )}
            {canGovern && (
              <>
                <input aria-label="new version" placeholder="new version" value={newVersion} onChange={(e) => setNewVersion(e.target.value)} />
                <button type="button" className="hl-btn" disabled={!newVersion.trim()} onClick={() => void act(async () => {
                  const r = await sendJson<PolicyVersionView>("POST", "/api/v1/risk-policies/versions",
                    { from_version_id: current.id, version: newVersion });
                  setNewVersion("");
                  return r;
                })}>Duplicate</button>
              </>
            )}
            {canGovern && current.status === "PUBLISHED" && (
              <button type="button" className="hl-btn danger" disabled={!reason.trim()} onClick={() => void act(() =>
                sendJson<PolicyVersionView>("POST", `/api/v1/risk-policies/versions/${current.id}/deprecate`, { reason }))}>
                Deprecate
              </button>
            )}
            {canGovern && current.status === "PUBLISHED" && (
              <input aria-label="reason" placeholder="reason (required to deprecate)" value={reason} onChange={(e) => setReason(e.target.value)} />
            )}
          </div>
        </section>
      )}
    </div>
  );
}
