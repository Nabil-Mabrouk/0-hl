// Charts of a market report — spec 03-ecrans/UI-18 « Catalogue de graphiques ». SVG, tokens only,
// readable without colour (values and labels are written), each with a text alternative built
// from its frozen data. Data is never computed here beyond scaling.
import type { ReactNode } from "react";
import { strategyColor } from "../shared/ui";
import type { Chart } from "./types";

type Num = string | number | null | undefined;
const n = (v: Num): number | null => (v === null || v === undefined || v === "" || !Number.isFinite(Number(v)) ? null : Number(v));
const signed = (v: Num, unit = "") => { const x = n(v); return x === null ? "—" : `${x > 0 ? "+" : x < 0 ? "−" : ""}${Math.abs(x)}${unit}`; };
const hm = (iso: string) => { const d = new Date(iso); return `${String(d.getUTCHours()).padStart(2, "0")}:${String(d.getUTCMinutes()).padStart(2, "0")}`; };
const W = 640;

function Frame({ label, height, children }: { label: string; height: number; children: ReactNode }) {
  return <svg className="hl-mchart" viewBox={`0 0 ${W} ${height}`} role="img" aria-label={label} preserveAspectRatio="xMidYMid meet">{children}</svg>;
}

// --------------------------------------------------------------------------- BTC and breadth
function BtcBreadth({ data }: { data: { points: { t: string; btc: Num; up_pct: Num }[] } }) {
  const pts = data.points.filter((p) => n(p.btc) !== null);
  if (pts.length < 2) return <p className="hl-readonly-note">Not enough data.</p>;
  const prices = pts.map((p) => n(p.btc)!), lo = Math.min(...prices), hi = Math.max(...prices);
  const L = 76, R = W - 12, top = 14, h1 = 130, b0 = 170, h2 = 56;
  const price = (v: number) => (v >= 1000 ? Math.round(v) : v).toLocaleString("en-US");
  const x = (i: number) => L + (i / (pts.length - 1)) * (R - L);
  const y = (v: number) => top + (hi === lo ? h1 / 2 : (1 - (v - lo) / (hi - lo)) * h1);
  const yb = (v: number) => b0 + (1 - v / 100) * h2;
  const line = pts.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(n(p.btc)!).toFixed(1)}`).join(" ");
  const area = `M${x(0)},${yb(0)} ` + pts.map((p, i) => `L${x(i).toFixed(1)},${yb(n(p.up_pct) ?? 0).toFixed(1)}`).join(" ") + ` L${x(pts.length - 1)},${yb(0)} Z`;
  const first = pts[0], last = pts[pts.length - 1];
  return (
    <Frame height={b0 + h2 + 22} label={`BTC from ${first.btc} to ${last.btc}; share of pairs up from ${first.up_pct} % to ${last.up_pct} %.`}>
      <text x={4} y={top + h1 / 2 + 4} className="lbl">BTC</text>
      <text x={L - 6} y={top + 4} className="val end">{price(hi)}</text>
      <text x={L - 6} y={top + h1} className="val end">{price(lo)}</text>
      <path d={line} className="series" />
      <text x={4} y={b0 + 10} className="lbl">pairs up</text>
      <line x1={L} x2={R} y1={yb(50)} y2={yb(50)} className="guide" /><text x={L - 6} y={yb(50) + 4} className="val end">50 %</text>
      <path d={area} className="area" />
      <text x={R} y={b0 + h2 + 16} className="val end">{last.up_pct} % up at {hm(last.t)}</text>
      <text x={L} y={b0 + h2 + 16} className="val">{hm(first.t)}</text>
    </Frame>
  );
}

// --------------------------------------------------------------------------- horizontal bars around 0
function Bars({ rows, unit, label }: { rows: { name: string; v: number | null; note?: string }[]; unit: string; label: string }) {
  const max = Math.max(1e-9, ...rows.map((r) => Math.abs(r.v ?? 0)));
  const rowH = 22, L = 120, R = W - 70, mid = (L + R) / 2;
  return (
    <Frame height={rows.length * rowH + 10} label={label}>
      <line x1={mid} x2={mid} y1={0} y2={rows.length * rowH + 4} className="guide" />
      {rows.map((r, i) => {
        const v = r.v ?? 0, w = (Math.abs(v) / max) * (R - mid), yy = i * rowH + 4;
        return (
          <g key={r.name + i}>
            <text x={L - 8} y={yy + 14} className="lbl end">{r.name}</text>
            <rect x={v >= 0 ? mid : mid - w} y={yy + 3} width={Math.max(1, w)} height={rowH - 8} rx={2} className={v >= 0 ? "gain" : "loss"} />
            <text x={v >= 0 ? mid + w + 6 : mid + 6} y={yy + 14} className="val">{r.v === null ? "—" : `${signed(r.v)}${unit}`}{r.note ? ` ${r.note}` : ""}</text>
          </g>
        );
      })}
    </Frame>
  );
}

function Leaders({ data }: { data: { bars: { symbol: string; excess_pp: Num; vol_ratio: Num }[] } }) {
  const rows = data.bars.map((b) => ({ name: b.symbol, v: n(b.excess_pp) }));
  return <Bars rows={rows} unit=" pp" label={`Return against BTC: ${data.bars.map((b) => `${b.symbol} ${signed(b.excess_pp)} pp`).join(", ")}.`} />;
}

function Volatility({ data }: { data: { bars: { symbol: string; vol_ratio: Num }[] } }) {
  const rows = data.bars.map((b) => ({ name: b.symbol, v: n(b.vol_ratio) === null ? null : Math.log2(n(b.vol_ratio)!), note: n(b.vol_ratio) === null ? "" : `(${b.vol_ratio}×)` }));
  return <Bars rows={rows} unit="" label={`Volatility relative to BTC: ${data.bars.map((b) => `${b.symbol} ${b.vol_ratio ?? "—"}×`).join(", ")}.`} />;
}

// --------------------------------------------------------------------------- vertical bars
function Columns({ cols, label, ref1, height = 180 }: { cols: { label: string; v: number; text?: string; cls?: string }[]; label: string; ref1?: { v: number; text: string }; height?: number }) {
  const max = Math.max(1e-9, ...cols.map((c) => c.v), ref1?.v ?? 0);
  const L = 16, R = W - 16, top = 14, h = height - 40, bw = (R - L) / Math.max(1, cols.length);
  const y = (v: number) => top + (1 - v / max) * h;
  const every = Math.ceil(cols.length / 8);
  return (
    <Frame height={height} label={label}>
      {ref1 && <><line x1={L} x2={R} y1={y(ref1.v)} y2={y(ref1.v)} className="guide" /><text x={R} y={y(ref1.v) - 4} className="val end">{ref1.text}</text></>}
      {cols.map((c, i) => (
        <g key={i}>
          <rect x={L + i * bw + 1} y={y(c.v)} width={Math.max(1, bw - 2)} height={top + h - y(c.v)} className={c.cls ?? "col"} />
          {c.text && cols.length <= 12 && <text x={L + i * bw + bw / 2} y={y(c.v) - 4} className="val mid">{c.text}</text>}
          {i % every === 0 && <text x={L + i * bw + bw / 2} y={height - 8} className="lbl mid">{c.label}</text>}
        </g>
      ))}
    </Frame>
  );
}

function Distribution({ data }: { data: { bins: { from: Num; to: Num; count: number }[] } }) {
  const cols = data.bins.map((b) => ({ label: `${signed(b.from)}`, v: b.count, text: b.count ? String(b.count) : "", cls: (n(b.to) ?? 0) <= 0 ? "loss" : "gain" }));
  return <Columns cols={cols} label={`Returns of all pairs: ${data.bins.filter((b) => b.count).map((b) => `${b.count} between ${b.from} and ${b.to} %`).join(", ")}.`} />;
}

function Volume({ data }: { data: { bucket_minutes: number; bars: { t: string; vs_normal: Num }[] } }) {
  const cols = data.bars.map((b) => ({ label: hm(b.t), v: n(b.vs_normal) ?? 0, cls: (n(b.vs_normal) ?? 0) >= 1 ? "gain" : "col" }));
  return <Columns cols={cols} ref1={{ v: 1, text: "normal" }} label={`Volume per ${data.bucket_minutes} minutes relative to normal: ${data.bars.map((b) => `${hm(b.t)} ${b.vs_normal ?? "—"}×`).join(", ")}.`} />;
}

// --------------------------------------------------------------------------- small multiples and tables
function Trend3({ data }: { data: { points: { period_end: string; btc_return_pct: Num; breadth_pct: Num; dispersion_pp: Num }[] } }) {
  const series: [string, keyof (typeof data.points)[number], string][] = [["BTC", "btc_return_pct", " %"], ["Pairs up", "breadth_pct", " %"], ["Dispersion", "dispersion_pp", " pp"]];
  const cw = W / 3, h = 120;
  return (
    <Frame height={h + 30} label={series.map(([name, k, u]) => `${name}: ${data.points.map((p) => `${p[k] ?? "—"}${u}`).join(" → ")}`).join("; ")}>
      {series.map(([name, k], s) => {
        const vals = data.points.map((p) => n(p[k] as Num));
        const known = vals.filter((v): v is number => v !== null);
        const lo = Math.min(0, ...known), hi = Math.max(0, ...known, 1e-9);
        const x = (i: number) => s * cw + 30 + (i / Math.max(1, vals.length - 1)) * (cw - 60);
        const y = (v: number) => 24 + (1 - (v - lo) / (hi - lo || 1)) * (h - 40);
        return (
          <g key={name}>
            <text x={s * cw + cw / 2} y={14} className="lbl mid">{name}</text>
            <line x1={s * cw + 20} x2={s * cw + cw - 20} y1={y(0)} y2={y(0)} className="guide" />
            <path className="series" d={vals.map((v, i) => (v === null ? "" : `${i && vals[i - 1] !== null ? "L" : "M"}${x(i)},${y(v)}`)).join(" ")} />
            {vals.map((v, i) => v !== null && <g key={i}><circle cx={x(i)} cy={y(v)} r={3.5} className="dot" />
              <text x={x(i)} y={y(v) - 8} className="val mid">{signed(v)}</text>
              <text x={x(i)} y={h + 18} className="lbl mid">{hm(data.points[i].period_end)}</text></g>)}
          </g>
        );
      })}
    </Frame>
  );
}

function Liquidity({ data }: { data: { pairs: { symbol: string; spread_start: Num; spread_end: Num; depth_change_pct: Num }[] } }) {
  return (
    <table className="hl-table hl-mtable" aria-label="Liquidity of the most traded pairs">
      <thead><tr><th>Pair</th><th className="num">Spread start (bps)</th><th className="num">Spread end (bps)</th><th className="num">Book depth change</th></tr></thead>
      <tbody>{data.pairs.map((p) => <tr key={p.symbol}><td>{p.symbol}</td><td className="num">{p.spread_start ?? "—"}</td><td className="num">{p.spread_end ?? "—"}</td>
        <td className={`num ${(n(p.depth_change_pct) ?? 0) < 0 ? "hl-loss" : "hl-gain"}`}>{signed(p.depth_change_pct, " %")}</td></tr>)}</tbody>
    </table>
  );
}

function Bots({ data }: { data: { bots: { name: string; strategy: string | null; strategy_id: string | null; signals: number; refusals: Record<string, number>; opened: number; closed: number; net_pct: Num }[] } }) {
  return (
    <table className="hl-table hl-mtable" aria-label="What the paper bots did">
      <thead><tr><th>Bot</th><th className="num">Signals</th><th>Refused</th><th className="num">Opened</th><th className="num">Closed</th><th className="num">Result</th></tr></thead>
      <tbody>{data.bots.map((b) => (
        <tr key={b.name}><td><span className="hl-dot" style={{ background: strategyColor(b.strategy_id) }} aria-hidden="true" /> {b.strategy ?? b.name}</td>
          <td className="num">{b.signals}</td><td>{Object.entries(b.refusals).map(([k, v]) => `${k.replace(/_/g, " ")} ×${v}`).join(", ") || "—"}</td>
          <td className="num">{b.opened}</td><td className="num">{b.closed}</td>
          <td className={`num ${(n(b.net_pct) ?? 0) < 0 ? "hl-loss" : (n(b.net_pct) ?? 0) > 0 ? "hl-gain" : ""}`}>{signed(b.net_pct, " %")}</td></tr>))}</tbody>
    </table>
  );
}

const X_MAX: Record<number, number> = { 60: 5, 240: 10, 1440: 20 };
function RadarSnapshot({ data }: { data: { window_minutes: number; pairs: { symbol: string; excess_pp: Num; y: Num; judgeable: boolean }[];
  bots: { name: string; strategy: string; open_positions: string[] }[] } }) {
  const S = 340, c = S / 2, R = 140, xmax = X_MAX[data.window_minutes] ?? 5;
  const pos = (p: { excess_pp: Num; y: Num; judgeable: boolean }) => {
    const nx = Math.max(-1, Math.min(1, (n(p.excess_pp) ?? 0) / xmax)), ny = p.judgeable ? Math.max(-1, Math.min(1, (n(p.y) ?? 0) / 2)) : 0;
    return [c + nx * R, c - ny * R];
  };
  const far = [...data.pairs].sort((a, b) => Math.hypot(n(b.excess_pp) ?? 0, n(b.y) ?? 0) - Math.hypot(n(a.excess_pp) ?? 0, n(a.y) ?? 0)).slice(0, 4).map((p) => p.symbol);
  const held = new Map<string, string[]>();
  for (const b of data.bots) for (const s of b.open_positions) held.set(s, [...(held.get(s) ?? []), b.strategy]);
  return (
    <svg className="hl-mchart hl-mradar" viewBox={`0 0 ${S} ${S + 24}`} role="img"
      aria-label={`Pairs against BTC at the end of the period. Furthest: ${far.join(", ")}. Held by the bots: ${[...held.keys()].join(", ") || "none"}.`}>
      {[1 / 3, 2 / 3, 1].map((k) => <circle key={k} cx={c} cy={c} r={R * k} className="ring" />)}
      <line x1={c - R} x2={c + R} y1={c} y2={c} className="ring" /><line x1={c} x2={c} y1={c - R} y2={c + R} className="ring" />
      <text x={c} y={12} className="lbl mid">Higher volatility</text><text x={c} y={S - 4} className="lbl mid">Lower volatility</text>
      <text x={c - R} y={S + 18} className="lbl">← Lags BTC</text><text x={c + R} y={S + 18} className="lbl end">Beats BTC →</text>
      {data.pairs.map((p) => {
        const [x, y] = pos(p);
        return (
          <g key={p.symbol}>
            <circle cx={x} cy={y} r={2.8} className={p.judgeable ? "pt" : "pt hollow"} />
            {(held.get(p.symbol) ?? []).map((sid, k) => <circle key={sid} cx={x} cy={y} r={6 + 3 * k} className="held" style={{ stroke: strategyColor(sid) }} />)}
            {(far.includes(p.symbol) || held.has(p.symbol)) && <text x={x} y={y - 8} className="val mid">{p.symbol.replace("/USD", "")}</text>}
          </g>
        );
      })}
      <circle cx={c} cy={c} r={4.5} className="btc" /><text x={c} y={c + 16} className="val mid">BTC</text>
    </svg>
  );
}

export function ChartView({ chart }: { chart: Chart }) {
  const d = chart.data as never;
  let body: ReactNode = <p className="hl-readonly-note">Chart unavailable.</p>;
  if (d) {
    body = ({
      "btc-breadth": () => <BtcBreadth data={d} />, leaders: () => <Leaders data={d} />, volatility: () => <Volatility data={d} />,
      distribution: () => <Distribution data={d} />, volume: () => <Volume data={d} />, trend3: () => <Trend3 data={d} />,
      liquidity: () => <Liquidity data={d} />, bots: () => <Bots data={d} />, radar: () => <RadarSnapshot data={d} />,
    } as Record<string, () => ReactNode>)[chart.id]?.() ?? body;
  }
  return (
    <figure className="hl-mfigure" data-testid={`chart-${chart.id}`}>
      {body}
      <figcaption>{chart.caption}</figcaption>
    </figure>
  );
}
