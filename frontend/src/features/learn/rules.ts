// The rules of a strategy in plain English, generated from its published configuration
// (spec 03-ecrans/UI-17 « Règles en phrases »). Never hand-copied: thresholds come from the API.

export interface Condition { feature: string; op: "gte" | "gt" | "lte" | "lt"; threshold: string; unit?: string }
export interface PublicConfig {
  universe?: { top_n_by_volume?: number; quote_currencies?: string[]; exclude_stablecoins?: boolean; min_volume_24h_usd?: string };
  entry?: { operator?: "AND" | "OR"; conditions: Condition[] };
  exit?: { initial_stop_pct?: string; take_profit_r?: string | null; max_hold_seconds?: number | null;
           exit_on_status_change?: boolean; conditions?: { operator?: "AND" | "OR"; conditions: Condition[] } | null };
  cooldown_seconds?: number | null;
  risk_per_trade_pct?: string | null;
  max_open_positions?: number | null;
}

/** English label of every feature of the catalogue (a backend test checks none is missing). */
export const FEATURES: Record<string, { subject: string; unit: string; suffix?: string; above?: string; below?: string }> = {
  volume_spike: { subject: "trading volume over the last 5 minutes", unit: "ratio", suffix: "its usual level" },
  buy_notional_share: { subject: "the share of buying in the volume of the last 5 minutes", unit: "fraction" },
  price_change_5m: { subject: "the price change over the last 5 minutes", unit: "log_return",
    above: "the price went up over the last 5 minutes", below: "the price went down over the last 5 minutes" },
  spread_bps: { subject: "the gap between the best buy and sell prices (spread)", unit: "bps" },
  window_notional_usd: { subject: "the amount traded over the last 5 minutes", unit: "USD" },
  window_trade_count: { subject: "the number of trades over the last 5 minutes", unit: "count" },
  breakout_1h: { subject: "the price", unit: "log_return", suffix: "above the highest price of the previous hour",
    above: "the price closes above the highest price of the previous hour" },
  range_1h: { subject: "the range of the previous hour (from its low to its high)", unit: "log_return" },
  trend_4h: { subject: "the price change over 4 hours", unit: "log_return",
    above: "the price is higher than 4 hours ago", below: "the price is lower than 4 hours ago" },
  drop_from_high_3h: { subject: "the price", unit: "log_return", suffix: "from its highest price of the last 3 hours" },
  gap_to_mean_4h: { subject: "the price", unit: "log_return", suffix: "from its average of the last 4 hours",
    above: "the price is back above its 4-hour average", below: "the price is below its 4-hour average" },
  trend_24h: { subject: "the price change over 24 hours", unit: "log_return",
    above: "the price is higher than 24 hours ago", below: "the price is lower than 24 hours ago" },
  btc_change_3h: { subject: "BTC's price change over 3 hours", unit: "log_return",
    above: "BTC went up over the last 3 hours", below: "BTC went down over the last 3 hours" },
  btc_trend_24h: { subject: "BTC's price change over 24 hours", unit: "log_return",
    above: "BTC is higher than 24 hours ago", below: "BTC is lower than 24 hours ago" },
  rs_24h: { subject: "the pair's 24-hour change minus BTC's", unit: "log_return",
    above: "the pair did better than BTC over 24 hours", below: "the pair did worse than BTC over 24 hours" },
  rs_rank_24h: { subject: "the pair's rank by 24-hour strength against BTC (1 = strongest)", unit: "rank" },
  breadth_24h: { subject: "the share of pairs that are up over 24 hours", unit: "fraction" },
};

const OP: Record<Condition["op"], string> = { gte: "is at least", gt: "is above", lte: "is at most", lt: "is below" };

const trim = (s: string) => (s.includes(".") ? s.replace(/0+$/, "").replace(/\.$/, "") : s);

/** A threshold for humans: log returns and fractions as %, ratios as ×, money as $. */
export function value(threshold: string, unit: string): string {
  const n = Number(threshold);
  if (!Number.isFinite(n)) return threshold;
  switch (unit) {
    case "log_return": case "fraction": return `${trim((n * 100).toFixed(2))} %`;
    case "ratio": return `${trim(n.toFixed(2))}×`;
    case "bps": return `${trim(String(n))} basis points`;
    case "USD": return `$${n.toLocaleString("en-US")}`;
    default: return trim(String(n));
  }
}

export function sentence(c: Condition): string {
  const f = FEATURES[c.feature];
  if (!f) return `${c.feature} ${c.op} ${c.threshold}`;                  // never fail on a new feature
  if (Number(c.threshold) === 0 && (c.op === "gt" || c.op === "gte") && f.above) return f.above;
  if (Number(c.threshold) === 0 && (c.op === "lt" || c.op === "lte") && f.below) return f.below;
  return `${f.subject} ${OP[c.op]} ${value(c.threshold, c.unit ?? f.unit)}${f.suffix ? ` ${f.suffix}` : ""}`;
}

const hours = (s: number) => (s % 3600 === 0 ? `${s / 3600} hour${s === 3600 ? "" : "s"}` : `${Math.round(s / 60)} minutes`);

export interface Rules { entry: string[]; entryAll: boolean; exit: string[]; other: string[] }

export function rules(cfg: PublicConfig): Rules {
  const entry = (cfg.entry?.conditions ?? []).map(sentence);
  const exit: string[] = [];
  const stop = cfg.exit?.initial_stop_pct;
  if (stop) exit.push(`Stop-loss: it sells if the price falls ${trim(stop)} % below the entry price.`);
  if (stop && cfg.exit?.take_profit_r)
    exit.push(`Target: it sells at ${trim(cfg.exit.take_profit_r)}R, i.e. ${trim((Number(stop) * Number(cfg.exit.take_profit_r)).toFixed(2))} % above the entry price.`);
  const conds = cfg.exit?.conditions?.conditions ?? [];
  if (conds.length) exit.push(`It also sells as soon as ${conds.map(sentence).join(cfg.exit?.conditions?.operator === "AND" ? " and " : " or ")}.`);
  if (cfg.exit?.max_hold_seconds) exit.push(`Time limit: it sells after ${hours(cfg.exit.max_hold_seconds)} at most.`);
  if (cfg.exit?.exit_on_status_change) exit.push("It sells if Kraken stops normal trading on the pair.");
  const other: string[] = [];
  const u = cfg.universe;
  if (u) other.push(`Pairs watched: the ${u.top_n_by_volume ?? "most"} most traded ${(u.quote_currencies ?? ["USD"]).join("/")} pairs on Kraken`
    + `${u.exclude_stablecoins ? ", stablecoins excluded" : ""}${u.min_volume_24h_usd ? `, with at least ${value(u.min_volume_24h_usd, "USD")} traded over 24 hours` : ""}.`);
  if (cfg.cooldown_seconds) other.push(`After an exit, it waits ${hours(cfg.cooldown_seconds)} before trading the same pair again.`);
  if (cfg.risk_per_trade_pct) other.push(`Size: each trade risks at most ${trim(cfg.risk_per_trade_pct)} % of the bot's capital if the stop-loss is hit`
    + `${cfg.max_open_positions ? `, with at most ${cfg.max_open_positions} positions open at once` : ""}.`);
  return { entry, entryAll: (cfg.entry?.operator ?? "AND") === "AND", exit, other };
}
