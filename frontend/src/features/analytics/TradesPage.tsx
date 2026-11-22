// UI-03 Trades — M2.5 (spec 03-ecrans/M2-trades-et-statistiques « Écrans »). Realised and unrealised
// results are never mixed; unknown values render as "—".
import { useCallback, useEffect, useMemo, useState } from "react";
import { Navigate, useSearchParams } from "react-router-dom";
import { apiFetch } from "../../api";
import { ApiError, getJson } from "../shared/client";
import { DASH, fmtNum, fmtPct, fmtSignedUsd, fmtUsd, fmtUptime, symbolOf } from "../shared/format";
import { useTradingAccess } from "../shared/hooks";
import type { DomainEvent, PositionView } from "../shared/types";
import FiltersBar from "./FiltersBar";
import { apiQuery, readFilters, writeFilters, type Filters } from "./filters";
import "../shared/ui/ui.css";

export interface TradeView {
  id: string;
  bot_id?: string;
  instrument_id: string;
  quantity: string;
  avg_entry_price: string | null;
  avg_exit_price: string | null;
  entry_notional: string | null;
  fees_paid: string;
  realized_pnl_net: string | null;
  net_pct: string | null;
  r_multiple: string | null;
  exit_reason: string | null;
  opened_at: string | null;
  closed_at: string | null;
  hold_seconds: number | null;
  entry_slippage_bps: string | null;
  correlation_id: string | null;
  strategy: { version_id: string; strategy_id: string; version: string } | null;
}

interface StatsOverall { trades: number; net_usd: string; fees_usd: string }

type SortKey = "closed_at" | "instrument_id" | "realized_pnl_net" | "net_pct" | "hold_seconds" | "fees_paid";
const PAGE = 50;
const asError = (e: unknown) => (e instanceof ApiError ? e : new ApiError(0, "NETWORK", String(e), null));
const num = (s: string | null | undefined) => (s === null || s === undefined ? null : Number(s));

export function sortTrades(items: TradeView[], key: SortKey, asc: boolean): TradeView[] {
  const val = (t: TradeView) => {
    const v = t[key];
    return typeof v === "number" ? v : key === "instrument_id" || key === "closed_at" ? (v ?? "") : (num(v as string | null) ?? -Infinity);
  };
  return [...items].sort((a, b) => {
    const x = val(a), y = val(b);
    const c = x < y ? -1 : x > y ? 1 : 0;
    return asc ? c : -c;
  });
}

export default function TradesPage() {
  const access = useTradingAccess();
  const [params, setParams] = useSearchParams();
  const filters = useMemo(() => readFilters(params), [params]);
  const offset = Number(params.get("offset") ?? 0) || 0;
  const [data, setData] = useState<{ items: TradeView[]; total: number } | null>(null);
  const [overall, setOverall] = useState<StatsOverall | null>(null);
  const [open, setOpen] = useState<PositionView[] | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [sort, setSort] = useState<{ key: SortKey; asc: boolean }>({ key: "closed_at", asc: false });
  const [trace, setTrace] = useState<{ trade: TradeView; events: DomainEvent[] | null } | null>(null);

  const load = useCallback(async () => {
    try {
      setError(null);
      const [t, s] = await Promise.all([
        getJson<{ items: TradeView[]; total: number }>(`/api/v1/trades?${apiQuery(filters, { limit: String(PAGE), offset: String(offset) })}`),
        getJson<{ overall: StatsOverall }>(`/api/v1/stats?${apiQuery(filters)}`),
      ]);
      setData(t);
      setOverall(s.overall);
      setOpen(filters.bot ? (await getJson<{ items: PositionView[] }>(`/api/v1/positions?bot_id=${filters.bot}`)).items : null);
    } catch (e) {
      setError(asError(e));
    }
  }, [filters, offset]);

  useEffect(() => {
    if (access.kind === "ready") void load();
  }, [access.kind, load]);

  if (access.kind === "unauthenticated") return <Navigate to="/login" replace />;
  if (access.kind === "loading") return <div className="hl-state-box">loading…</div>;
  if (access.kind === "forbidden") return <div className="hl-state-box" role="alert">A trading role is required.</div>;
  if (access.kind === "error") return <div className="hl-state-box" role="alert">{access.error.code}</div>;

  const change = (f: Filters) => setParams(writeFilters(f));
  const page = (o: number) => { const p = writeFilters(filters); if (o) p.set("offset", String(o)); setParams(p); };
  const items = data ? sortTrades(data.items, sort.key, sort.asc) : [];
  const unrealized = open === null ? null
    : open.some((p) => p.unrealized_pnl === null) ? null
      : open.reduce((acc, p) => acc + Number(p.unrealized_pnl), 0);
  const head = (key: SortKey, label: string) => (
    <th><button type="button" className="hl-sort" onClick={() => setSort({ key, asc: sort.key === key ? !sort.asc : false })}>
      {label}{sort.key === key ? (sort.asc ? " ▲" : " ▼") : ""}</button></th>
  );

  async function exportCsv() {
    try {
      const res = await apiFetch(`/api/v1/trades.csv?${apiQuery(filters)}`);
      if (!res.ok) throw new ApiError(res.status, `HTTP_${res.status}`, res.statusText, null);
      const url = URL.createObjectURL(await res.blob());
      const a = document.createElement("a");
      a.href = url;
      a.download = "0-hl-trades.csv";
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      setError(asError(e));
    }
  }

  async function openTrace(t: TradeView) {
    setTrace({ trade: t, events: null });
    if (!t.correlation_id) return;
    try {
      const r = await getJson<{ items: DomainEvent[] }>(`/api/v1/events/trace/${t.correlation_id}`);
      setTrace({ trade: t, events: r.items });
    } catch {
      setTrace({ trade: t, events: [] });
    }
  }

  return (
    <div className="hl-page">
      <h1 className="hl-title">Trades</h1>
      <div className="hl-section">
        <FiltersBar value={filters} onChange={change} pairs={[...new Set(items.map((t) => t.instrument_id))]} />
        {error && <div className="hl-banner error" role="alert">{error.code} — {error.detail}</div>}
        {overall && (
          <div className="hl-banner provenance" data-testid="summary">
            realised net {fmtSignedUsd(overall.net_usd)} · fees {fmtUsd(overall.fees_usd)} · {overall.trades} trades
            {open !== null && <> · open positions {open.length}, unrealised {unrealized === null ? DASH : fmtSignedUsd(unrealized.toFixed(2))}</>}
          </div>
        )}
      </div>

      {open && open.length > 0 && (
        <section className="hl-section">
          <h2>Open positions</h2>
          <table className="hl-table" data-testid="open-positions">
            <thead><tr><th>pair</th><th>quantity</th><th>entry</th><th>mark</th><th>unrealised</th><th>age</th></tr></thead>
            <tbody>{open.map((p) => (
              <tr key={p.id}><td>{symbolOf(p.instrument_id)}</td><td>{fmtNum(p.quantity)}</td><td>{fmtNum(p.avg_entry_price)}</td>
                <td>{fmtNum(p.mark_price)}</td><td>{fmtSignedUsd(p.unrealized_pnl)}</td>
                <td>{p.opened_at ? fmtUptime(Math.floor((Date.now() - Date.parse(p.opened_at)) / 1000)) : DASH}</td></tr>
            ))}</tbody>
          </table>
        </section>
      )}

      <section className="hl-section">
        <div className="hl-actions" style={{ justifyContent: "space-between", marginBottom: 6 }}>
          <span>{data ? `${data.total} trades` : ""}</span>
          <button type="button" className="hl-btn" onClick={() => void exportCsv()} disabled={!data || data.total === 0}>Export CSV</button>
        </div>
        {!data && !error && <div className="hl-state-box">loading trades…</div>}
        {data && data.total === 0 && <div className="hl-state-box">No closed trade for these filters.</div>}
        {data && data.total > 0 && (
          <div style={{ overflowX: "auto" }}>
          <table className="hl-table" data-testid="trades">
            <thead>
              <tr>{head("closed_at", "closed")}{head("instrument_id", "pair")}<th>version</th><th>entry → exit</th>
                <th>amount</th>{head("fees_paid", "fees")}{head("realized_pnl_net", "net")}{head("net_pct", "net %")}
                <th>R</th><th>exit</th>{head("hold_seconds", "held")}<th>slip. bps</th></tr>
            </thead>
            <tbody>
              {items.map((t) => (
                <tr key={t.id} data-testid={`trade-${t.id}`} className="hl-clickable" onClick={() => void openTrace(t)}>
                  <td>{t.closed_at ? t.closed_at.slice(0, 16).replace("T", " ") : DASH}</td>
                  <td>{symbolOf(t.instrument_id)}</td>
                  <td>{t.strategy ? `v${t.strategy.version}` : DASH}</td>
                  <td>{fmtNum(t.avg_entry_price)} → {fmtNum(t.avg_exit_price)}</td>
                  <td>{fmtUsd(t.entry_notional)}</td>
                  <td>{fmtUsd(t.fees_paid)}</td>
                  <td className={num(t.realized_pnl_net) !== null && num(t.realized_pnl_net)! < 0 ? "hl-neg" : "hl-pos"}>{fmtSignedUsd(t.realized_pnl_net)}</td>
                  <td>{fmtPct(num(t.net_pct), 2, true)}</td>
                  <td>{t.r_multiple === null ? DASH : Number(t.r_multiple).toFixed(2)}</td>
                  <td>{t.exit_reason ?? DASH}</td>
                  <td>{fmtUptime(t.hold_seconds)}</td>
                  <td>{t.entry_slippage_bps === null ? DASH : Number(t.entry_slippage_bps).toFixed(1)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          </div>
        )}
        {data && data.total > PAGE && (
          <div className="hl-actions" style={{ marginTop: 8 }}>
            <button type="button" className="hl-btn" disabled={offset === 0} onClick={() => page(Math.max(0, offset - PAGE))}>previous</button>
            <span>{offset + 1}–{Math.min(offset + PAGE, data.total)} of {data.total}</span>
            <button type="button" className="hl-btn" disabled={offset + PAGE >= data.total} onClick={() => page(offset + PAGE)}>next</button>
          </div>
        )}
      </section>

      {trace && (
        <div className="hl-dialog-backdrop" onClick={() => setTrace(null)}>
          <div className="hl-dialog" role="dialog" aria-label="Trade trace" style={{ maxWidth: 640 }} onClick={(e) => e.stopPropagation()}>
            <h3>{symbolOf(trace.trade.instrument_id)} — {trace.trade.exit_reason ?? DASH} {fmtSignedUsd(trace.trade.realized_pnl_net)}</h3>
            {trace.events === null && <p>loading trace…</p>}
            {trace.events && trace.events.length === 0 && <p>No trace available for this trade.</p>}
            {trace.events && trace.events.length > 0 && (
              <ol className="hl-trace" data-testid="trace">
                {trace.events.map((e) => (
                  <li key={e.event_id}><strong>{e.event_type}</strong> <code>{e.occurred_at.slice(11, 19)}</code>
                    <div className="hl-trace-payload">{summarise(e)}</div></li>
                ))}
              </ol>
            )}
            <div className="hl-actions">
              <a className="hl-btn" href={`/ai?service=explain-trade&position_id=${encodeURIComponent(trace.trade.id)}`}>Explain (AI)</a>
              <button type="button" className="hl-btn" onClick={() => setTrace(null)}>close</button></div>
          </div>
        </div>
      )}
    </div>
  );
}

/** One readable line per trace event (candidate features, gate, sizing, fills, exit). */
export function summarise(e: DomainEvent): string {
  const p = e.payload as Record<string, unknown>;
  const pick = (keys: string[]) => keys.filter((k) => p[k] !== undefined && p[k] !== null)
    .map((k) => `${k}=${typeof p[k] === "object" ? JSON.stringify(p[k]) : String(p[k])}`).join("  ");
  switch (e.event_type) {
    case "CANDIDATE_DETECTED": return pick(["features"]);
    case "RISK_PASSED": case "RISK_REJECTED": return pick(["stage", "failed", "reasons"]);
    case "POSITION_SIZED": return pick(["quantity", "notional", "binding_limit", "risk_budget"]);
    case "ORDER_FILLED": case "ORDER_PARTIAL_FILL": return pick(["filled_qty", "avg_price", "fee"]);
    case "POSITION_OPENED": return pick(["quantity", "avg_entry_price", "initial_stop_price", "take_profit_price"]);
    case "POSITION_CLOSED": return pick(["reason", "realized_pnl_net", "fees_paid", "r_multiple"]);
    default: return pick(Object.keys(p).slice(0, 4));
  }
}
