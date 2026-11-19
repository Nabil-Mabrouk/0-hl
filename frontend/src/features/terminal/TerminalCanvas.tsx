// UI-01 canonical canvas 550×688 (spec 03-ecrans/M1-terminal-et-bots, geometry of capture 3 /
// original/prototype_v1.html). Renders only API data; unknown values show "—".
import { useMemo, useState } from "react";
import { DASH, fmtNum, fmtPct, fmtUptime, fmtUsd, ratio } from "../shared/format";
import type { ConnectionState, DomainEvent, Snapshot } from "../shared/types";
import {
  activityChip,
  agentCards,
  type AgentKey,
  candidateCards,
  entryNotional,
  logLines,
  noSignal,
  resultBar,
  tradeView,
} from "./viewModel";
import "./terminal.css";

export interface TerminalCanvasProps {
  snapshot: Snapshot;
  events: DomainEvent[];
  connection: ConnectionState;
  userLabel: string | null;
  canOperate: boolean;
  pausePending: boolean;
  onTogglePause: () => void;
  onOpenTrace: (correlationId: string) => void;
  reducedMotion: boolean;
}

const AGENT_CLASS: Record<AgentKey, string> = {
  SCANNER: "scanner", RUG_CHECK: "rug", SIZER: "sizer", EXECUTOR: "executor",
  LOGGER: "logger", EQUITY: "equity",
};
const AGENT_ICON: Record<AgentKey, string> = {
  SCANNER: "◫", RUG_CHECK: "⚠", SIZER: "◈", EXECUTOR: "◧", LOGGER: "⬡", EQUITY: "♟",
};

export default function TerminalCanvas(props: TerminalCanvasProps) {
  const { snapshot: s, events, connection } = props;
  const [drawer, setDrawer] = useState<AgentKey | null>(null);
  const lines = useMemo(() => logLines(events, s), [events, s]);
  const agents = useMemo(() => agentCards(s, events), [s, events]);
  const trade = useMemo(() => tradeView(s), [s]);
  const cands = useMemo(() => candidateCards(events), [events]);
  const paused = s.bot.status === "PAUSED";
  const running = s.bot.status === "RUNNING";
  const motion = props.reducedMotion ? "" : " hl-motion";
  const lastEvent = events[events.length - 1];
  const filtered = noSignal(s);
  const bar = resultBar(s);
  const lastClosed = s.recent_trades[0];
  const lastOpen = s.open_positions[s.open_positions.length - 1];
  const sizeRatio = lastOpen ? ratio(entryNotional(lastOpen), s.owner_capital) : null;
  const logRows = lines.filter((l) => ["ENTER", "EXIT", "FILTERED", "REFUSED"].includes(l.kind)).slice(-5).reverse();
  const tail = lines.slice(-6);

  return (
    <div className={`hl-shell${paused ? " hl-paused" : ""}${motion}`} data-testid="hl-canvas">
      <header className="hl-topbar">
        <div className="hl-dots"><i /><i /><i /></div>
        <div className="hl-top-center">0-hl — {s.bot.environment.toLowerCase()} · six agents · log first</div>
        <div className="hl-handle">{props.userLabel ? `@${props.userLabel}` : ""}</div>
        <span className={`hl-tag hl-conn-${connection.toLowerCase()}`} data-testid="hl-connection">
          {s.bot.environment.toLowerCase()} · {connection === "LIVE" ? "live" : connection.toLowerCase()}
        </span>
      </header>

      <section className="hl-hero">
        <button
          className={`hl-pause${paused ? " playing" : ""}`}
          type="button"
          aria-label={paused ? "Resume entries" : "Pause entries"}
          title={props.canOperate ? (paused ? "Resume entries" : "Pause entries") : "Operator role required"}
          disabled={!props.canOperate || props.pausePending || !(paused || running)}
          onClick={props.onTogglePause}
        >
          <span className="hl-pause-symbol"><i /><i /></span>
        </button>
        <div className="hl-hero-title"><span className="hl-brand">0-hl</span> Terminal</div>
        <span className="hl-version">v{s.config.strategy_version ?? DASH}-{s.bot.environment.toLowerCase()}</span>
        <div className="hl-running">
          <span className="hl-dotrun" />
          {props.pausePending ? "pending…" : s.bot.status.toLowerCase()} · {fmtUptime(s.session?.uptime_seconds)}
        </div>
        <span className="hl-statuschip" data-testid="hl-activity">{activityChip(s, lines)}</span>
        <div className="hl-tagline">every trade gets a reason</div>
        <div className="hl-stat hl-big" data-testid="hl-balance">{fmtUsd(s.equity.equity)}</div>
        <div className="hl-stat hl-blabel">balance{s.equity.quality === "STALE" ? " · stale" : ""}</div>
        <div className="hl-stat hl-trades" data-testid="hl-trades">{s.stats.trades}</div>
        <div className="hl-stat hl-tlabel">trades</div>
        <div className="hl-stat hl-wins" data-testid="hl-wins">{s.stats.wins}</div>
        <div className="hl-stat hl-wlabel">wins</div>
        <div className="hl-stat hl-losses" data-testid="hl-losses">{s.stats.losses}</div>
        <div className="hl-stat hl-llabel">losses</div>
      </section>

      <main className="hl-workspace">
        <div className="hl-sidebar-label">AGENTS</div>
        <div className="hl-online">{agents.filter((a) => a.status !== "DOWN").length} online</div>
        <div className="hl-flowlabel">LIVE FLOW · SCAN → CHECK → SIZE → EXEC → LOG</div>
        <div className="hl-checked" data-testid="hl-checked">checked {s.scan?.evaluated ?? DASH}</div>

        <aside className="hl-agent-sidebar">
          {agents.map((a) => (
            <button
              key={a.key}
              type="button"
              className={`hl-agent ${AGENT_CLASS[a.key]} hl-status-${a.status.toLowerCase()}`}
              onClick={() => setDrawer(a.key)}
              data-testid={`hl-agent-${a.key}`}
            >
              <span className="hl-ico"><span>{AGENT_ICON[a.key]}</span></span>
              <span className="hl-name">{a.label}</span>
              <span className="hl-desc">{a.desc}</span>
              <span className="hl-pill">{a.status}</span>
              <span className="hl-detail">{a.motto}</span>
              <span className="hl-subdetail">{a.detail}</span>
            </button>
          ))}
          <div className="hl-agent you">
            <span className="hl-ico"><span>Ⅱ</span></span>
            <span className="hl-name">YOU</span>
            <span className="hl-desc">reads the log</span>
            <span className="hl-pill">READING</span>
          </div>
        </aside>

        <section className="hl-flow">
          {cands.map((c, i) => (
            <div key={c.position} className={`hl-coin ${i === 0 ? "one" : "two"}${motion ? " hl-arrive" : ""}`}>
              <strong>{c.symbol}</strong>
              <small>{c.spike}</small>
            </div>
          ))}
          <div className="hl-station hl-rugstation"><span className="hl-sign">!</span></div>
          <div className="hl-station-title r">RUG CHECK</div>
          <div className="hl-station-sub r">{agents[1].detail}</div>
          <div key={lastEvent?.event_type === "ORDER_FILLED" ? lastEvent.position : "exec"}
            className={`hl-station hl-execstation${motion && lastEvent?.event_type === "ORDER_FILLED" ? " hl-ring" : ""}`}>
            <span className="hl-sign">✓</span>
          </div>
          <div className="hl-station-title e">EXECUTOR</div>
          <div className="hl-station-sub e">{agents[3].detail}</div>
          <div className="hl-flow-mini filtered">
            <div className="hl-heading">NO SIGNAL</div>
            <strong>{filtered ?? DASH}</strong>
            <small>+ {s.scan?.unknown ?? DASH} unknown</small>
          </div>
          <div key={lastOpen?.id ?? "sizer"} className={`hl-flow-mini sized${motion && lastOpen ? " hl-flash" : ""}`}>
            <div className="hl-heading">SIZER</div>
            <strong>{sizeRatio === null ? DASH : fmtPct(sizeRatio * 100, 0)} cap</strong>
            <small>{lastOpen ? fmtUsd(entryNotional(lastOpen)) : DASH}</small>
          </div>
          <div className="hl-flow-mini logged">
            <div className="hl-heading">LOGGER</div>
            <strong>line #{s.position}</strong>
            <small>each with a reason</small>
          </div>
          <div className="hl-flow-mini equity">
            <div className="hl-heading">EQUITY</div>
            <strong className={lastClosed?.realized_pnl_net?.startsWith("-") ? "hl-neg" : ""}>
              {lastClosed ? fmtUsd(lastClosed.realized_pnl_net) : DASH}
            </strong>
            <small>last close</small>
          </div>
        </section>

        <section className="hl-trade-section" data-testid="hl-trade">
          <div className="hl-header">TRADE · ENTRY vs EXIT</div>
          {trade ? (
            <>
              <span className="hl-open-pill">{trade.open ? "OPEN" : "CLOSED"}</span>
              <div className="hl-pair">{trade.symbol}</div>
              <span className="hl-longpill">LONG</span>
              <div className={`hl-change ${trade.changeTone}`}>{trade.change}</div>
              <div className="hl-traderows">
                <div className="hl-traderow"><span>entry</span><b>{trade.entry}</b></div>
                <div className="hl-traderow"><span>exit</span><b>{trade.exit}</b></div>
                <div className="hl-traderow"><span>stop</span><b>{trade.stop}</b></div>
                <div className="hl-traderow"><span>target</span><b>{trade.target}</b></div>
                <div className="hl-traderow"><span>size</span><b>{trade.size}</b></div>
              </div>
              <div className="hl-reason">reason: {trade.reason}</div>
            </>
          ) : (
            <div className="hl-empty">no trade yet — the scanner is waiting for a qualifying window</div>
          )}
        </section>

        <section className="hl-rightlog" data-testid="hl-tail">
          <div className="hl-title">$ tail -f night.log</div>
          <div className="hl-lines">
            {tail.length === 0 && <div>{DASH}</div>}
            {tail.map((l) => (
              <div key={l.position} className={l.kind === "ENTER" || l.kind === "EXIT" ? "g" : ""}>
                {l.time} {l.kind} {l.symbol} {l.value || l.detail}
              </div>
            ))}
          </div>
        </section>

        <section className="hl-result">
          <span className="hl-rtitle">RESULT · start vs now</span>
          <span className="hl-night">session {fmtUptime(s.session?.uptime_seconds)}</span>
          <span className="hl-start">START</span>
          <span className="hl-startdot" />
          <span className="hl-line" />
          <span className="hl-now">NOW</span>
          <span className="hl-progress" data-testid="hl-result-bar">
            <span className={`hl-bar ${bar.tone}`} style={{ left: `${bar.left}%`, width: `${bar.width}%` }} />
            <i className="hl-bar-center" />
          </span>
          <span className="hl-winloss">{s.stats.wins} W / {s.stats.losses} L</span>
          <span className="hl-bal1">{fmtUsd(s.owner_capital)}</span>
          <span className="hl-bal2" data-testid="hl-now">{fmtUsd(s.equity.equity)}</span>
          <span className="hl-wl">realized {fmtUsd(s.equity.realized_pnl_net)}</span>
        </section>

        <section className="hl-history" data-testid="hl-log">
          <span className="hl-hlabel">LOG</span>
          <span className="hl-reading"><span className="hl-strike">would you long this?</span> → reading the log</span>
          <div className="hl-logrows">
            {logRows.length === 0 && <div className="hl-logrow hl-empty-row">{DASH}</div>}
            {logRows.map((l) => (
              <button key={l.position} type="button" className="hl-logrow" onClick={() => props.onOpenTrace(l.correlationId)}>
                <span className="hl-time">{l.time}</span>
                <span className={`hl-event ${l.kind.toLowerCase()}`}>{l.kind}</span>
                <span className="hl-item"><b>{l.symbol}</b><small>{l.detail}</small></span>
                <span className={`hl-value ${l.tone === "bad" ? "negative" : l.tone === "muted" ? "muted" : ""}`}>{l.value}</span>
              </button>
            ))}
          </div>
        </section>

        {drawer && (
          <div className="hl-detailpop" role="dialog" aria-label={`${drawer} details`}>
            <button type="button" aria-label="Close" onClick={() => setDrawer(null)}>×</button>
            <h3>{agents.find((a) => a.key === drawer)?.label}</h3>
            <p>
              status {s.agents[drawer]?.status ?? DASH} · last {s.agents[drawer]?.last_event_type ?? DASH} at{" "}
              {s.agents[drawer]?.last_event_at ?? DASH}
            </p>
            {drawer === "SCANNER" && s.scan && (
              <p data-testid="hl-scan-fails">
                fails: {Object.entries(s.scan.fails).map(([k, v]) => `${k}=${v}`).join(" ") || DASH}
              </p>
            )}
          </div>
        )}
      </main>

      <footer className="hl-footer">
        <div className="hl-last">{tail.length ? `${tail[tail.length - 1].time} ${tail[tail.length - 1].kind} ${tail[tail.length - 1].symbol} ${tail[tail.length - 1].detail}` : "waiting for the first event"}</div>
        <div className="hl-state">{s.feed.pairs_live}/{s.feed.pairs_subscribed} pairs live · equity {fmtNum(s.equity.equity, 2)}</div>
      </footer>
    </div>
  );
}
