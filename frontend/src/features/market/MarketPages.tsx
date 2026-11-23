// Market reports pages — spec 03-ecrans/UI-18 « Écrans » (ADR-030): /market (list, filters, calendar,
// search, sort), /market/:id (report with charts), and the home-page card. Public, no amount.
import { useEffect, useMemo, useState, type ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Link, useInRouterContext, useParams, useSearchParams } from "react-router-dom";
import { apiFetch } from "../../api";
import { Button, Input, Pct, Select } from "../shared/ui";
import { ChartView } from "./charts";
import { CADENCE_MINUTES, TAGS, TAG_LABEL, periodLabel, type LatestReport, type Report, type ReportSummary } from "./types";
import "../shared/ui/ui.css";
import "../learn/learn.css";
import "./market.css";

function useJson<T>(url: string | null): { data: T | null; status: "loading" | "ok" | "missing" | "error" } {
  const [s, setS] = useState<{ data: T | null; status: "loading" | "ok" | "missing" | "error" }>({ data: null, status: "loading" });
  useEffect(() => {
    if (!url) return;
    let alive = true;
    setS((p) => ({ data: p.data, status: "loading" }));
    apiFetch(url).then(async (r) => {
      if (!alive) return;
      if (r.status === 404) return setS({ data: null, status: "missing" });
      if (!r.ok) throw new Error(String(r.status));
      setS({ data: (await r.json()) as T, status: "ok" });
    }).catch(() => alive && setS({ data: null, status: "error" }));
    return () => { alive = false; };
  }, [url]);
  return s;
}

function useTitle(title: string) {
  useEffect(() => { const before = document.title; document.title = `${title} · 0-hl`; return () => { document.title = before; }; }, [title]);
}

export function TagChips({ tags }: { tags: ReportSummary["tags"] }) {
  return (
    <span className="hl-mtags">
      {tags.map((t) => <span key={t.tag} className={`hl-mtag${t.source === "model" ? " model" : ""}`}
        title={t.source === "model" ? "suggested by the model" : "computed by rules"}>{TAG_LABEL[t.tag] ?? t.tag}</span>)}
    </span>
  );
}

const CHART_MARK = /^\s*\{\{chart:([a-z0-9-]+)\}\}\s*$/gm;

/** A section body with its {{chart:id}} lines replaced by the frozen charts. */
function SectionBody({ body, charts }: { body: string; charts: Report["charts"] }) {
  const parts: ReactNode[] = [];
  let last = 0, k = 0;
  for (const m of body.matchAll(CHART_MARK)) {
    if (m.index! > last) parts.push(<ReactMarkdown key={k++} remarkPlugins={[remarkGfm]} skipHtml>{body.slice(last, m.index)}</ReactMarkdown>);
    const chart = charts.find((c) => c.id === m[1]);
    if (chart) parts.push(<ChartView key={k++} chart={chart} />);
    last = m.index! + m[0].length;
  }
  if (last < body.length) parts.push(<ReactMarkdown key={k++} remarkPlugins={[remarkGfm]} skipHtml>{body.slice(last)}</ReactMarkdown>);
  return <>{parts}</>;
}

// ------------------------------------------------------------------------------------- list
const FILTERS = ["cadence", "date", "tag", "q", "btc_min", "btc_max", "breadth_min", "breadth_max", "sort", "order", "offset"] as const;
const PAGE = 20;

export function MarketListPage() {
  useTitle("Market reports");
  const [params, setParams] = useSearchParams();
  const [q, setQ] = useState(params.get("q") ?? "");
  const query = useMemo(() => {
    const out = new URLSearchParams();
    for (const k of FILTERS) { const v = params.get(k); if (v) out.set(k, v); }
    out.set("limit", String(PAGE));
    return out.toString();
  }, [params]);
  const { data, status } = useJson<{ items: ReportSummary[]; total: number; offset: number }>(`/api/v1/public/market/reports?${query}`);
  const set = (k: string, v: string) => {
    const next = new URLSearchParams(params);
    if (v) next.set(k, v); else next.delete(k);
    if (k !== "offset") next.delete("offset");
    setParams(next);
  };
  const offset = Number(params.get("offset") ?? 0);
  return (
    <div className="hl-page hl-market">
      <header className="hl-guide-head"><h1>Market reports</h1>
        <p>What the market did, every 4 and 24 hours — written by an AI model from 0-hl's market data, with every number checked.</p></header>
      <form className="hl-mfilters" role="search" onSubmit={(e) => { e.preventDefault(); set("q", q.trim()); }}>
        <Select aria-label="Series" value={params.get("cadence") ?? ""} onChange={(e) => set("cadence", e.target.value)}>
          <option value="">All series</option>{Object.keys(CADENCE_MINUTES).map((c) => <option key={c} value={c}>{c}</option>)}</Select>
        <Input type="date" aria-label="Date" value={params.get("date") ?? ""} onChange={(e) => set("date", e.target.value)} />
        <Select aria-label="Situation" value={params.get("tag") ?? ""} onChange={(e) => set("tag", e.target.value)}>
          <option value="">Any situation</option>{TAGS.map((t) => <option key={t} value={t}>{TAG_LABEL[t]}</option>)}</Select>
        <Input aria-label="BTC min %" placeholder="BTC min %" inputMode="decimal" defaultValue={params.get("btc_min") ?? ""} onBlur={(e) => set("btc_min", e.target.value.trim())} />
        <Input aria-label="BTC max %" placeholder="BTC max %" inputMode="decimal" defaultValue={params.get("btc_max") ?? ""} onBlur={(e) => set("btc_max", e.target.value.trim())} />
        <Input aria-label="Pairs up min %" placeholder="Pairs up min %" inputMode="decimal" defaultValue={params.get("breadth_min") ?? ""} onBlur={(e) => set("breadth_min", e.target.value.trim())} />
        <Select aria-label="Sort" value={`${params.get("sort") ?? "period_end"}:${params.get("order") ?? "desc"}`}
          onChange={(e) => { const [s, o] = e.target.value.split(":"); const next = new URLSearchParams(params); next.set("sort", s); next.set("order", o); next.delete("offset"); setParams(next); }}>
          <option value="period_end:desc">Newest first</option><option value="period_end:asc">Oldest first</option>
          <option value="btc_return:desc">BTC best first</option><option value="btc_return:asc">BTC worst first</option>
          <option value="breadth:desc">Most pairs up</option><option value="breadth:asc">Fewest pairs up</option></Select>
        <Input type="search" aria-label="Search" placeholder="Search titles and summaries" value={q} onChange={(e) => setQ(e.target.value)} />
        <Button type="submit">Search</Button>
        {[...params.keys()].length > 0 && <Button variant="ghost" onClick={() => { setQ(""); setParams(new URLSearchParams()); }}>Reset</Button>}
      </form>
      {status === "error" && <p className="hl-banner error" role="alert">Reports unavailable — try again later.</p>}
      {data && data.items.length === 0 && <p className="hl-state-box">No report matches these filters.</p>}
      <ol className="hl-mlist" aria-label="Reports">
        {(data?.items ?? []).map((r) => (
          <li key={r.id} className={`hl-mitem${r.status === "MISSED" ? " missed" : ""}`} data-testid={`report-${r.id}`}>
            <div className="hl-mitem-meta"><span className="hl-badge hl-tone-info">{r.cadence}</span> <span className="hl-num">{periodLabel(r)}</span></div>
            {r.status === "MISSED" ? <p className="hl-readonly-note">No report for this period (the worker was stopped).</p> : <>
              <h2><Link to={`/market/${r.id}`}>{r.title}</Link></h2>
              <p className="hl-mitem-figs">BTC <Pct value={r.btc_return_pct} signed /> · pairs up <Pct value={r.breadth_pct} places={1} />
                {r.generated_by === "data-only" && <span className="hl-readonly-note"> · data-only</span>}</p>
              <TagChips tags={r.tags} /></>}
          </li>
        ))}
      </ol>
      {data && data.total > PAGE && (
        <nav className="hl-actions hl-mpager" aria-label="Pages">
          <Button disabled={offset === 0} onClick={() => set("offset", String(Math.max(0, offset - PAGE)))}>Newer</Button>
          <span className="hl-num">{offset + 1}–{Math.min(offset + PAGE, data.total)} of {data.total}</span>
          <Button disabled={offset + PAGE >= data.total} onClick={() => set("offset", String(offset + PAGE))}>Older</Button>
        </nav>
      )}
    </div>
  );
}

// ------------------------------------------------------------------------------------- one report
export function MarketReportPage() {
  const { id = "" } = useParams<{ id: string }>();
  const { data: r, status } = useJson<Report>(`/api/v1/public/market/reports/${encodeURIComponent(id)}`);
  useTitle(r?.title ?? "Market report");
  if (status === "missing") return <div className="hl-page"><div className="hl-state-box">Unknown report. <Link to="/market">All reports</Link></div></div>;
  if (status === "error") return <div className="hl-page"><p className="hl-banner error" role="alert">Report unavailable.</p></div>;
  if (!r) return <div className="hl-page"><div className="hl-state-box">Loading…</div></div>;
  const placed = new Set(r.sections.flatMap((s) => [...s.body.matchAll(CHART_MARK)].map((m) => m[1])));
  const loose = r.charts.filter((c) => !placed.has(c.id));
  return (
    <div className="hl-page hl-market">
      <article className="hl-guide-article hl-mreport">
        <p className="hl-guide-crumb"><Link to="/market">Market reports</Link> / {r.cadence}</p>
        <p className="hl-mitem-meta"><span className="hl-badge hl-tone-info">{r.cadence}</span> <span className="hl-num">{periodLabel(r)}</span></p>
        <h1>{r.title}</h1>
        <TagChips tags={r.tags} />
        <ul className="hl-msummary">{r.summary.map((s, i) => <li key={i}>{s}</li>)}</ul>
        {r.sections.map((s) => (
          <section key={s.id} aria-label={s.heading}>
            {s.heading && <h2>{s.heading}</h2>}
            <SectionBody body={s.body} charts={r.charts} />
          </section>
        ))}
        {loose.map((c) => <ChartView key={c.id} chart={c} />)}
        <p className="hl-mfoot">{r.generated_by === "llm"
          ? `Generated by an AI model (${r.model ?? "—"}) from 0-hl market data · every number checked automatically · not investment advice.`
          : "Data-only report: charts and a summary built from 0-hl market data, without AI text · not investment advice."}
          {" "}<Link to="/">See the live radar</Link></p>
        <nav className="hl-actions hl-mpager" aria-label="Series">
          {r.previous_id ? <Link className="hl-btn" to={`/market/${r.previous_id}`}>← Previous {r.cadence}</Link> : <span />}
          {r.next_id && <Link className="hl-btn" to={`/market/${r.next_id}`}>Next {r.cadence} →</Link>}
        </nav>
      </article>
    </div>
  );
}

// ------------------------------------------------------------------------------------- home-page card
/** Router link inside the app, plain link elsewhere (the landing is also rendered outside a router). */
function To({ to, children }: { to: string; children: ReactNode }) {
  return useInRouterContext() ? <Link to={to}>{children}</Link> : <a href={to}>{children}</a>;
}

export function LatestReportCard() {
  const [cadence, setCadence] = useState<"4h" | "24h">("4h");
  const { data, status } = useJson<LatestReport>(`/api/v1/public/market/reports/latest?cadence=${cadence}`);
  const r = data && typeof data.id === "string" && Array.isArray(data.charts) && Array.isArray(data.summary) ? data : null;
  const chart = r?.charts.find((c) => c.id === "leaders") ?? r?.charts[0];
  return (
    <section className="hl-board hl-mcard" aria-label="Latest market report" data-testid="latest-report">
      <div className="hl-board-bar">
        <h2>Latest market report {r && <small>· {periodLabel(r)}</small>}</h2>
        <div className="hl-board-actions" role="group" aria-label="Series">
          {(["4h", "24h"] as const).map((c) => <button key={c} type="button" className={`hl-btn${c === cadence ? " primary" : ""}`}
            aria-pressed={c === cadence} onClick={() => setCadence(c)}>{c}</button>)}
        </div>
      </div>
      <div className="hl-board-body">
        {status === "missing" && <p className="hl-readonly-note">No {cadence} report yet.</p>}
        {status === "error" && <p className="hl-readonly-note">Reports unavailable.</p>}
        {r && (
          <div className="hl-mcard-grid">
            <div>
              <h3><To to={`/market/${r.id}`}>{r.title}</To></h3>
              <TagChips tags={r.tags ?? []} />
              <ul className="hl-msummary">{r.summary.map((s, i) => <li key={i}>{s}</li>)}</ul>
              <p><To to={`/market/${r.id}`}>Read the report →</To> · <To to="/market">All reports</To></p>
            </div>
            {chart && <ChartView chart={chart} />}
          </div>
        )}
      </div>
    </section>
  );
}
