// Test fixtures shaped exactly like the API M1.4 responses (decimal strings, ISO dates).
import type { DomainEvent, PositionView, Snapshot } from "./types";

let pos = 100;
export function ev(event_type: string, payload: Record<string, unknown> = {}, extra: Partial<DomainEvent> = {}): DomainEvent {
  pos += 1;
  return {
    position: pos, event_id: `e${pos}`, event_type, aggregate_type: "candidate",
    aggregate_id: "a", aggregate_sequence: 1, correlation_id: "corr-1", causation_id: null,
    bot_id: "bot-1", occurred_at: "2026-11-18T06:34:00Z", source: "worker:agents", payload, ...extra,
  };
}

export const closedTrade: PositionView = {
  id: "p1", instrument_id: "kraken_spot:XRP/USD", symbol: "XRP/USD", status: "CLOSED",
  quantity: "0", cost_basis: "0", avg_entry_price: "0.9891", initial_stop_price: "0.9594",
  take_profit_price: "1.0485", r0_usd: "45.10", opened_at: "2026-11-18T06:20:00Z",
  closed_at: "2026-11-18T06:31:00Z", exit_reason: "TAKE_PROFIT", fees_paid: "10.52",
  realized_pnl_net: "342.52", r_multiple: "2.11", mark_price: null, mark_value: null, marked_at: null,
  unrealized_pnl: null,
  entry_features: { volume_spike: "4.1", buy_notional_share: "0.71", window_notional_usd: "182000" },
};

export function snapshot(overrides: Partial<Snapshot> = {}): Snapshot {
  return {
    generated_at: "2026-11-18T06:38:33Z",
    bot: { id: "bot-1", name: "paper-kraken-01", environment: "PAPER", status: "RUNNING", row_version: 3,
      strategy_version_id: "sv", risk_policy_version_id: "pv" },
    config: { strategy_id: "volume-spike", strategy_version: "0.2.0", max_trade_risk_pct: "0.5", max_slippage_pct: "0.5" },
    owner_capital: "10000.000000000000000000",
    session: { id: "s1", status: "RUNNING", started_at: "2026-11-18T00:00:00Z", ended_at: null, uptime_seconds: 23913 },
    equity: { cash: "10745.21", cash_reserved: "0", positions_value: "0", equity: "10745.214999",
      realized_pnl_net: "745.21", unrealized_pnl: "0", quality: "OK", as_of: "2026-11-18T06:38:33Z" },
    stats: { trades: 61, wins: 40, losses: 21, breakeven: 0 },
    open_positions: [],
    recent_trades: [closedTrade],
    agents: {
      SCANNER: { status: "ACTIVE", last_event_type: "SCAN_CYCLE_COMPLETED", last_event_at: "2026-11-18T06:38:00Z" },
      RUG_CHECK: { status: "IDLE", last_event_type: null, last_event_at: null },
      SIZER: { status: "IDLE", last_event_type: null, last_event_at: null },
      EXECUTOR: { status: "ACTIVE", last_event_type: "POSITION_CLOSED", last_event_at: "2026-11-18T06:31:00Z" },
      LOGGER: { status: "ACTIVE", last_event_type: "SCAN_CYCLE_COMPLETED", last_event_at: "2026-11-18T06:38:00Z", position: 151 },
      EQUITY: { status: "ACTIVE", last_event_type: "EQUITY_UPDATED", last_event_at: "2026-11-18T06:38:00Z" },
    },
    scan: { window_end: "2026-11-18T06:38:00Z", evaluated: 35, unknown: 2, candidates: 1, rejected: 0,
      fails: { volume_spike: 30, window_notional_usd: 21 }, unknown_reasons: {}, occurred_at: "2026-11-18T06:38:30Z" },
    feed: { connection: "LIVE", worker_alive: true, pairs_live: 35, pairs_subscribed: 35, updated_at: "2026-11-18T06:38:30Z" },
    recent_events: [],
    position: 151,
    ...overrides,
  };
}
