// UI-12 Stats — M2.5 (spec 03-ecrans/M2-trades-et-statistiques « Écrans »). Performance excludes
// deposits; a version with fewer than 20 trades is flagged « sample too small ».
import { useCallback, useEffect, useMemo, useState } from "react";
import { Navigate, useSearchParams } from "react-router-dom";
import { ApiError, getJson } from "../shared/client";
import { DASH, fmtPct, fmtSignedUsd, fmtUptime, fmtUsd, symbolOf } from "../shared/format";
import { useTradingAccess } from "../shared/hooks";
import FiltersBar from "./FiltersBar";
import { apiQuery, periodRange, readFilters, writeFilters, type Filters } from "./filters";
import "../shared/ui/ui.css";

export interface Stats {
  trades: number; wins: number; losses: number;
  win_rate_pct: string | null; net_usd: string; gross_usd: string; fees_usd: string;
  fees_share_of_gross_pct: string | null; avg_win_usd: string | null; avg_loss_usd: string | null;
  expectancy_usd: string | null; expectancy_pct: string | null; profit_factor: string | null;
  avg_r: string | null; avg_hold_seconds: number | null; avg_entry_slippage_bps: string | null;
  exits: Record<string, number>; sample_too_small: boolean;
}
interface Report {
  overall: Stats;
  by_version: (Stats & { version_id: string; strategy_id: string; version: string })[];
  by_pair: { instrument_id: string; trades: number; net_usd: string; win_rate_pct: string | null; fees_usd: string }[];
  by_exit_reason: Record<string, number>;
}
interface Curve { points: { at: string; performance: string }[]; max_drawdown_pct: string | null; max_drawdown_at: string | null }

const n = (s: string | null | undefined) => (s === null || s === undefined ? null : Number(s));
const asError = (e: unknown) => (e instanceof ApiError ? e : new ApiError(0, "NETWORK", String(e), null));

const ROWS: { label: string; get: (s: Stats) => string }[] = [
  { label: "trades", get: (s) => String(s.trades) },
  { label: "win rate", get: (s) => fmtPct(n(s.win_rate_pct)) },
  { label: "net result", get: (s) => fmtSignedUsd(s.net_usd) },
  { label: "gross result", get: (s) => fmtSignedUsd(s.gross_usd) },
  { label: "fees", get: (s) => fmtUsd(s.fees_usd) },
  { label: "expectancy / trade", get: (s) => `${fmtSignedUsd(s.expectancy_usd)} (${fmtPct(n(s.expectancy_pct), 2, true)})` },
  { label: "profit factor", get: (s) => (s.profit_factor === null ? DASH : Number(s.profit_factor).toFixed(2)) },
  { label: "average R", get: (s) => (s.avg_r === null ? DASH : Number(s.avg_r).toFixed(2)) },
  { label: "average hold", get: (s) => fmtUptime(s.avg_hold_seconds) },
];

export default function StatsPage() {
  const access = useTradingAccess();
  const [params, setParams] = useSearchParams();
  const filters = useMemo(() => readFilters(params), [params]);
  const [report, setReport] = useState<Report | null>(null);
  const [curve, setCurve] = useState<Curve | null>(null);
  const [error, setError] = useState<ApiError | null>(null);

  const load = useCallback(async () => {
    try {
      setError(null);
      setReport(await getJson<Report>(`/api/v1/stats?${apiQuery(filters)}`));
      if (filters.bot) {
        const r = periodRange(filters);
        const q = new URLSearchParams({ bot_id: filters.bot, ...(r.since ? { since: r.since } : {}), ...(r.until ? { until: r.until } : {}) });
        setCurve(await getJson<Curve>(`/api/v1/stats/curve?${q}`));
      } else setCurve(null);
    } catch (e) {
      setError(asError(e));
    }
  }, [filters]);

  useEffect(() => {
    if (access.kind === "ready") void load();
  }, [access.kind, load]);

  if (access.kind === "unauthenticated") return <Navigate to="/login" replace />;
  if (access.kind === "loading") return <div className="hl-state-box">loading…</div>;
  if (access.kind === "forbidden") return <div className="hl-state-box" role="alert">A trading role is required.</div>;
  if (access.kind === "error") return <div className="hl-state-box" role="alert">{access.error.code}</div>;

  const s = report?.overall;
  const change = (f: Filters) => setParams(writeFilters(f));
  return (
    <div className="hl-page">
      <h1 className="hl-title">Stats</h1>
      <div className="hl-section">
        <FiltersBar value={filters} onChange={change} full={false} />
        {error && <div className="hl-banner error" role="alert">{error.code} — {error.detail}</div>}
        {!report && !error && <div className="hl-state-box">loading statistics…</div>}
        {s && s.trades === 0 && <div className="hl-state-box">No closed trade for these filters.</div>}
        {s && s.trades > 0 && (
          <div className="hl-cards" data-testid="cards">
            <Card label="net result" value={fmtSignedUsd(s.net_usd)} />
            <Card label="gross result (before fees)" value={fmtSignedUsd(s.gross_usd)} />
            <Card label="fees" value={`${fmtUsd(s.fees_usd)}${s.fees_share_of_gross_pct ? ` · ${fmtPct(n(s.fees_share_of_gross_pct), 0)} of gross` : ""}`} />
            <Card label="win rate" value={`${fmtPct(n(s.win_rate_pct))} (${s.wins}/${s.trades})`} />
            <Card label="expectancy / trade" value={fmtSignedUsd(s.expectancy_usd)} />
            <Card label="profit factor" value={s.profit_factor === null ? DASH : Number(s.profit_factor).toFixed(2)} />
            <Card label="max drawdown" value={curve ? fmtPct(n(curve.max_drawdown_pct), 2) : DASH} />
            <Card label="average hold" value={fmtUptime(s.avg_hold_seconds)} />
          </div>
        )}
      </div>

      <section className="hl-section">
        <h2>Performance (deposits excluded)</h2>
        {!filters.bot && <p className="hl-readonly-note">Select a bot to see its performance curve.</p>}
        {curve && <CurveChart curve={curve} />}
      </section>

      {report && report.by_version.length > 0 && (
        <section className="hl-section">
          <h2>By strategy version</h2>
          <div style={{ overflowX: "auto" }}>
            <table className="hl-table" data-testid="by-version">
              <thead><tr><th></th>{report.by_version.map((v) => (
                <th key={v.version_id}>{v.strategy_id} v{v.version}
                  {v.sample_too_small && <div className="hl-small-sample">sample too small (&lt; 20 trades)</div>}</th>))}</tr></thead>
              <tbody>{ROWS.map((r) => (
                <tr key={r.label}><td>{r.label}</td>{report.by_version.map((v) => <td key={v.version_id}>{r.get(v)}</td>)}</tr>
              ))}</tbody>
            </table>
          </div>
        </section>
      )}

      {report && report.by_pair.length > 0 && (
        <section className="hl-section">
          <h2>By pair</h2>
          <table className="hl-table" data-testid="by-pair">
            <thead><tr><th>pair</th><th>trades</th><th>net</th><th>win rate</th><th>fees</th></tr></thead>
            <tbody>{report.by_pair.map((p) => (
              <tr key={p.instrument_id}><td>{symbolOf(p.instrument_id)}</td><td>{p.trades}</td>
                <td className={Number(p.net_usd) < 0 ? "hl-neg" : "hl-pos"}>{fmtSignedUsd(p.net_usd)}</td>
                <td>{fmtPct(n(p.win_rate_pct))}</td><td>{fmtUsd(p.fees_usd)}</td></tr>
            ))}</tbody>
          </table>
          <h2>Exits</h2>
          <p data-testid="exits">{Object.entries(report.by_exit_reason).map(([k, v]) => `${k} ${v}`).join(" · ") || DASH}</p>
        </section>
      )}
    </div>
  );
}

function Card({ label, value }: { label: string; value: string }) {
  return <div className="hl-card"><div className="label">{label}</div><div className="value">{value}</div></div>;
}

/** Plain SVG line of P over time, zero line, and the max-drawdown point. */
export function CurveChart({ curve, width = 900, height = 220 }: { curve: Curve; width?: number; height?: number }) {
  const pts = curve.points.map((p) => ({ t: Date.parse(p.at), v: Number(p.performance) }));
  if (pts.length < 2) return <p className="hl-readonly-note" data-testid="curve-empty">Not enough equity snapshots yet.</p>;
  const pad = 24;
  const t0 = pts[0].t, t1 = pts[pts.length - 1].t || t0 + 1;
  const vs = pts.map((p) => p.v).concat(0);
  const lo = Math.min(...vs), hi = Math.max(...vs);
  const span = hi - lo || 1;
  const x = (t: number) => pad + ((t - t0) / (t1 - t0 || 1)) * (width - 2 * pad);
  const y = (v: number) => height - pad - ((v - lo) / span) * (height - 2 * pad);
  const trough = curve.max_drawdown_at ? pts.find((p) => p.t === Date.parse(curve.max_drawdown_at!)) : undefined;
  return (
    <svg className="hl-curve" viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" role="img"
      aria-label={`performance curve, max drawdown ${curve.max_drawdown_pct ? Number(curve.max_drawdown_pct).toFixed(2) : "unknown"} %`} data-testid="curve">
      <line x1={pad} x2={width - pad} y1={y(0)} y2={y(0)} className="zero" strokeDasharray="4 4" />
      <polyline className="line" fill="none" strokeWidth="2" points={pts.map((p) => `${x(p.t)},${y(p.v)}`).join(" ")} />
      {trough && <circle data-testid="trough" cx={x(trough.t)} cy={y(trough.v)} r="5" className="trough" />}
      <text x={pad} y={14} fontSize="11">{fmtSignedUsd(hi.toFixed(2))}</text>
      <text x={pad} y={height - 6} fontSize="11">{fmtSignedUsd(lo.toFixed(2))}</text>
    </svg>
  );
}
