// Learn articles — spec 03-ecrans/UI-17 « Format d'un article » (ADR-029).
// Markdown files of src/domain/content/<lang>/<kind>/<id>.md, bundled at build time;
// a missing translation falls back to English.
const RAW = import.meta.glob("../../domain/content/*/*/*.md", { query: "?raw", import: "default", eager: true }) as Record<string, string>;

export interface Article {
  lang: string;
  kind: "strategies" | "basics";
  id: string;
  title: string;
  strap: string;
  order: number;
  readingMinutes: number | null;
  radarZone: { x: [number, number]; y: [number, number] } | null;
  study: { date?: string; verdict?: string; criteria?: string; summary?: string } | null;
  body: string;
}

/** `{ x: [0, 3], date: 2026-11-21, verdict: "No" }` → JSON: keys quoted, bare non-numeric values quoted. */
function relaxed(v: string): string {
  // only the text outside quoted strings is rewritten (a summary may contain ":" or ",")
  return v.replace(/"(?:[^"\\]|\\.)*"|([^"]+)/g, (m: string, outside?: string) => (outside === undefined ? m : outside
    .replace(/([{,]\s*)([a-z_]+)\s*:/g, '$1"$2":')
    .replace(/:\s*([^\s,"[\]{}][^,"[\]{}]*?)\s*(?=[,}])/g, (_x: string, bare: string) =>
      (/^-?\d+(\.\d+)?$/.test(bare) ? `: ${bare}` : `: "${bare}"`))));
}

/** Tiny front-matter reader: `key: value` lines; inline `{ ... }` objects are read as relaxed JSON. */
export function parseArticle(path: string, raw: string): Article | null {
  const m = /\/content\/([a-z]{2})\/(strategies|basics)\/([^/]+)\.md$/.exec(path);
  const fm = /^---\r?\n([\s\S]*?)\r?\n---\r?\n?([\s\S]*)$/.exec(raw);
  if (!m || !fm) return null;
  const meta: Record<string, unknown> = {};
  for (const line of fm[1].split(/\r?\n/)) {
    const kv = /^([a-z_]+):\s*(.*?)\s*(#.*)?$/.exec(line);
    if (!kv) continue;
    const v = kv[2];
    if (v.startsWith("{")) {
      try { meta[kv[1]] = JSON.parse(relaxed(v)); } catch { meta[kv[1]] = null; }
    } else if (/^-?\d+(\.\d+)?$/.test(v)) meta[kv[1]] = Number(v);
    else meta[kv[1]] = v.replace(/^"(.*)"$/, "$1");
  }
  return {
    lang: m[1], kind: m[2] as Article["kind"], id: String(meta.id ?? m[3]), title: String(meta.title ?? m[3]),
    strap: String(meta.strap ?? ""), order: Number(meta.order ?? 99),
    readingMinutes: typeof meta.reading_minutes === "number" ? meta.reading_minutes : null,
    radarZone: (meta.radar_zone as Article["radarZone"]) ?? null,
    study: (meta.study as Article["study"]) ?? null, body: fm[2],
  };
}

const ALL: Article[] = Object.entries(RAW).map(([p, raw]) => parseArticle(p, raw)).filter((a): a is Article => a !== null);

export function articles(kind: Article["kind"], lang = "en"): Article[] {
  const ids = new Set(ALL.filter((a) => a.kind === kind).map((a) => a.id));
  return [...ids].map((id) => article(kind, id, lang)!).sort((a, b) => a.order - b.order || a.id.localeCompare(b.id));
}

export function article(kind: Article["kind"], id: string, lang = "en"): Article | null {
  return ALL.find((a) => a.kind === kind && a.id === id && a.lang === lang)
    ?? ALL.find((a) => a.kind === kind && a.id === id && a.lang === "en") ?? null;
}

/** Splits an article body at its reserved lines ({{live}}, {{rules}}…): text and block segments. */
export type Segment = { text: string } | { block: string };
export function segments(body: string): Segment[] {
  const out: Segment[] = [];
  let last = 0;
  for (const m of body.matchAll(/^\{\{([a-z]+)\}\}[ \t]*$/gm)) {
    if (m.index! > last) out.push({ text: body.slice(last, m.index) });
    out.push({ block: m[1] });
    last = m.index! + m[0].length;
  }
  if (last < body.length) out.push({ text: body.slice(last) });
  return out.filter((s) => !("text" in s) || s.text.trim() !== "");
}
