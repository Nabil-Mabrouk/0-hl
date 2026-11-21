// URL filters shared by Trades (UI-03) and Stats (UI-12) — spec 03-ecrans/M2-trades-et-statistiques.
export type Period = "today" | "7d" | "30d" | "all" | "custom";

export interface Filters {
  bot: string;
  period: Period;
  since: string;   // YYYY-MM-DD (custom)
  until: string;   // YYYY-MM-DD (custom, exclusive end = next day)
  pair: string;
  reason: string;
  version: string;
}

const PERIODS: Period[] = ["today", "7d", "30d", "all", "custom"];

export function readFilters(p: URLSearchParams): Filters {
  const period = p.get("period") as Period | null;
  return {
    bot: p.get("bot") ?? "", period: period && PERIODS.includes(period) ? period : "7d",
    since: p.get("since") ?? "", until: p.get("until") ?? "", pair: p.get("pair") ?? "",
    reason: p.get("reason") ?? "", version: p.get("version") ?? "",
  };
}

export function writeFilters(f: Filters): URLSearchParams {
  const out = new URLSearchParams();
  for (const [k, v] of Object.entries(f)) if (v && !(k === "period" && v === "7d")) out.set(k, v);
  return out;
}

/** Period → [since, until) as ISO strings (UTC). */
export function periodRange(f: Filters, now: Date = new Date()): { since?: string; until?: string } {
  const day = (d: Date) => new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate()));
  const back = (days: number) => new Date(now.getTime() - days * 86_400_000).toISOString();
  switch (f.period) {
    case "today": return { since: day(now).toISOString() };
    case "7d": return { since: back(7) };
    case "30d": return { since: back(30) };
    case "all": return {};
    case "custom": {
      const out: { since?: string; until?: string } = {};
      if (f.since) out.since = new Date(`${f.since}T00:00:00Z`).toISOString();
      if (f.until) out.until = new Date(new Date(`${f.until}T00:00:00Z`).getTime() + 86_400_000).toISOString();
      return out;
    }
  }
}

/** API query string for /trades, /trades.csv and /stats. */
export function apiQuery(f: Filters, extra: Record<string, string> = {}, now?: Date): string {
  const q = new URLSearchParams();
  const r = periodRange(f, now);
  if (f.bot) q.set("bot_id", f.bot);
  if (r.since) q.set("since", r.since);
  if (r.until) q.set("until", r.until);
  if (f.pair) q.set("instrument_id", f.pair);
  if (f.reason) q.set("exit_reason", f.reason);
  if (f.version) q.set("strategy_version_id", f.version);
  for (const [k, v] of Object.entries(extra)) q.set(k, v);
  return q.toString();
}

export const EXIT_REASONS = ["TAKE_PROFIT", "STOP", "TIME", "EXIT_CONDITION", "STATUS", "EMERGENCY"];
