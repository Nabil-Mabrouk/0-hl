// UI-00 public landing sections (spec 03-ecrans/UI-00-accueil-public « Contenu »). Live figures come from
// GET /api/v1/public/stats (no auth, aggregates only); unknown values render as "—", never 0.
import { useEffect, useRef, useState, type ReactNode } from "react";
import { apiFetch } from "../../api";
import { useAuth } from "../../context/AuthContext";
import "./landing-hl.css";

export interface PublicStats {
  bots_total: number | null;
  bots_running: number | null;
  trades_total: number | null;
  trades_24h: number | null;
  open_positions: number | null;
  pairs_live: number | null;
  pairs_subscribed: number | null;
  worker_alive: boolean | null;
  evaluations_24h: number | null;
  market_data_days: string | null;
  last_trade_at: string | null;
  generated_at: string | null;
}

const DASH = "—";
const AGENTS = [
  { name: "SCANNER", text: "reads every trade and order book of the top Kraken USD pairs, minute by minute" },
  { name: "RUG CHECK", text: "refuses thin books, wide spreads, abnormal moves and stale data" },
  { name: "SIZER", text: "sizes each position from a fixed risk budget, liquidity and portfolio limits" },
  { name: "EXECUTOR", text: "fills against the real order book with fees and latency" },
  { name: "EQUITY", text: "marks every position, reconciles the ledger and enforces daily-loss limits" },
];

function prefersReducedMotion(): boolean {
  return typeof window !== "undefined" && !!window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
}

function num(v: unknown): number | null {
  if (typeof v === "number" && Number.isFinite(v)) return v;
  if (typeof v === "string" && v.trim() !== "" && Number.isFinite(Number(v))) return Number(v);
  return null;
}

export function ago(iso: string | null | undefined, now: number = Date.now()): string {
  if (!iso) return DASH;
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return DASH;
  const s = Math.max(0, Math.round((now - t) / 1000));
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} d ago`;
}

/** Animated count-up to `value` (instant under reduced motion); "—" when unknown. */
export function Counter({ value, decimals = 0 }: { value: number | null; decimals?: number }) {
  const [shown, setShown] = useState<number | null>(value);
  const from = useRef(0);
  useEffect(() => {
    if (value === null) { setShown(null); return; }
    if (prefersReducedMotion() || typeof requestAnimationFrame === "undefined") { setShown(value); return; }
    const start = performance.now();
    const base = from.current;
    let raf = 0;
    const step = (t: number) => {
      const k = Math.min(1, (t - start) / 900);
      const eased = 1 - Math.pow(1 - k, 3);
      setShown(base + (value - base) * eased);
      if (k < 1) raf = requestAnimationFrame(step);
      else from.current = value;
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [value]);
  if (shown === null) return <>{DASH}</>;
  return <>{shown.toLocaleString("en-US", { minimumFractionDigits: decimals, maximumFractionDigits: decimals })}</>;
}

export function useStats(intervalMs = 30_000): { stats: PublicStats | null; failed: boolean } {
  const [stats, setStats] = useState<PublicStats | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let alive = true;
    const load = () => apiFetch("/api/v1/public/stats")
      .then(async (r) => {
        if (!r.ok) throw new Error(String(r.status));
        const body = (await r.json()) as PublicStats;
        if (alive) { setStats(body); setFailed(false); }
      })
      .catch(() => alive && setFailed(true));
    void load();
    const t = setInterval(load, intervalMs);
    return () => { alive = false; clearInterval(t); };
  }, [intervalMs]);
  return { stats, failed };
}

/** `greeting`: headline of the manifest hero block (Studio content), shown as a small greeting. */
export default function HlLanding({ greeting }: { greeting?: string }) {
  const { user } = useAuth();
  const { stats, failed } = useStats();
  const s = failed ? null : stats;
  const live = s?.worker_alive === true;
  const terminalHref = user ? "/terminal" : "/login";

  const cards: { label: string; value: ReactNode; hint?: string; id: string }[] = [
    { id: "bots", label: "bots running", value: <><Counter value={num(s?.bots_running)} /><span className="hl-of"> / <Counter value={num(s?.bots_total)} /></span></>, hint: "paper bots trading right now" },
    { id: "trades", label: "trades executed", value: <Counter value={num(s?.trades_total)} />, hint: "closed positions, all time" },
    { id: "trades24", label: "trades · last 24 h", value: <Counter value={num(s?.trades_24h)} />, hint: `${num(s?.open_positions) ?? DASH} position(s) open now` },
    { id: "pairs", label: "pairs watched live", value: <><Counter value={num(s?.pairs_live)} /><span className="hl-of"> / <Counter value={num(s?.pairs_subscribed)} /></span></>, hint: "Kraken USD order books streamed" },
    { id: "evals", label: "market evaluations · 24 h", value: <Counter value={num(s?.evaluations_24h)} />, hint: "pair × minute decisions, each one logged" },
    { id: "days", label: "days of market data", value: <Counter value={num(s?.market_data_days)} decimals={1} />, hint: `last trade ${ago(s?.last_trade_at)}` },
  ];

  return (
    <div className="hl-landing">
      <section className="hl-hero">
        <div className="hl-grid-bg" aria-hidden="true" />
        <div className="hl-hero-copy">
          {greeting && <p className="hl-greet">{greeting}</p>}
          <span className="hl-pill">PAPER — real Kraken prices · simulated execution</span>
          <h1>Trading agents{" "}<br /><span className="hl-grad">you can measure.</span></h1>
          <p className="hl-sub">
            0-hl runs autonomous trading agents on live Kraken market data. Every decision is traced,
            every fee is counted, and every strategy is tested against the real engine before it trades.
          </p>
          <div className="hl-ctas">
            <a className="hl-cta primary" href={terminalHref}>Open the terminal</a>
            <a className="hl-cta ghost" href="#how">How it works</a>
          </div>
        </div>
        <ol className={`hl-pipeline${prefersReducedMotion() ? "" : " hl-animate"}`} aria-label="agent pipeline">
          {AGENTS.map((a, i) => (
            <li key={a.name} style={{ ["--i" as string]: i }}>
              <span className="hl-node" />
              <span className="hl-agent">{a.name}</span>
            </li>
          ))}
        </ol>
      </section>

      <section className="hl-stats" aria-label="live statistics">
        <div className="hl-stats-head">
          <h2>Running right now</h2>
          <span className={`hl-live ${live ? "on" : "off"}`} data-testid="hl-live">
            <i />{s === null ? (failed ? "UNAVAILABLE" : "CONNECTING") : live ? "LIVE" : "OFFLINE"}
          </span>
        </div>
        <div className="hl-cards">
          {cards.map((c) => (
            <div className="hl-card" key={c.id} data-testid={`stat-${c.id}`}>
              <div className="hl-value">{c.value}</div>
              <div className="hl-label">{c.label}</div>
              {c.hint && <div className="hl-hint">{c.hint}</div>}
            </div>
          ))}
        </div>
        <p className="hl-note">Figures refresh every 30 seconds from the production worker. Paper trading: no real order is ever sent.</p>
      </section>

      <section className="hl-how" id="how">
        <h2>Five agents, one accountable loop</h2>
        <div className="hl-steps">
          {AGENTS.map((a, i) => (
            <article key={a.name} className="hl-step">
              <span className="hl-step-n">{String(i + 1).padStart(2, "0")}</span>
              <h3>{a.name}</h3>
              <p>{a.text}</p>
            </article>
          ))}
        </div>
      </section>

      <section className="hl-lab">
        <div>
          <h2>A strategy lab, not a black box</h2>
          <p className="hl-sub">Strategies are versioned and immutable once published. Before a new version trades, it is replayed on weeks of recorded order books by the very engine that runs the bots.</p>
        </div>
        <ul className="hl-facts">
          <li><strong>Immutable versions</strong><span>every bot session records the exact hash of its strategy and risk policy</span></li>
          <li><strong>Backtests on the real engine</strong><span>same scanner, risk gate, sizer and executor — no look-ahead</span></li>
          <li><strong>Fees counted</strong><span>≈ 0.9 % per round trip: most ideas don't survive it, and the lab says so</span></li>
          <li><strong>Bots side by side</strong><span>compare versions on the same market, trade by trade</span></li>
        </ul>
      </section>

      <section className="hl-trust">
        <h2>Transparent by design</h2>
        <div className="hl-trust-grid">
          <p><strong>Paper only.</strong> Real prices, simulated execution, fictitious capital.</p>
          <p><strong>No withdrawal keys.</strong> Live trading is out of scope until it is explicitly approved.</p>
          <p><strong>Not financial advice.</strong> 0-hl is an engineering and research project.</p>
        </div>
      </section>
    </div>
  );
}
