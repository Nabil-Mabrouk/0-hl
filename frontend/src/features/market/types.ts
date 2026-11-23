// Market reports — spec 03-ecrans/UI-18 (ADR-030). Shapes of the public API.
export interface Tag { tag: string; source: "rules" | "model" }
export interface ReportSummary {
  id: string; cadence: "1h" | "4h" | "24h"; period_start: string; period_end: string;
  status: "PUBLISHED" | "DATA_ONLY" | "MISSED"; generated_by: "llm" | "data-only" | null;
  title: string | null; summary: string[]; tags: Tag[];
  btc_return_pct: string | null; breadth_pct: string | null; dispersion_pp: string | null;
  btc_vol_ratio: string | null; volume_ratio: string | null;
}
export interface Chart { id: string; caption: string; data: Record<string, unknown> | null }
export interface Report extends ReportSummary {
  sections: { id: string; heading: string; body: string }[];
  charts: Chart[]; model: string | null; previous_id: string | null; next_id: string | null;
}
export interface LatestReport extends ReportSummary { charts: Chart[] }

export const TAG_LABEL: Record<string, string> = {
  "btc-up": "BTC up", "btc-down": "BTC down", "btc-flat": "BTC flat", "broad-rally": "Broad rally",
  "broad-selloff": "Broad sell-off", "alt-rotation": "Rotation into alts", "btc-dominance": "BTC holds up best",
  "high-volatility": "High volatility", calm: "Calm", "volume-surge": "Volume surge", "thin-volume": "Thin volume",
  "high-dispersion": "High dispersion", "wide-spreads": "Wide spreads",
};
export const TAGS = Object.keys(TAG_LABEL);
export const CADENCE_MINUTES: Record<string, number> = { "1h": 60, "4h": 240, "24h": 1440 };

export function periodLabel(r: Pick<ReportSummary, "period_start" | "period_end" | "cadence">): string {
  const end = new Date(r.period_end);
  const day = end.toISOString().slice(0, 10);
  const hm = (d: Date) => `${String(d.getUTCHours()).padStart(2, "0")}:${String(d.getUTCMinutes()).padStart(2, "0")}`;
  return r.cadence === "24h" ? `${new Date(r.period_start).toISOString().slice(0, 10)} → ${day} 00:00 UTC`
    : `${day} · ${hm(new Date(r.period_start))}–${hm(end)} UTC`;
}
