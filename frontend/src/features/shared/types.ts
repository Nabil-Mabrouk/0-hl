// Types of the 0-hl API M1.4 (spec 10-backend/04). Money/quantities are decimal strings.

export type Dec = string;

export interface DomainEvent {
  position: number;
  event_id: string;
  event_type: string;
  aggregate_type: string;
  aggregate_id: string;
  aggregate_sequence: number;
  correlation_id: string;
  causation_id: string | null;
  bot_id: string | null;
  occurred_at: string;
  source: string;
  payload: Record<string, unknown>;
}

export interface Bot {
  id: string;
  name: string;
  environment: "DEMO" | "PAPER" | "LIVE";
  status: string;
  row_version: number;
  strategy_version_id: string;
  risk_policy_version_id: string;
}

export interface Session {
  id: string;
  status: string;
  started_at: string;
  ended_at: string | null;
  uptime_seconds: number;
}

export interface PositionView {
  id: string;
  instrument_id: string;
  symbol: string;
  status: string;
  quantity: Dec;
  cost_basis: Dec;
  avg_entry_price: Dec | null;
  initial_stop_price: Dec | null;
  take_profit_price: Dec | null;
  r0_usd: Dec | null;
  opened_at: string | null;
  closed_at: string | null;
  exit_reason: string | null;
  fees_paid: Dec;
  realized_pnl_net: Dec | null;
  r_multiple: Dec | null;
  mark_price: Dec | null;
  mark_value: Dec | null;
  marked_at: string | null;
  unrealized_pnl: Dec | null;
  entry_features: Record<string, Dec | null> | null;
}

export interface AgentState {
  status: "ACTIVE" | "IDLE" | "DOWN";
  last_event_type: string | null;
  last_event_at: string | null;
  position?: number | null;
}

export interface ScanStats {
  window_end: string;
  evaluated: number;
  unknown: number;
  candidates: number;
  rejected: number;
  fails: Record<string, number>;
  unknown_reasons: Record<string, number>;
  occurred_at: string;
}

export interface Snapshot {
  generated_at: string;
  bot: Bot;
  config: {
    strategy_id: string | null;
    strategy_version: string | null;
    max_trade_risk_pct: Dec | null;
    max_slippage_pct: Dec | null;
  };
  owner_capital: Dec;
  session: Session | null;
  equity: {
    cash: Dec;
    cash_reserved: Dec;
    positions_value: Dec | null;
    equity: Dec | null;
    realized_pnl_net: Dec;
    unrealized_pnl: Dec | null;
    quality: "OK" | "STALE";
    as_of: string;
  };
  stats: { trades: number; wins: number; losses: number; breakeven: number };
  open_positions: PositionView[];
  recent_trades: PositionView[];
  agents: Record<string, AgentState>;
  scan: ScanStats | null;
  feed: {
    connection: string | null;
    worker_alive: boolean;
    pairs_live: number;
    pairs_subscribed: number;
    updated_at: string | null;
  };
  recent_events: DomainEvent[];
  position: number;
}

export interface CommandView {
  command_id: string;
  bot_id: string;
  command: string;
  status: "PENDING" | "COMPLETED" | "REJECTED";
  rejection_code: string | null;
}

export type ConnectionState = "CONNECTING" | "LIVE" | "RECONNECTING" | "OFFLINE";
