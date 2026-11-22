// Learn section — spec 03-ecrans/UI-17 (ADR-029): public hub `/guide` and living strategy sheets
// `/guide/strategies/:id`. Text from Markdown articles, figures from the public API (in % only).
import { useEffect, useState, type ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Link, useParams } from "react-router-dom";
import { apiFetch } from "../../api";
import { useAuth } from "../../context/AuthContext";
import { DASH, fmtTime } from "../shared/format";
import { Card, DataTable, Pct, StatusBadge, strategyColor } from "../shared/ui";
import { article, articles, segments, type Article } from "./content";
import { rules, type PublicConfig } from "./rules";
import "../shared/ui/ui.css";
import "./learn.css";

interface BotSummary { name: string; status: string; since: string | null; net_pct: string | null; trades: number }
export interface StrategySummary { id: string; name: string; version: string; published_at: string | null; bot: BotSummary | null }
export interface StrategyDetail extends StrategySummary {
  config: PublicConfig; config_version: string;
  stats: { net_pct: string | null; trades: number; wins: number; losses: number; win_rate_pct: string | null;
           best_pct: string | null; worst_pct: string | null; max_drawdown_pct: string | null; as_of: string | null } | null;
  trades: { symbol: string; opened_at: string | null; closed_at: string | null; result_pct: string | null; exit_reason: string | null }[];
  versions: { version: string; published_at: string | null; reason: string | null }[];
}

function useJson<T>(url: string): { data: T | null; status: "loading" | "ok" | "missing" | "error" } {
  const [state, setState] = useState<{ data: T | null; status: "loading" | "ok" | "missing" | "error" }>({ data: null, status: "loading" });
  useEffect(() => {
    let alive = true;
    setState({ data: null, status: "loading" });
    apiFetch(url).then(async (r) => {
      if (!alive) return;
      if (r.status === 404) return setState({ data: null, status: "missing" });
      if (!r.ok) throw new Error(String(r.status));
      setState({ data: (await r.json()) as T, status: "ok" });
    }).catch(() => alive && setState({ data: null, status: "error" }));
    return () => { alive = false; };
  }, [url]);
  return state;
}

function useTitle(title: string) {
  useEffect(() => { const before = document.title; document.title = `${title} · 0-hl`; return () => { document.title = before; }; }, [title]);
}

const date = (iso: string | null | undefined) => (iso ? new Date(iso).toISOString().slice(0, 10) : DASH);

/** Markdown without raw HTML; internal links through the router. */
function Markdown({ text }: { text: string }) {
  return (
    <ReactMarkdown remarkPlugins={[remarkGfm]} skipHtml
      components={{ a: ({ href, children }) => (href?.startsWith("/") ? <Link to={href}>{children}</Link>
        : <a href={href} target="_blank" rel="noreferrer">{children}</a>) }}>
      {text}
    </ReactMarkdown>
  );
}

// ------------------------------------------------------------------------------ hub

export function GuidePage() {
  useTitle("Learn");
  const { data, status } = useJson<{ items: StrategySummary[] }>("/api/v1/public/strategies");
  const basics = articles("basics");
  const written = Object.fromEntries(articles("strategies").map((a) => [a.id, a]));
  return (
    <div className="hl-page hl-guide">
      <header className="hl-guide-head">
        <h1>Learn</h1>
        <p>How 0-hl trades, strategy by strategy — with the real paper results.</p>
      </header>
      <section aria-labelledby="g-strategies">
        <h2 id="g-strategies">Strategies</h2>
        {status === "loading" && <p className="hl-readonly-note">Loading…</p>}
        {status === "error" && <p className="hl-banner error" role="alert">Strategies unavailable — try again later.</p>}
        <div className="hl-guide-cards">
          {(data?.items ?? []).map((s) => (
            <Card key={s.id} accent={strategyColor(s.id)} className="hl-guide-card" data-testid={`guide-${s.id}`}>
              <h3><Link to={`/guide/strategies/${s.id}`}>{written[s.id]?.title ?? s.name}</Link></h3>
              <p className="hl-guide-strap">{written[s.id]?.strap ?? ""}</p>
              <p className="hl-guide-meta">
                {s.bot ? <><StatusBadge status={s.bot.status} /> <Pct value={s.bot.net_pct} signed /> · {s.bot.trades} trades</>
                  : <span>No paper bot</span>}
              </p>
            </Card>
          ))}
        </div>
      </section>
      {basics.length > 0 && (
        <section aria-labelledby="g-basics">
          <h2 id="g-basics">Basics</h2>
          <ul className="hl-guide-list">
            {basics.map((a) => (
              <li key={a.id}><Link to={`/guide/${a.id}`}>{a.title}</Link> <span>{a.strap}{a.readingMinutes ? ` · ${a.readingMinutes} min` : ""}</span></li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}

// ------------------------------------------------------------------------------ blocks

function LiveBlock({ d }: { d: StrategyDetail }) {
  const s = d.stats;
  if (!d.bot || !s) return <p className="hl-guide-note">No paper bot running this strategy.</p>;
  const cell = (label: string, v: ReactNode) => <div className="hl-card"><div className="label">{label}</div><div className="value">{v}</div></div>;
  return (
    <section className="hl-guide-block" aria-label="Live result" data-testid="block-live">
      <h3>Live result <small>paper bot {d.bot.name} · since {date(d.bot.since)} · as of {fmtTime(s.as_of)} UTC</small></h3>
      <div className="hl-cards">
        {cell("net result", <Pct value={s.net_pct} signed />)}
        {cell("trades", s.trades)}
        {cell("win rate", <Pct value={s.win_rate_pct} places={0} />)}
        {cell("best trade", <Pct value={s.best_pct} signed />)}
        {cell("worst trade", <Pct value={s.worst_pct} signed />)}
        {cell("max drawdown", <Pct value={s.max_drawdown_pct} />)}
      </div>
      <p className="hl-guide-note">Net of fees, on fictitious capital. A few trades prove nothing, good or bad.</p>
    </section>
  );
}

function RulesBlock({ d }: { d: StrategyDetail }) {
  const r = rules(d.config);
  return (
    <section className="hl-guide-block hl-guide-rules" aria-label="The rules" data-testid="block-rules">
      <h3>Version {d.config_version}</h3>
      <p><strong>It buys when {r.entryAll ? "all" : "any"} of these are true:</strong></p>
      <ul>{r.entry.map((s, i) => <li key={i}>{s}</li>)}</ul>
      <p><strong>It sells:</strong></p>
      <ul>{r.exit.map((s, i) => <li key={i}>{s}</li>)}</ul>
      <ul className="hl-guide-other">{r.other.map((s, i) => <li key={i}>{s}</li>)}</ul>
    </section>
  );
}

/** Small static radar with the zone where the strategy buys (02-design/05 axes, 1h scales). */
export function RadarZone({ zone, id }: { zone: Article["radarZone"]; id: string }) {
  if (!zone) return null;
  const S = 220, c = S / 2, R = 90, X = 5, Y = 2;
  const px = (v: number) => c + (Math.max(-X, Math.min(X, v)) / X) * R;
  const py = (v: number) => c - (Math.max(-Y, Math.min(Y, v)) / Y) * R;
  const [x0, x1] = zone.x, [y0, y1] = zone.y;
  return (
    <figure className="hl-guide-block hl-guide-radar" data-testid="block-radar">
      <svg viewBox={`0 0 ${S} ${S + 16}`} role="img"
        aria-label={`Zone where it buys: ${x0} to ${x1} percentage points versus BTC, volatility ${2 ** y0}× to ${2 ** y1}× BTC's`}>
        {[1 / 3, 2 / 3, 1].map((k) => <circle key={k} cx={c} cy={c} r={R * k} className="ring" />)}
        <line x1={c - R} x2={c + R} y1={c} y2={c} className="ring" /><line x1={c} x2={c} y1={c - R} y2={c + R} className="ring" />
        <rect x={px(x0)} y={py(y1)} width={px(x1) - px(x0)} height={py(y0) - py(y1)} rx={4}
          style={{ fill: strategyColor(id), stroke: strategyColor(id) }} className="zone" />
        <circle cx={c} cy={c} r={4} className="btc" /><text x={c} y={c + 16} className="lbl">BTC</text>
        <text x={c} y={10} className="lbl">Higher volatility</text>
        <text x={c} y={S + 12} className="lbl">Lower volatility</text>
      </svg>
      <figcaption>Left: lags BTC · right: beats BTC, over the last hour. <Link to="/">See the live radar</Link></figcaption>
    </figure>
  );
}

function TradesBlock({ d }: { d: StrategyDetail }) {
  if (!d.trades.length) return <p className="hl-guide-note" data-testid="block-trades">No trade yet.</p>;
  return (
    <div data-testid="block-trades">
      <DataTable label="Latest trades">
        <thead><tr><th>Pair</th><th>Opened (UTC)</th><th>Closed (UTC)</th><th className="num">Result</th><th>Exit</th></tr></thead>
        <tbody>{d.trades.map((t, i) => (
          <tr key={i}><td>{t.symbol}</td><td className="hl-num">{date(t.opened_at)} {fmtTime(t.opened_at)}</td>
            <td className="hl-num">{date(t.closed_at)} {fmtTime(t.closed_at)}</td>
            <td className="num"><Pct value={t.result_pct} signed /></td><td>{(t.exit_reason ?? DASH).replace(/_/g, " ").toLowerCase()}</td></tr>
        ))}</tbody>
      </DataTable>
    </div>
  );
}

function StudyBlock({ a }: { a: Article | null }) {
  const s = a?.study;
  if (!s) return null;
  return (
    <section className="hl-guide-block hl-guide-study" aria-label="Study" data-testid="block-study">
      <p><strong>{s.verdict ?? DASH}</strong>{s.criteria ? ` — ${s.criteria} criteria passed` : ""}{s.date ? ` · study of ${s.date}` : ""}</p>
      {s.summary && <p>{s.summary}</p>}
    </section>
  );
}

function VersionsBlock({ d }: { d: StrategyDetail }) {
  return (
    <ul className="hl-guide-versions" data-testid="block-versions">
      {d.versions.map((v) => <li key={v.version}><span className="hl-num">{v.version}</span> · {date(v.published_at)}{v.reason ? ` · ${v.reason}` : ""}</li>)}
    </ul>
  );
}

// ------------------------------------------------------------------------------ strategy sheet

export function StrategySheet() {
  const { id = "" } = useParams<{ id: string }>();
  const { user } = useAuth();
  const a = article("strategies", id);
  const { data: d, status } = useJson<StrategyDetail>(`/api/v1/public/strategies/${encodeURIComponent(id)}`);
  useTitle(a?.title ?? d?.name ?? "Strategy");
  if (status === "missing") return <div className="hl-page"><div className="hl-state-box">Unknown strategy. <Link to="/guide">Back to Learn</Link></div></div>;
  const block = (name: string): ReactNode => {
    if (name === "radar") return <RadarZone zone={a?.radarZone ?? null} id={id} />;
    if (name === "study") return <StudyBlock a={a} />;
    if (!d) return status === "error" ? <p className="hl-guide-note">Live data unavailable.</p> : <p className="hl-guide-note">Loading…</p>;
    return { live: <LiveBlock d={d} />, rules: <RulesBlock d={d} />, trades: <TradesBlock d={d} />, versions: <VersionsBlock d={d} /> }[name] ?? null;
  };
  // without an article yet: the live blocks only
  const parts = a ? segments(a.body) : [{ block: "live" }, { text: "## The rules" }, { block: "rules" }, { text: "## Latest trades" },
    { block: "trades" }, { text: "## Version history" }, { block: "versions" }];
  return (
    <div className="hl-page hl-guide">
      <article className="hl-guide-article" style={{ ["--accent" as string]: strategyColor(id) }}>
        <p className="hl-guide-crumb"><Link to="/guide">Learn</Link> / Strategies</p>
        <h1><span className="hl-dot" aria-hidden="true" /> {a?.title ?? d?.name ?? id}</h1>
        {a?.strap && <p className="hl-guide-lede">{a.strap}</p>}
        <p className="hl-guide-badges">
          {d && <span className="hl-badge hl-tone-info">version {d.version}</span>}
          {d?.bot && <span className="hl-badge hl-tone-ok">paper observation since {date(d.bot.since)}</span>}
          {a?.readingMinutes && <span className="hl-badge hl-tone-idle">{a.readingMinutes} min read</span>}
          {user && d?.bot && <Link to="/terminal" className="hl-guide-terminal">Open in the Terminal →</Link>}
        </p>
        {!a && <p className="hl-guide-note">The full article for this strategy is coming soon.</p>}
        {parts.map((p, i) => ("block" in p ? <div key={i}>{block(p.block)}</div> : <Markdown key={i} text={p.text} />))}
      </article>
    </div>
  );
}

export function BasicsArticle() {
  const { slug = "" } = useParams<{ slug: string }>();
  const a = article("basics", slug);
  useTitle(a?.title ?? "Learn");
  if (!a) return <div className="hl-page"><div className="hl-state-box">Unknown article. <Link to="/guide">Back to Learn</Link></div></div>;
  return (
    <div className="hl-page hl-guide">
      <article className="hl-guide-article">
        <p className="hl-guide-crumb"><Link to="/guide">Learn</Link> / Basics</p>
        <h1>{a.title}</h1>
        {a.strap && <p className="hl-guide-lede">{a.strap}</p>}
        <Markdown text={a.body} />
      </article>
    </div>
  );
}
