// UI-16 AI services (spec 03-ecrans/UI-16-services-ia « Écran »): budget bar, services, run journal,
// settings (ADMIN). Agents only read through tools and produce text or drafts; the backend enforces
// roles, limits and the daily budget.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Navigate, useNavigate, useSearchParams } from "react-router-dom";
import { ApiError, getJson, sendJson } from "../shared/client";
import { useTradingAccess } from "../shared/hooks";
import "../shared/ui/ui.css";
import "./ai.css";

export interface Budget {
  daily_budget_usd: string; spent_usd: string; spent_actual_usd: string; spent_estimated_usd: string;
  spent_pct: string | null; exhausted: boolean; reset_at: string;
  proxy: { spend?: string | null; max_budget?: string | null; error?: string }; proxy_gap: boolean | null;
  alerts: { id: string; threshold: number; source: string; spent_usd: string; created_at: string;
    email_sent: boolean; email_error: string | null; acked_at: string | null }[];
  email_configured: boolean;
}
export interface Service { id: string; name: string; description: string; model: string; output: string;
  agent: { id: string; version: string; tools: string[] } }
export interface Step { seq: number; kind: "MODEL_CALL" | "TOOL_CALL"; at: string; detail: Record<string, unknown>;
  tokens_in: number; tokens_out: number; cost_actual_usd: string | null; cost_estimated_usd: string | null;
  duration_ms: number; error_code: string | null }
export interface Run { id: string; service_id: string; agent_id: string; agent_version: string; instructions_sha256: string;
  model: string; input: Record<string, unknown>; status: string; output_text: string | null; output_json: unknown;
  tokens_in: number; tokens_out: number; cost_usd: string; cost_estimated_only: boolean; model_calls: number;
  tool_calls: number; started_at: string; finished_at: string | null; duration_ms: number | null;
  error_code: string | null; error_detail: string | null; steps?: Step[] }

const asError = (e: unknown) => (e instanceof ApiError ? e : new ApiError(0, "NETWORK", String(e), null));
const usd = (s: string | null | undefined, d = 4) => (s === null || s === undefined ? "—" : `${Number(s).toFixed(d)} $`);
const time = (s: string | null) => (s ? s.replace("T", " ").slice(0, 19) : "—");
const FINAL = ["SUCCEEDED", "FAILED", "LIMIT_REACHED", "BUDGET_BLOCKED", "ABANDONED"];

/** Minimal Markdown without HTML: headings, list items, bold; everything else is plain text. */
export function MarkdownText({ text }: { text: string }) {
  const inline = (s: string) => s.split(/(\*\*[^*]+\*\*)/g).map((part, i) =>
    part.startsWith("**") && part.endsWith("**") ? <strong key={i}>{part.slice(2, -2)}</strong> : <span key={i}>{part}</span>);
  return (
    <div className="hl-md">
      {text.split("\n").map((line, i) => {
        const h = /^(#{1,4})\s+(.*)$/.exec(line);
        if (h) return <p key={i} className={`hl-md-h hl-md-h${h[1].length}`}>{inline(h[2])}</p>;
        const li = /^\s*(?:[-*]|\d+\.)\s+(.*)$/.exec(line);
        if (li) return <p key={i} className="hl-md-li">• {inline(li[1])}</p>;
        return line.trim() ? <p key={i}>{inline(line)}</p> : <br key={i} />;
      })}
    </div>
  );
}

export function BudgetBar({ budget }: { budget: Budget | null }) {
  if (!budget) return <div className="hl-ai-budget" data-testid="budget">budget…</div>;
  const pct = budget.spent_pct === null ? 100 : Number(budget.spent_pct);
  const level = budget.exhausted || pct >= 100 ? "red" : pct >= 80 ? "orange" : "ok";
  return (
    <div className={`hl-ai-budget ${level}`} data-testid="budget" role="status">
      <div><strong>AI budget today</strong> {usd(budget.spent_usd)} / {usd(budget.daily_budget_usd, 2)}
        {" "}({budget.spent_pct ?? "—"} %){level === "red" ? " — exhausted" : level === "orange" ? " — over 80 %" : ""}</div>
      <div className="hl-ai-gauge"><span style={{ width: `${Math.min(pct, 100)}%` }} /></div>
      <div className="hl-readonly-note">
        proxy spend {budget.proxy.error ? `unavailable (${budget.proxy.error})` : usd(budget.proxy.spend ?? null)} (≈ 1 min late)
        {budget.proxy_gap ? " — gap with 0-hl spend > 10 %" : ""} · reset {time(budget.reset_at)} UTC
        {budget.spent_estimated_usd !== "0" && Number(budget.spent_estimated_usd) > 0 ? ` · ${usd(budget.spent_estimated_usd)} estimated` : ""}
      </div>
    </div>
  );
}

function ServiceForm({ service, bots, initial, disabled, onRun }: {
  service: Service; bots: { id: string; name: string }[]; initial: URLSearchParams; disabled: string | null;
  onRun: (input: Record<string, unknown>) => void;
}) {
  const [position, setPosition] = useState(initial.get("position_id") ?? "");
  const [bot, setBot] = useState(initial.get("bot_id") ?? "");
  const [days, setDays] = useState("7");
  const [idea, setIdea] = useState("");
  const [from, setFrom] = useState("");
  const input: Record<string, unknown> | null =
    service.id === "explain-trade" ? (position ? { position_id: position } : null)
      : service.id === "bot-review" ? (bot ? { bot_id: bot, days: Number(days) } : null)
        : idea.trim().length >= 10 ? { idea, ...(from ? { from_strategy: from } : {}) } : null;
  return (
    <form className="hl-card hl-ai-service" aria-label={service.name} onSubmit={(e) => { e.preventDefault(); if (input) onRun(input); }}>
      <h3>{service.name}</h3>
      <p className="hl-readonly-note">{service.description} · model <code>{service.model}</code></p>
      {service.id === "explain-trade" && (
        <label className="hl-field">position id<input value={position} onChange={(e) => setPosition(e.target.value)} /></label>)}
      {service.id === "bot-review" && (<>
        <label className="hl-field">bot<select value={bot} onChange={(e) => setBot(e.target.value)}>
          <option value="">—</option>{bots.map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}</select></label>
        <label className="hl-field">days<input type="number" min={1} max={90} value={days} onChange={(e) => setDays(e.target.value)} /></label>
      </>)}
      {service.id === "strategy-draft" && (<>
        <label className="hl-field">idea (10 to 2000 characters)
          <textarea maxLength={2000} rows={4} value={idea} onChange={(e) => setIdea(e.target.value)} /></label>
        <label className="hl-field">starting version (optional, id@version)<input value={from} onChange={(e) => setFrom(e.target.value)} /></label>
      </>)}
      <button type="submit" className="hl-btn" disabled={!input || disabled !== null} title={disabled ?? undefined}>Run</button>
      {disabled && <div className="hl-field-error">{disabled}</div>}
    </form>
  );
}

function StepRow({ s }: { s: Step }) {
  const d = s.detail as Record<string, unknown>;
  const title = s.kind === "TOOL_CALL" ? `tool ${String(d.tool)}` : `model call${d.attempt === 2 ? " (retry)" : ""}`;
  return (
    <details className={`hl-ai-step${s.error_code ? " error" : ""}`} data-testid={`step-${s.seq}`}>
      <summary>#{s.seq} {title} · {s.duration_ms} ms
        {s.kind === "MODEL_CALL" ? ` · ${s.tokens_in}+${s.tokens_out} tokens · ${usd(s.cost_actual_usd ?? s.cost_estimated_usd, 6)}${s.cost_actual_usd === null ? " (est.)" : ""}` : ""}
        {s.error_code ? ` · ${s.error_code}` : ""}</summary>
      <pre className="hl-ai-pre">{JSON.stringify(d, null, 2)}</pre>
    </details>
  );
}

export function RunView({ runId, canDraft }: { runId: string; canDraft: boolean }) {
  const [run, setRun] = useState<Run | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [draft, setDraft] = useState<string | null>(null);
  const navigate = useNavigate();
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    let alive = true;
    const poll = async () => {
      try {
        const r = await getJson<Run>(`/api/v1/ai/runs/${runId}`);
        if (!alive) return;
        setRun(r);
        if (!FINAL.includes(r.status)) timer.current = setTimeout(() => void poll(), 2000);
      } catch (e) {
        if (alive) setError(asError(e));
      }
    };
    void poll();
    return () => { alive = false; if (timer.current) clearTimeout(timer.current); };
  }, [runId]);
  if (error) return <div className="hl-state-box" role="alert">{error.code} {error.detail} <code>{error.correlationId}</code></div>;
  if (!run) return <div className="hl-state-box">loading run…</div>;
  const config = (run.output_json as { config?: Record<string, unknown> } | null)?.config;
  const createDraft = async () => {
    try {
      const v = await sendJson<{ id: string }>("POST", "/api/v1/strategies/versions", { config });
      setDraft(`draft created`);
      navigate(`/strategies/${v.id}`);
    } catch (e) {
      const err = asError(e);
      setDraft(`${err.code}: ${err.detail}`);
    }
  };
  return (
    <div className="hl-section" data-testid="run">
      <h2>{run.service_id} — <span className={`hl-status ${run.status.toLowerCase()}`}>{run.status}</span></h2>
      <p className="hl-readonly-note">agent {run.agent_id} v{run.agent_version} · instructions <code>{run.instructions_sha256.slice(0, 12)}</code> ·
        model <code>{run.model}</code> · {run.model_calls} model calls, {run.tool_calls} tool calls ·
        {" "}{run.tokens_in}+{run.tokens_out} tokens · {usd(run.cost_usd, 6)}{run.cost_estimated_only ? " (estimated)" : ""} ·
        {" "}{run.duration_ms === null ? "running…" : `${(run.duration_ms / 1000).toFixed(1)} s`}</p>
      <p className="hl-readonly-note">input <code>{JSON.stringify(run.input)}</code></p>
      <div className="hl-ai-steps">{(run.steps ?? []).map((s) => <StepRow key={s.seq} s={s} />)}</div>
      {run.error_code && <div className="hl-banner" role="alert">{run.error_code}{run.error_detail ? ` — ${run.error_detail.slice(0, 300)}` : ""}</div>}
      {run.output_text && <div className="hl-card" data-testid="answer"><MarkdownText text={run.output_text} /></div>}
      {config && (
        <div className="hl-section">
          <pre className="hl-ai-pre" data-testid="draft-json">{JSON.stringify(config, null, 2)}</pre>
          <div className="hl-actions">
            {canDraft && <button type="button" className="hl-btn" onClick={() => void createDraft()}>Create draft</button>}
            <button type="button" className="hl-btn" onClick={() => void navigator.clipboard?.writeText(JSON.stringify(config, null, 2))}>Copy</button>
          </div>
          <p className="hl-readonly-note">Then study it before any paper launch:{" "}
            <code>python -m app.domain.trading.research.study --strategy {String(config.strategy_id)}@{String(config.version)}</code></p>
          {draft && <div role="status">{draft}</div>}
        </div>
      )}
      <div className="hl-actions">
        <button type="button" className="hl-btn" onClick={() => void navigator.clipboard?.writeText(JSON.stringify(run, null, 2))}>Copy log (JSON)</button>
      </div>
    </div>
  );
}

function Journal({ onOpen }: { onOpen: (id: string) => void }) {
  const [service, setService] = useState("");
  const [status, setStatus] = useState("");
  const [rows, setRows] = useState<Run[] | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  useEffect(() => {
    const q = new URLSearchParams({ ...(service ? { service_id: service } : {}), ...(status ? { status } : {}), limit: "100" });
    getJson<{ items: Run[] }>(`/api/v1/ai/runs?${q}`).then((r) => setRows(r.items)).catch((e) => setError(asError(e)));
  }, [service, status]);
  return (
    <div className="hl-section">
      <div className="hl-filters">
        <label className="hl-field">service<select value={service} onChange={(e) => setService(e.target.value)}>
          <option value="">all</option><option value="explain-trade">explain-trade</option>
          <option value="bot-review">bot-review</option><option value="strategy-draft">strategy-draft</option></select></label>
        <label className="hl-field">status<select value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">all</option>{["RUNNING", ...FINAL].map((s) => <option key={s} value={s}>{s}</option>)}</select></label>
      </div>
      {error && <div className="hl-banner" role="alert">{error.code}</div>}
      {rows && rows.length === 0 && <p>No run yet.</p>}
      {rows && rows.length > 0 && (
        <table className="hl-table" data-testid="journal">
          <thead><tr><th>started</th><th>service</th><th>model</th><th>status</th><th>steps</th><th>tokens</th><th>cost</th><th>duration</th></tr></thead>
          <tbody>{rows.map((r) => (
            <tr key={r.id} className="hl-clickable" onClick={() => onOpen(r.id)} data-testid={`run-${r.id}`}>
              <td>{time(r.started_at)}</td><td>{r.service_id}</td><td>{r.model}</td><td>{r.status}</td>
              <td>{r.model_calls + r.tool_calls}</td><td>{r.tokens_in + r.tokens_out}</td><td>{usd(r.cost_usd, 6)}</td>
              <td>{r.duration_ms === null ? "—" : `${(r.duration_ms / 1000).toFixed(1)} s`}</td></tr>))}</tbody>
        </table>
      )}
    </div>
  );
}

interface Settings { daily_budget_usd: string; max_budget_usd: string; models: Record<string, string>;
  prices: Record<string, { input: string; output: string }>; version: number; email_configured: boolean }

function SettingsPanel({ services, onSaved }: { services: Service[]; onSaved: () => void }) {
  const [s, setS] = useState<Settings | null>(null);
  const [models, setModels] = useState<{ id: string; selectable: boolean }[]>([]);
  const [msg, setMsg] = useState<string | null>(null);
  useEffect(() => {
    getJson<Settings>("/api/v1/ai/settings").then(setS).catch((e) => setMsg(asError(e).code));
    getJson<{ items: { id: string; selectable: boolean }[] }>("/api/v1/ai/models").then((r) => setModels(r.items)).catch(() => setModels([]));
  }, []);
  if (!s) return <div className="hl-state-box">{msg ?? "loading settings…"}</div>;
  const save = async () => {
    try {
      const out = await sendJson<Settings>("PATCH", "/api/v1/ai/settings",
        { daily_budget_usd: s.daily_budget_usd, models: s.models, prices: s.prices }, { "If-Match": `"${s.version}"` });
      setS(out);
      setMsg("saved");
      onSaved();
    } catch (e) {
      const err = asError(e);
      setMsg(`${err.code}: ${err.detail}`);
    }
  };
  const priced = Object.keys(s.prices);
  const choices = Array.from(new Set([...models.filter((m) => m.selectable).map((m) => m.id), ...priced])).filter((m) => priced.includes(m));
  return (
    <div className="hl-section" data-testid="settings">
      <label className="hl-field">daily cap (USD, 0 to {s.max_budget_usd})
        <input value={s.daily_budget_usd} onChange={(e) => setS({ ...s, daily_budget_usd: e.target.value })} /></label>
      {Number(s.daily_budget_usd) > Number(s.max_budget_usd) && <div className="hl-field-error">over the proxy budget: ask for it to be raised first</div>}
      <h2>Model per service</h2>
      {services.map((svc) => (
        <label key={svc.id} className="hl-field">{svc.name}
          <select value={s.models[svc.id] ?? ""} onChange={(e) => setS({ ...s, models: { ...s.models, [svc.id]: e.target.value } })}>
            {choices.map((m) => <option key={m} value={m}>{m}</option>)}</select></label>))}
      <h2>Price table (USD per million tokens)</h2>
      <table className="hl-grid"><thead><tr><th>model</th><th>input</th><th>output</th></tr></thead>
        <tbody>{priced.map((m) => (
          <tr key={m}><td>{m}</td>
            {(["input", "output"] as const).map((k) => (
              <td key={k}><input aria-label={`${m} ${k}`} value={s.prices[m][k]}
                onChange={(e) => setS({ ...s, prices: { ...s.prices, [m]: { ...s.prices[m], [k]: e.target.value } } })} /></td>))}
          </tr>))}</tbody></table>
      <p className="hl-readonly-note">Alert e-mail: {s.email_configured ? "configured" : "not configured (ALERT_EMAIL_TO)"}</p>
      <div className="hl-actions"><button type="button" className="hl-btn" onClick={() => void save()}>Save</button></div>
      {msg && <div role="status">{msg}</div>}
    </div>
  );
}

export default function AiPage() {
  const access = useTradingAccess();
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") ?? "services";
  const runId = params.get("run");
  const [budget, setBudget] = useState<Budget | null>(null);
  const [services, setServices] = useState<Service[]>([]);
  const [bots, setBots] = useState<{ id: string; name: string }[]>([]);
  const [error, setError] = useState<ApiError | null>(null);
  const roles = access.kind === "ready" ? access.roles : [];
  const isAdmin = roles.includes("ADMIN");
  const canRun = roles.includes("ANALYST");

  const loadBudget = useCallback(() => {
    getJson<Budget>("/api/v1/ai/budget").then(setBudget).catch((e) => setError(asError(e)));
  }, []);
  useEffect(() => {
    if (access.kind !== "ready") return;
    loadBudget();
    getJson<{ items: Service[] }>("/api/v1/ai/services").then((r) => setServices(r.items)).catch((e) => setError(asError(e)));
    getJson<{ items: { id: string; name: string }[] }>("/api/v1/bots").then((r) => setBots(r.items)).catch(() => setBots([]));
  }, [access.kind, loadBudget]);
  useEffect(() => {
    if (!runId) return;
    const t = setInterval(loadBudget, 5000);
    return () => clearInterval(t);
  }, [runId, loadBudget]);

  const disabled = useMemo(() => {
    if (!canRun) return "the ANALYST role is required to run a service";
    if (budget?.exhausted) return `daily budget exhausted until ${time(budget.reset_at)} UTC`;
    return null;
  }, [canRun, budget]);

  if (access.kind === "unauthenticated") return <Navigate to="/login" replace />;
  if (access.kind === "loading") return <div className="hl-state-box">loading…</div>;
  if (access.kind === "forbidden") return <div className="hl-state-box" role="alert">A trading role is required.</div>;
  if (access.kind === "error") return <div className="hl-state-box" role="alert">{access.error.code}</div>;

  const go = (next: Record<string, string>) => setParams(new URLSearchParams(next));
  const run = async (serviceId: string, input: Record<string, unknown>) => {
    try {
      const r = await sendJson<Run>("POST", "/api/v1/ai/runs", { service_id: serviceId, input },
        { "Idempotency-Key": crypto.randomUUID() });
      go({ tab: "services", run: r.id });
    } catch (e) {
      setError(asError(e));
    }
  };
  const ack = async (id: string) => {
    await sendJson("POST", `/api/v1/ai/alerts/${id}/ack`).catch((e) => setError(asError(e)));
    loadBudget();
  };
  const open = (budget?.alerts ?? []).filter((a) => !a.acked_at);
  return (
    <div className="hl-page">
      <h1 className="hl-title">AI services</h1>
      <div className="hl-section"><BudgetBar budget={budget} /></div>
      {isAdmin && open.map((a) => (
        <div key={a.id} className="hl-section hl-banner" role="alert" data-testid="ai-alert">
          AI budget {a.threshold === 100 ? "exhausted" : "over 80 %"} ({usd(a.spent_usd)}, source {a.source})
          {a.email_sent ? " — e-mail sent" : ` — e-mail not sent${a.email_error ? ` (${a.email_error})` : ""}`}
          {" "}<button type="button" className="hl-btn" onClick={() => void ack(a.id)}>acknowledge</button>
        </div>))}
      {error && <div className="hl-section hl-banner" role="alert">{error.code}: {error.detail} <code>{error.correlationId}</code></div>}
      <div className="hl-section hl-tabs" role="tablist">
        {[["services", "Services"], ["journal", "Journal"], ...(isAdmin ? [["settings", "Settings"]] : [])].map(([k, label]) => (
          <button key={k} type="button" role="tab" className="hl-btn" aria-selected={tab === k} onClick={() => go({ tab: k })}>{label}</button>))}
      </div>
      {runId ? (<>
        <div className="hl-section"><button type="button" className="hl-btn" onClick={() => go({ tab })}>← back</button></div>
        <RunView runId={runId} canDraft={canRun} />
      </>) : tab === "journal" ? <Journal onOpen={(id) => go({ tab: "journal", run: id })} />
        : tab === "settings" && isAdmin ? <SettingsPanel services={services} onSaved={loadBudget} />
          : (
            <div className="hl-section hl-cards hl-ai-services">
              {services.map((svc) => (
                <ServiceForm key={svc.id} service={svc} bots={bots} disabled={disabled}
                  initial={params.get("service") === svc.id ? params : new URLSearchParams()}
                  onRun={(input) => void run(svc.id, input)} />))}
            </div>)}
    </div>
  );
}
