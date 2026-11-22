// Market radar data and pure geometry — spec 02-design/05 (ADR-028). No DOM here: unit-testable.
import type { Stage, StageState } from "../shared/ui";

export type Outcome = "NO_SIGNAL" | "UNKNOWN" | "COOLDOWN" | "HELD" | "SIGNAL" | "FILTERED" | "REFUSED" | "NOT_FILLED" | "ENTERED";

export interface RadarPair {
  symbol: string; return_pct: string | null; excess_pp: string | null; vol_ratio: string | null;
  y: string; minutes_traded: number; judgeable: boolean;
}
export interface RadarBot {
  name: string; status: string; strategy: { id: string | null; version: string | null; name: string | null };
  net_pct: string | null;
  last_cycle: { window_end: string | null; evaluated: number | null; signals: number | null;
                outcomes: Record<string, { r: Outcome; why?: string }> } | null;
  open_positions: string[];
}
export interface RadarData {
  generated_at: string; window_minutes: number; window_end: string; stale: boolean;
  reference: { symbol: string; return_pct: string | null; volatility_pct: string | null };
  pairs: RadarPair[]; pairs_without_data: number; bots: RadarBot[];
}

/** Fixed scales (02-design/05 « Calcul » 5): x in ± pp per window, y = log2 ratio in [−2, 2]. */
export const X_MAX: Record<number, number> = { 60: 5, 240: 10, 1440: 20 };
export const Y_MAX = 2;

export interface Point { symbol: string; nx: number; ny: number; clippedX: -1 | 0 | 1; clippedY: -1 | 0 | 1; hollow: boolean }

/** Normalised position in [−1, 1]² (BTC at 0,0); values beyond the scale are pinned to the edge. */
export function normalise(p: RadarPair, windowMinutes: number): Point {
  const xmax = X_MAX[windowMinutes] ?? 5;
  const ex = Number(p.excess_pp);
  const x = Number.isFinite(ex) ? ex / xmax : 0;
  const yv = p.judgeable ? Number(p.y) : 0;
  const y = Number.isFinite(yv) ? yv / Y_MAX : 0;
  const clip = (v: number): -1 | 0 | 1 => (v > 1 ? 1 : v < -1 ? -1 : 0);
  return { symbol: p.symbol, nx: Math.max(-1, Math.min(1, x)), ny: Math.max(-1, Math.min(1, y)),
           clippedX: clip(x), clippedY: clip(y), hollow: !p.judgeable };
}

export const smoothstep = (t: number) => { const k = Math.max(0, Math.min(1, t)); return k * k * (3 - 2 * k); };

/** Pipeline steps lit by an outcome (02-design/05 « Ce que chaque bot a conclu »). */
const STEPS: Record<Outcome, { on: Stage[]; stop?: Stage }> = {
  NO_SIGNAL: { on: ["Scan"] }, UNKNOWN: { on: ["Scan"] }, COOLDOWN: { on: ["Scan"] }, HELD: { on: ["Scan"] },
  SIGNAL: { on: ["Scan", "Check"] },
  FILTERED: { on: ["Scan"], stop: "Check" },
  REFUSED: { on: ["Scan", "Check"], stop: "Size" },
  NOT_FILLED: { on: ["Scan", "Check", "Size"], stop: "Execute" },
  ENTERED: { on: ["Scan", "Check", "Size", "Execute"] },
};
const RANK: Outcome[] = ["ENTERED", "NOT_FILLED", "REFUSED", "FILTERED", "SIGNAL", "COOLDOWN", "HELD", "NO_SIGNAL", "UNKNOWN"];
export const WIRED: Outcome[] = ["FILTERED", "REFUSED", "NOT_FILLED", "ENTERED", "SIGNAL"];

/** Steps of an outcome, revealed up to `shown` steps (animation); the stop step comes last. */
export function stagesFor(outcome: Outcome | null, shown = Infinity): Partial<Record<Stage, StageState>> {
  if (!outcome) return {};
  const s = STEPS[outcome];
  const seq: [Stage, StageState][] = [...s.on.map((x) => [x, "on"] as [Stage, StageState]),
    ...(s.stop ? [[s.stop, "stop"] as [Stage, StageState]] : [])];
  return Object.fromEntries(seq.slice(0, Math.max(0, shown)));
}
export const stepCount = (o: Outcome) => STEPS[o].on.length + (STEPS[o].stop ? 1 : 0);

const WHY: Record<string, string> = {
  spread: "Spread too wide", min_order_liquidity: "Book too thin", abnormal_move: "Abnormal move",
  data_freshness: "Data not live", pair_status: "Pair not online", volume_floor: "Volume too low",
  FEED_NOT_LIVE: "Feed not live",
};
export const why = (w?: string) => (w ? WHY[w] ?? w.replace(/_/g, " ").toLowerCase() : "");
const LABEL: Record<Outcome, string> = {
  ENTERED: "Paper fill", NOT_FILLED: "Not filled", REFUSED: "Refused by sizer", FILTERED: "Filtered",
  SIGNAL: "Signal detected", COOLDOWN: "Cooldown", HELD: "Position held", NO_SIGNAL: "No signal", UNKNOWN: "Data unknown",
};

/** Headline of a bot card: its most advanced outcome, or the scan counter. */
export function headline(bot: RadarBot, selected?: string): { pair: string | null; outcome: Outcome | null; line: string } {
  if (bot.status === "PAUSED" && !bot.last_cycle) return { pair: null, outcome: null, line: "Entries paused" };
  const c = bot.last_cycle;
  if (!c) return { pair: null, outcome: null, line: "Waiting for scan" };
  const entries = Object.entries(c.outcomes ?? {});
  const sel = selected ? entries.find(([s]) => s === selected) : undefined;
  const best = sel ?? entries.filter(([, o]) => WIRED.includes(o.r))
    .sort((a, b) => RANK.indexOf(a[1].r) - RANK.indexOf(b[1].r))[0];
  const counter = `${c.evaluated ?? entries.length} scanned · ${c.signals ?? 0} signal${c.signals === 1 ? "" : "s"}`;
  if (!best) return { pair: null, outcome: entries.length ? "NO_SIGNAL" : null, line: counter };
  const [pair, o] = best;
  return { pair, outcome: o.r, line: `${pair} · ${LABEL[o.r]}${o.why ? ` · ${why(o.why)}` : ""}` };
}

/** Pairs that get a wire for this bot: signals, open positions and the selected pair. */
export function wiredPairs(bot: RadarBot, selected?: string): string[] {
  const out = new Set<string>();
  for (const [s, o] of Object.entries(bot.last_cycle?.outcomes ?? {})) if (WIRED.includes(o.r)) out.add(s);
  for (const s of bot.open_positions) out.add(s);
  if (selected && (bot.last_cycle?.outcomes?.[selected] || bot.open_positions.includes(selected))) out.add(selected);
  return [...out];
}

export function readout(p: RadarPair | undefined, ref: string): string {
  if (!p) return ref + " · reference";
  const ex = p.excess_pp === null ? "—" : `${Number(p.excess_pp) > 0 ? "+" : ""}${p.excess_pp}`;
  const vol = p.vol_ratio === null ? "—" : `${p.vol_ratio}×`;
  return `${p.symbol} · ${ex} pp vs BTC · ${vol} BTC volatility · ${p.minutes_traded} min traded${p.judgeable ? "" : " · thin trading"}`;
}

export function cycleLabel(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "—" : `${String(d.getUTCHours()).padStart(2, "0")}:${String(d.getUTCMinutes()).padStart(2, "0")} UTC`;
}
