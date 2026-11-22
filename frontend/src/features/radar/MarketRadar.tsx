// Market radar — spec 02-design/05 (ADR-028). BTC at the centre; x = return vs BTC, y = volatility
// vs BTC (log2); wires from the pairs a bot just evaluated to its card. Animation replays each new
// cycle (sweep → glide → wires and steps per bot); none under prefers-reduced-motion.
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { BotCard, Board, Pct, Select, strategyColor, type Stage, type StageState } from "../shared/ui";
import {
  bubbleText, cycleLabel, headline, normalise, readout, smoothstep, stagesFor, stepCount, wiredPairs,
  type Outcome, type Point, type RadarData,
} from "./model";
import "./radar.css";

// jsdom (tests) has no 2D canvas: the figure is skipped, cards and readout stay testable
const NO_CANVAS = typeof navigator !== "undefined" && /jsdom/i.test(navigator.userAgent);
const SWEEP = 1.6, GLIDE = 1.35, BOT_GAP = 0.4, STEP = 1.1, FADE = 1.7, MAX_WIRES = 6;

/** Radius of the radar circle: room left above and below for the axis labels (operator review 2026-11-22). */
const radiusOf = (w: number, h: number) => Math.max(40, Math.min(w / 2 - 24, h / 2 - 48));

function reducedMotion(): boolean {
  return typeof window !== "undefined" && !!window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
}

function token(name: string, fallbackVar: string): string {
  if (typeof document === "undefined") return fallbackVar;
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallbackVar;
}

function colourOf(strategyId: string | null): string {
  const m = /var\((--[^)]+)\)/.exec(strategyColor(strategyId));
  return m ? token(m[1], "gray") : "gray";
}

interface Props {
  data: RadarData | null;
  failed?: boolean;
  title?: ReactNode;
  windowChoice?: { value: string; options: string[]; onChange: (w: string) => void };
  selectedBot?: string;
  onSelectBot?: (name: string) => void;
  onStage?: (bot: string, stage: Stage) => void;
}

export default function MarketRadar({ data, failed, title = "Market radar", windowChoice, selectedBot, onSelectBot, onStage }: Props) {
  const reduced = useMemo(reducedMotion, []);
  const [playing, setPlaying] = useState(!reduced);
  const [selected, setSelected] = useState<string>("");
  const [hover, setHover] = useState<{ symbol: string; x: number; y: number } | null>(null);
  const [clock, setClock] = useState(reduced ? 1e6 : 0);        // seconds since the cycle started
  const plotRef = useRef<HTMLCanvasElement>(null);
  const wiresRef = useRef<HTMLCanvasElement>(null);
  const sceneRef = useRef<HTMLDivElement>(null);
  const cardsRef = useRef<Record<string, HTMLDivElement | null>>({});
  const prevPos = useRef<Record<string, { nx: number; ny: number }>>({});
  const curPos = useRef<Record<string, { nx: number; ny: number }>>({});
  const cycleKey = data ? `${data.window_end}|${data.bots.map((b) => b.last_cycle?.window_end).join(",")}` : "";
  const lastKey = useRef("");

  const points: Point[] = useMemo(() => (data ? data.pairs.map((p) => normalise(p, data.window_minutes)) : []), [data]);
  const bots = data?.bots ?? [];
  const pairBySymbol = useMemo(() => Object.fromEntries((data?.pairs ?? []).map((p) => [p.symbol, p])), [data]);

  // a new cycle: remember where the points were, then replay
  useEffect(() => {
    if (!data || cycleKey === lastKey.current) return;
    lastKey.current = cycleKey;
    prevPos.current = { ...curPos.current };
    curPos.current = Object.fromEntries(points.map((p) => [p.symbol, { nx: p.nx, ny: p.ny }]));
    setClock(reduced ? 1e6 : 0);
  }, [cycleKey, data, points, reduced]);

  // animation loop: runs only while playing, visible and not finished
  const total = SWEEP + BOT_GAP * Math.max(0, bots.length - 1) + STEP * 5 + FADE + 0.5;
  useEffect(() => {
    if (!playing || reduced || clock > total) return;
    let raf = 0, prev: number | undefined;
    const tick = (t: number) => {
      if (document.visibilityState === "hidden") { prev = undefined; raf = requestAnimationFrame(tick); return; }
      const dt = prev === undefined ? 0 : Math.max(0, Math.min(0.1, (t - prev) / 1000));   // read now, not in the updater
      prev = t;
      if (dt) setClock((c) => c + dt);
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [playing, reduced, clock > total, total]);   // eslint-disable-line react-hooks/exhaustive-deps

  // per bot: the outcome shown, how many steps are revealed, and its wires
  const botViews = bots.map((b, i) => {
    const h = headline(b, selected || undefined);
    const start = SWEEP + BOT_GAP * i;
    const active = clock >= start;
    const shown = h.outcome ? Math.floor((clock - start) / STEP) + 1 : 0;
    const stages: Partial<Record<Stage, StageState>> = active ? stagesFor(h.outcome, shown) : {};
    const outcomeCount = h.outcome ? stepCount(h.outcome as Outcome) : 1;
    const wireAlive = reduced || (active && clock < start + outcomeCount * STEP + FADE);
    return { bot: b, h, stages, active, wireAlive, line: active ? h.line : "Waiting for scan" };
  });

  const draw = useCallback(() => {
    const canvas = plotRef.current, wires = wiresRef.current, scene = sceneRef.current;
    if (NO_CANVAS) return;
    const ctx = canvas?.getContext?.("2d");
    if (!canvas || !ctx || !scene) return;
    const ratio = Math.min(window.devicePixelRatio || 1, 2);
    const rect = canvas.getBoundingClientRect();
    if (canvas.width !== Math.round(rect.width * ratio)) { canvas.width = Math.round(rect.width * ratio); canvas.height = Math.round(rect.height * ratio); }
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    ctx.clearRect(0, 0, rect.width, rect.height);
    const cx = rect.width / 2, cy = rect.height / 2, R = radiusOf(rect.width, rect.height);
    const line = token("--hl-line", "gray"), faint = token("--hl-faint", "gray"), accent = token("--hl-accent", "gray");
    const muted = token("--hl-muted", "gray"), mono = token("--hl-mono", "monospace");
    // rings and axes
    ctx.strokeStyle = line; ctx.lineWidth = 1;
    for (const k of [1 / 3, 2 / 3, 1]) { ctx.beginPath(); ctx.arc(cx, cy, R * k, 0, Math.PI * 2); ctx.stroke(); }
    ctx.beginPath(); ctx.moveTo(cx - R, cy); ctx.lineTo(cx + R, cy); ctx.moveTo(cx, cy - R); ctx.lineTo(cx, cy + R); ctx.stroke();
    // sweep
    if (!reduced && clock < SWEEP) {
      const a = -Math.PI / 2 + (clock / SWEEP) * Math.PI * 2;
      ctx.beginPath(); ctx.moveTo(cx, cy); ctx.arc(cx, cy, R, a - 0.55, a); ctx.closePath();
      ctx.fillStyle = accent; ctx.globalAlpha = 0.15; ctx.fill(); ctx.globalAlpha = 1;
    }
    ctx.font = `11px ${mono}`; ctx.textAlign = "center"; ctx.fillStyle = muted;
    // axis names well outside the circle, so that pinned points never cover them
    ctx.fillText("Higher volatility", cx, cy - R - 28);
    ctx.fillText("Lower volatility", cx, cy + R + 36);
    ctx.textAlign = "left"; ctx.fillText("← Lags BTC", Math.max(2, cx - R - 6), cy + R + 36);
    ctx.textAlign = "right"; ctx.fillText("Beats BTC →", Math.min(rect.width - 2, cx + R + 6), cy + R + 36);
    ctx.textAlign = "center";
    // points (glide from the previous cycle)
    const g = reduced ? 1 : smoothstep(clock / GLIDE);
    const wired = new Set(botViews.flatMap((v) => (v.wireAlive ? wiredPairs(v.bot, selected || undefined) : [])));
    const held = new Map<string, string[]>();
    for (const b of bots) for (const s of b.open_positions) held.set(s, [...(held.get(s) ?? []), colourOf(b.strategy.id)]);
    const far = [...points].sort((a, b) => Math.hypot(b.nx, b.ny) - Math.hypot(a.nx, a.ny)).slice(0, 3).map((p) => p.symbol);
    const narrow = rect.width < 420;
    const pos: Record<string, [number, number]> = {};
    for (const p of points) {
      const from = prevPos.current[p.symbol] ?? { nx: p.nx, ny: p.ny };
      const nx = from.nx + (p.nx - from.nx) * g, ny = from.ny + (p.ny - from.ny) * g;
      const x = cx + nx * R, y = cy - ny * R;
      pos[p.symbol] = [x, y];
      const important = p.symbol === selected || wired.has(p.symbol);
      ctx.globalAlpha = prevPos.current[p.symbol] ? 1 : g;
      ctx.beginPath(); ctx.arc(x, y, important ? 4 : 2.6, 0, Math.PI * 2);
      if (p.hollow) { ctx.strokeStyle = faint; ctx.stroke(); } else { ctx.fillStyle = important ? accent : faint; ctx.fill(); }
      (held.get(p.symbol) ?? []).forEach((c, k) => { ctx.strokeStyle = c; ctx.beginPath(); ctx.arc(x, y, 7 + 3 * k, 0, Math.PI * 2); ctx.stroke(); });
      if (p.clippedX || p.clippedY) {   // chevron pointing outwards
        const ang = Math.atan2(-p.clippedY, p.clippedX);
        ctx.fillStyle = faint; ctx.beginPath();
        ctx.moveTo(x + Math.cos(ang) * 9, y + Math.sin(ang) * 9);
        ctx.lineTo(x + Math.cos(ang + 2.5) * 5, y + Math.sin(ang + 2.5) * 5);
        ctx.lineTo(x + Math.cos(ang - 2.5) * 5, y + Math.sin(ang - 2.5) * 5); ctx.fill();
      }
      if (important || (!narrow && far.includes(p.symbol)) || (narrow && far.includes(p.symbol))) {
        ctx.fillStyle = important ? accent : muted; ctx.fillText(p.symbol.replace("/USD", ""), x, y - 9);
      }
      ctx.globalAlpha = 1;
    }
    // BTC at the centre
    ctx.fillStyle = accent; ctx.beginPath(); ctx.arc(cx, cy, 5, 0, Math.PI * 2); ctx.fill();
    ctx.fillText("BTC", cx, cy + 17);
    // wires (hidden on phones: steps and lines stay)
    const wctx = wires?.getContext?.("2d");
    if (!wires || !wctx) return;
    const sRect = scene.getBoundingClientRect();
    if (wires.width !== Math.round(sRect.width * ratio)) { wires.width = Math.round(sRect.width * ratio); wires.height = Math.round(sRect.height * ratio); }
    wctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    wctx.clearRect(0, 0, sRect.width, sRect.height);
    if (sRect.width < 740) return;
    let drawn = 0;
    botViews.forEach((v, i) => {
      if (!v.wireAlive || drawn >= MAX_WIRES) return;
      const card = cardsRef.current[v.bot.name];
      if (!card) return;
      const cr = card.getBoundingClientRect();
      const start = SWEEP + BOT_GAP * i;
      const fade = reduced ? 1 : Math.min(1, (clock - start) * 5);
      for (const sym of wiredPairs(v.bot, selected || undefined)) {
        if (drawn >= MAX_WIRES || !pos[sym]) continue;
        drawn += 1;
        const sx = pos[sym][0] + rect.left - sRect.left, sy = pos[sym][1] + rect.top - sRect.top;
        const ex = cr.left - sRect.left, ey = cr.top + cr.height / 2 - sRect.top;
        const outcome = v.bot.last_cycle?.outcomes?.[sym]?.r;
        // no signal: the wire stops at the card entry (it never reaches the steps)
        const endX = outcome && ["NO_SIGNAL", "UNKNOWN", "COOLDOWN"].includes(outcome) ? ex - 24 : ex;
        wctx.strokeStyle = colourOf(v.bot.strategy.id); wctx.globalAlpha = 0.72 * fade; wctx.lineWidth = 1.5;
        wctx.setLineDash(reduced ? [] : [6, 5]); wctx.lineDashOffset = reduced ? 0 : -clock * 35;
        const bend = Math.max(20, (endX - sx) * 0.45);
        wctx.beginPath(); wctx.moveTo(sx, sy); wctx.bezierCurveTo(sx + bend, sy, endX - bend, ey, endX, ey); wctx.stroke();
        wctx.globalAlpha = 1;
      }
    });
  }, [points, bots, botViews, clock, reduced, selected]);

  useEffect(() => { draw(); }, [draw]);
  useEffect(() => {
    const scene = sceneRef.current;
    if (!scene || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(() => draw());
    ro.observe(scene);
    return () => ro.disconnect();
  }, [draw]);

  /** Nearest point under the pointer (BTC included), within `reach` pixels. */
  const pick = (e: React.MouseEvent<HTMLCanvasElement>, reach: number) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const cx = rect.width / 2, cy = rect.height / 2, R = radiusOf(rect.width, rect.height);
    const px = e.clientX - rect.left, py = e.clientY - rect.top;
    let best: { symbol: string; x: number; y: number; d: number } | null = null;
    const ref = { symbol: data?.reference.symbol ?? "BTC/USD", nx: 0, ny: 0 };
    for (const p of [...points, ref]) {
      const x = cx + p.nx * R, y = cy - p.ny * R, d = Math.hypot(px - x, py - y);
      if (d <= reach && (!best || d < best.d)) best = { symbol: p.symbol, x, y, d };
    }
    return best;
  };
  const onPlotClick = (e: React.MouseEvent<HTMLCanvasElement>) => {
    const best = pick(e, 17);
    if (best && best.symbol !== data?.reference.symbol) setSelected(best.symbol);
  };
  const onPlotMove = (e: React.MouseEvent<HTMLCanvasElement>) => {
    const best = pick(e, 12);
    setHover((h) => (best ? (h?.symbol === best.symbol ? h : { symbol: best.symbol, x: best.x, y: best.y }) : null));
  };

  const stale = data?.stale || (data && Date.now() - Date.parse(data.window_end) > 5 * 60_000);
  const subtitle = data ? `${stale ? "Delayed" : "Live"} · ${data.pairs.length} pairs · cycle ${cycleLabel(data.window_end)}` : failed ? "Unavailable" : "Loading";
  const windowLabel = data ? (data.window_minutes === 60 ? "1h" : data.window_minutes === 240 ? "4h" : "24h") : "1h";

  return (
    <Board className="hl-radar" title={title} subtitle={subtitle}
      actions={<>
        {windowChoice && (
          <Select aria-label="Window" value={windowChoice.value} onChange={(e) => windowChoice.onChange(e.target.value)}>
            {windowChoice.options.map((o) => <option key={o} value={o}>{o}</option>)}
          </Select>
        )}
        {!reduced && <button type="button" className="hl-btn" onClick={() => setPlaying((p) => !p)}>{playing ? "Pause" : "Play"}</button>}
        {!reduced && <button type="button" className="hl-btn" onClick={() => { setPlaying(true); setClock(0); prevPos.current = {}; }}>Replay</button>}
      </>}
      footer={<>
        <label className="hl-radar-inspect">Inspect pair{" "}
          <Select value={selected} onChange={(e) => setSelected(e.target.value)} aria-label="Inspect pair">
            <option value="">BTC/USD (reference)</option>
            {(data?.pairs ?? []).map((p) => <option key={p.symbol} value={p.symbol}>{p.symbol}</option>)}
          </Select>
        </label>
        <output className="hl-num" data-testid="radar-readout">{readout(pairBySymbol[selected], data?.reference.symbol ?? "BTC/USD")}</output>
        <span>{windowLabel} rolling window · BTC reference</span>
      </>}>
      {failed && !data && <p className="hl-banner error" role="alert">Radar unavailable — retrying.</p>}
      {stale && <p className="hl-banner stale">Market data delayed</p>}
      <div className="hl-radar-scene" ref={sceneRef}>
        <div className="hl-radar-plot">
          <canvas ref={plotRef} onClick={onPlotClick} onMouseMove={onPlotMove} onMouseLeave={() => setHover(null)}
            role="img" data-testid="radar-canvas"
            aria-label={`Market radar, ${windowLabel} window. Horizontal axis: return versus BTC in percentage points. Vertical axis: volatility versus BTC, log scale. Use the pair selector for exact values.`} />
          {hover && (
            <div className="hl-radar-bubble" role="tooltip" data-testid="radar-bubble" style={{ left: hover.x, top: hover.y }}>
              <strong>{hover.symbol}</strong>
              {pairBySymbol[hover.symbol] ? <span>{bubbleText(pairBySymbol[hover.symbol])}</span> : <span>reference</span>}
            </div>
          )}
        </div>
        <canvas className="hl-radar-wires" ref={wiresRef} aria-hidden="true" />
        <div className="hl-radar-bots">
          {data && bots.length === 0 && <p className="hl-readonly-note">No bot running</p>}
          {botViews.map((v) => (
            <div key={v.bot.name} ref={(el) => { cardsRef.current[v.bot.name] = el; }}>
              <BotCard name={v.bot.name} strategyId={v.bot.strategy.id ?? ""} strategyName={v.bot.strategy.name ?? v.bot.strategy.id ?? "—"}
                status={v.bot.status} line={v.line} stages={v.stages}
                net={v.bot.net_pct === null ? undefined : <Pct value={v.bot.net_pct} signed />}
                selected={selectedBot === v.bot.name}
                onSelect={onSelectBot ? () => onSelectBot(v.bot.name) : undefined}
                onStage={onStage ? (s) => onStage(v.bot.name, s) : undefined} />
            </div>
          ))}
        </div>
      </div>
    </Board>
  );
}

/** Polls a radar endpoint (landing: public, 20 s; Terminal: authenticated, 15 s and at once when `nonce`
 * changes, i.e. when the realtime stream reports a scan cycle, a fill or a close). */
export function useRadar(url: string | null, intervalMs = 20_000, fetcher: (u: string) => Promise<Response>,
                         nonce?: unknown): { data: RadarData | null; failed: boolean } {
  const [data, setData] = useState<RadarData | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    if (!url) return;
    let alive = true;
    const load = () => fetcher(url)
      .then(async (r) => {
        if (!r.ok) throw new Error(String(r.status));
        const body = (await r.json()) as RadarData;
        if (!Array.isArray(body?.pairs) || !Array.isArray(body?.bots)) throw new Error("bad radar payload");
        if (alive) { setData(body); setFailed(false); }
      })
      .catch(() => alive && setFailed(true));
    void load();
    const t = setInterval(load, intervalMs);
    return () => { alive = false; clearInterval(t); };
  }, [url, intervalMs, fetcher, nonce]);
  return { data, failed };
}
