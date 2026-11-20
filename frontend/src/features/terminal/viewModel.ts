// Pure mapping snapshot + events → what the canonical terminal displays
// (spec 03-ecrans/M1-terminal-et-bots « UI-01 Live Terminal »). No value is invented:
// missing data stays null and renders as "—".
import { DASH, fmtNum, fmtPct, fmtSignedUsd, fmtTime, fmtUsd, ratio, symbolOf } from "../shared/format";
import type { DomainEvent, EntryBlock, PositionView, Snapshot } from "../shared/types";

export interface LogLine {
  position: number;
  time: string;
  kind: "ENTER" | "EXIT" | "FILTERED" | "REFUSED" | "SCAN" | "INFO";
  symbol: string;
  detail: string;
  value: string;
  tone: "good" | "bad" | "muted";
  correlationId: string;
}

export const AGENT_ORDER = ["SCANNER", "RUG_CHECK", "SIZER", "EXECUTOR", "LOGGER", "EQUITY"] as const;
export type AgentKey = (typeof AGENT_ORDER)[number];

export const AGENT_LABEL: Record<AgentKey, string> = {
  SCANNER: "SCANNER", RUG_CHECK: "RUG CHECK", SIZER: "SIZER", EXECUTOR: "EXECUTOR",
  LOGGER: "LOGGER", EQUITY: "EQUITY",
};

const AGENT_DESC: Record<AgentKey, string> = {
  SCANNER: "top USD pairs + volume spikes",
  RUG_CHECK: "market-quality gate",
  SIZER: "sizes from account equity",
  EXECUTOR: "opens + closes, tracks slip",
  LOGGER: "writes every decision",
  EQUITY: "balance + curve",
};

const AGENT_MOTTO: Record<AgentKey, string> = {
  SCANNER: "flags only, never trades",
  RUG_CHECK: "nothing executes before a pass",
  SIZER: "never trades a fixed ticket",
  EXECUTOR: "slippage cap",
  LOGGER: "no trade without a reason",
  EQUITY: "updates on every close",
};

function symbolsByCorrelation(events: DomainEvent[]): Map<string, string> {
  const m = new Map<string, string>();
  for (const e of events) {
    const iid = (e.payload as { instrument_id?: string }).instrument_id;
    if (iid) m.set(e.correlation_id, symbolOf(iid));
  }
  return m;
}

/** Amount committed at entry (review 2026-11-19): never the residual cost, which is 0 once sold. */
export function entryNotional(p: PositionView | undefined): string | null {
  if (!p) return null;
  return p.entry_notional ?? (p.status === "CLOSED" ? null : p.cost_basis);
}

function sizePct(p: PositionView | undefined, capital: string | null): string {
  const n = entryNotional(p);
  const r = ratio(n, capital);
  return r === null ? DASH : fmtPct(r * 100, 0);
}

export function logLines(events: DomainEvent[], snap: Snapshot | null): LogLine[] {
  const syms = symbolsByCorrelation(events);
  // events recorded before 2026-11-20 may lack instrument_id: fall back on the snapshot position
  const positionSyms = new Map<string, string>();
  for (const pos of [...(snap?.open_positions ?? []), ...(snap?.recent_trades ?? [])]) {
    positionSyms.set(pos.id, symbolOf(pos.instrument_id));
  }
  const equity = snap?.equity.equity ?? snap?.owner_capital ?? null;
  const out: LogLine[] = [];
  for (const e of events) {
    const p = e.payload as Record<string, unknown>;
    const base = { position: e.position, time: fmtTime(e.occurred_at), correlationId: e.correlation_id };
    const sym = p.instrument_id
      ? symbolOf(p.instrument_id as string)
      : positionSyms.get(e.aggregate_id) ?? syms.get(e.correlation_id) ?? DASH;
    switch (e.event_type) {
      case "POSITION_OPENED":
        out.push({ ...base, kind: "ENTER", symbol: sym,
          detail: `qty ${fmtNum(p.quantity as string, 6)} @ ${fmtNum(p.avg_entry_price as string, 6)}`,
          value: `size ${fmtPct((ratio(String(Number(p.quantity) * Number(p.avg_entry_price)), equity) ?? 0) * 100, 0)}`,
          tone: "good" });
        break;
      case "POSITION_CLOSED": {
        const pnl = p.realized_pnl_net as string | null;
        out.push({ ...base, kind: "EXIT", symbol: sym,
          detail: `reason: ${String(p.reason ?? DASH).toLowerCase().replace("_", " ")}`,
          value: fmtSignedUsd(pnl), tone: pnl && pnl.startsWith("-") ? "bad" : "good" });
        break;
      }
      case "RISK_REJECTED": {
        // RUG CHECK rejections are FILTERED; SIZER refusals (limits) are REFUSED (review 2026-11-19)
        const sizer = p.stage === "SIZER";
        const failed = (p.failed as string[] | undefined) ?? (p.reasons as string[] | undefined) ?? [];
        out.push({ ...base, kind: sizer ? "REFUSED" : "FILTERED", symbol: sym,
          detail: `${sizer ? "sizer" : "rug check"}: ${failed.join(", ").toLowerCase() || DASH}`,
          value: "not traded", tone: "muted" });
        break;
      }
      case "SCAN_CYCLE_COMPLETED":
        out.push({ ...base, kind: "SCAN", symbol: "", tone: "muted",
          detail: `checked ${p.evaluated} · ${p.candidates} cand.`, value: "" });
        break;
      case "DATA_STALE":
      case "PROVIDER_DEGRADED":
        out.push({ ...base, kind: "INFO", symbol: sym, tone: "bad",
          detail: `${e.event_type.toLowerCase()} ${String(p.reason ?? "")}`.trim(), value: "" });
        break;
      default:
        break;
    }
  }
  return out;
}

export interface AgentCard {
  key: AgentKey;
  label: string;
  desc: string;
  motto: string;
  detail: string;
  status: "ACTIVE" | "IDLE" | "DOWN";
}

export function agentCards(snap: Snapshot, events: DomainEvent[]): AgentCard[] {
  const lastClosed = snap.recent_trades[0];
  const lastOpen = snap.open_positions[snap.open_positions.length - 1];
  const lines = logLines(events, snap);
  const lastFilter = [...lines].reverse().find((l) => l.kind === "FILTERED");
  const lastRefusal = [...lines].reverse().find((l) => l.kind === "REFUSED");
  const details: Record<AgentKey, string> = {
    SCANNER: snap.scan ? `last scan: ${snap.scan.evaluated} pairs · ${snap.scan.candidates} cand.` : "waiting for first scan",
    RUG_CHECK: lastFilter ? `filtered ${lastFilter.symbol}` : "no rejection yet",
    SIZER: lastRefusal
      ? `refused ${lastRefusal.symbol}: ${lastRefusal.detail.replace("sizer: ", "")}`
      : `risk ${snap.config.max_trade_risk_pct ?? DASH}% of ${fmtUsd(snap.equity.equity)}`,
    EXECUTOR: lastOpen ? `LONG ${symbolOf(lastOpen.instrument_id)}` : lastClosed ? `last exit ${symbolOf(lastClosed.instrument_id)}` : "no fill yet",
    LOGGER: `line #${snap.agents.LOGGER?.position ?? snap.position}`,
    EQUITY: `${fmtUsd(snap.equity.equity)} · ${snap.stats.trades} closes`,
  };
  return AGENT_ORDER.map((key) => ({
    key, label: AGENT_LABEL[key], desc: AGENT_DESC[key],
    motto: key === "EXECUTOR" ? `slippage cap ${snap.config.max_slippage_pct ?? DASH}%` : AGENT_MOTTO[key],
    detail: details[key], status: snap.agents[key]?.status ?? "IDLE",
  }));
}

export interface TradeView {
  symbol: string;
  open: boolean;
  change: string;
  changeTone: "good" | "bad" | "muted";
  entry: string;
  exit: string;
  stop: string;
  target: string;
  size: string;
  reason: string;
}

export function tradeView(snap: Snapshot): TradeView | null {
  const p = snap.open_positions[snap.open_positions.length - 1] ?? snap.recent_trades[0];
  if (!p) return null;
  const open = p.status !== "CLOSED";
  const markRatio = ratio(p.mark_price, p.avg_entry_price);
  const pnlRatio = ratio(p.realized_pnl_net, entryNotional(p));
  const ch = open ? (markRatio === null ? null : (markRatio - 1) * 100) : (pnlRatio === null ? null : pnlRatio * 100);
  const f = p.entry_features ?? {};
  const reasonParts = [
    f.volume_spike ? `spike ${fmtNum(f.volume_spike, 1)}×` : null,
    f.buy_notional_share ? `buy ${fmtPct(Number(f.buy_notional_share) * 100, 0)}` : null,
    f.window_notional_usd ? `${fmtUsd(f.window_notional_usd, 0)} / 5m` : null,
  ].filter(Boolean);
  return {
    symbol: symbolOf(p.instrument_id), open,
    change: ch === null ? DASH : `${fmtPct(ch, 1, true)} ${open ? "live" : "closed"}`,
    changeTone: ch === null ? "muted" : ch >= 0 ? "good" : "bad",
    entry: fmtNum(p.avg_entry_price),
    exit: open ? "open" : (p.exit_reason ?? DASH).toLowerCase().replace("_", " "),
    stop: fmtNum(p.initial_stop_price),
    target: fmtNum(p.take_profit_price),
    size: `${sizePct(p, snap.owner_capital)} of capital (${fmtUsd(entryNotional(p))})`,
    reason: reasonParts.length ? reasonParts.join(" · ") : DASH,
  };
}

export function candidateCards(events: DomainEvent[]): { symbol: string; spike: string; position: number }[] {
  return events
    .filter((e) => e.event_type === "CANDIDATE_DETECTED")
    .slice(-2)
    .reverse()
    .map((e) => {
      const p = e.payload as { instrument_id?: string; features?: Record<string, string | null> };
      return { symbol: symbolOf(p.instrument_id), position: e.position,
        spike: p.features?.volume_spike ? `spike ${fmtNum(p.features.volume_spike, 1)}×` : DASH };
    });
}

export interface ResultBar {
  left: number; // % of the track
  width: number;
  tone: "good" | "bad" | "muted";
  pct: number | null;
}

/** RESULT bar centred on the starting capital; a half-bar is ±10 % (review 2026-11-19). */
export function resultBar(snap: Snapshot): ResultBar {
  const r = ratio(snap.equity.equity, snap.owner_capital);
  if (r === null) return { left: 50, width: 0, tone: "muted", pct: null };
  const pct = (r - 1) * 100;
  const half = Math.round(Math.min(Math.abs(pct) / 10, 1) * 5000) / 100; // 2 decimals for CSS
  return pct >= 0
    ? { left: 50, width: half, tone: "good", pct }
    : { left: Math.round((50 - half) * 100) / 100, width: half, tone: "bad", pct };
}

export function noSignal(snap: Snapshot): number | null {
  return snap.scan ? snap.scan.evaluated - snap.scan.candidates - snap.scan.unknown : null;
}

export function blockText(b: EntryBlock): string {
  const pct = (s: string) => fmtPct(Number(s) * 100, 1);
  switch (b.code) {
    case "MAX_DAILY_LOSS":
      return `daily loss ${pct(b.value)} ≥ ${pct(b.limit)} until 00:00 UTC`;
    case "MAX_DRAWDOWN":
      return `drawdown ${pct(b.value)} ≥ ${pct(b.limit)}`;
    case "KILL_SWITCH":
      return "kill switch on";
    case "MAX_OPEN_POSITIONS":
      return `max open positions ${b.value}/${b.limit}`;
    case "BOT_NOT_RUNNING":
      return `bot ${b.value.toLowerCase()}`;
    default:
      return String(b.code).toLowerCase();
  }
}

export function activityChip(snap: Snapshot, lines: LogLine[]): string {
  const exec = [...lines].reverse().find((l) => l.kind === "ENTER" || l.kind === "EXIT");
  if (exec) return `EXECUTOR · ${exec.kind === "ENTER" ? "ENTERED" : "EXITED"} ${exec.symbol}`;
  if (snap.scan) return `SCANNER · checked ${snap.scan.evaluated}`;
  return "SCANNER · warming up";
}
