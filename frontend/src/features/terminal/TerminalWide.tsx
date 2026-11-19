// UI-01 default full-width layout (ADR-016, spec 03-ecrans/M1-terminal-et-bots « Vue par défaut »).
// Same data and view model as the 550×688 reference canvas, on a standard responsive grid.
import { useMemo, useState } from "react";
import { DASH, fmtPct, fmtUptime, fmtUsd, ratio } from "../shared/format";
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
import type { TerminalCanvasProps } from "./TerminalCanvas";
import "./terminal-wide.css";

const AGENT_CLASS: Record<AgentKey, string> = {
  SCANNER: "scanner", RUG_CHECK: "rug", SIZER: "sizer", EXECUTOR: "executor",
  LOGGER: "logger", EQUITY: "equity",
};
const AGENT_ICON: Record<AgentKey, string> = {
  SCANNER: "◫", RUG_CHECK: "⚠", SIZER: "◈", EXECUTOR: "◧", LOGGER: "⬡", EQUITY: "♟",
};

export default function TerminalWide(props: TerminalCanvasProps) {
  const { snapshot: s, events, connection } = props;
  const [drawer, setDrawer] = useState<AgentKey | null>(null);
  const lines = useMemo(() => logLines(events, s), [events, s]);
  const agents = useMemo(() => agentCards(s, events), [s, events]);
  const trade = useMemo(() => tradeView(s), [s]);
  const cands = useMemo(() => candidateCards(events), [events]);
  const paused = s.bot.status === "PAUSED";
  const running = s.bot.status === "RUNNING";
  const motion = props.reducedMotion ? "" : " hw-motion";
  const lastEvent = events[events.length - 1];
  const filtered = noSignal(s);
  const bar = resultBar(s);
  const lastClosed = s.recent_trades[0];
  const lastOpen = s.open_positions[s.open_positions.length - 1];
  const sizeRatio = lastOpen ? ratio(entryNotional(lastOpen), s.owner_capital) : null;
  const logRows = lines.filter((l) => ["ENTER", "EXIT", "FILTERED", "REFUSED"].includes(l.kind)).slice(-12).reverse();
  const tail = lines.slice(-40);
  const last = tail[tail.length - 1];

  return (
    <div className={`hw-root${paused ? " hw-paused" : ""}${motion}`} data-testid="hl-canvas" data-layout="wide">
      <header className="hw-head">
        <button
          className={`hw-pause${paused ? " playing" : ""}`}
          type="button"
          aria-label={paused ? "Resume entries" : "Pause entries"}
          title={props.canOperate ? (paused ? "Resume entries" : "Pause entries") : "Operator role required"}
          disabled={!props.canOperate || props.pausePending || !(paused || running)}
          onClick={props.onTogglePause}
        >
          <span className="hw-pause-symbol"><i /><i /></span>
        </button>
        <div className="hw-ident">
          <div className="hw-title">
            <span className="hw-brand">0-hl</span> Terminal
            <span className="hw-version">v{s.config.strategy_version ?? DASH}-{s.bot.environment.toLowerCase()}</span>
          </div>
          <div className="hw-status">
            <span className="hw-dotrun" />
            {props.pausePending ? "pending…" : s.bot.status.toLowerCase()} · {fmtUptime(s.session?.uptime_seconds)}
            <span className="hw-chip" data-testid="hl-activity">{activityChip(s, lines)}</span>
          </div>
          <div className="hw-tagline">
            {s.bot.name} · every trade gets a reason
            {props.userLabel && <span className="hw-handle"> · @{props.userLabel}</span>}
          </div>
        </div>
        <div className="hw-stats">
          <div className="hw-stat hw-stat-balance">
            <b data-testid="hl-balance">{fmtUsd(s.equity.equity)}</b>
            <span>balance{s.equity.quality === "STALE" ? " · stale" : ""}</span>
          </div>
          <div className="hw-stat"><b data-testid="hl-trades">{s.stats.trades}</b><span>trades</span></div>
          <div className="hw-stat hw-good"><b data-testid="hl-wins">{s.stats.wins}</b><span>wins</span></div>
          <div className="hw-stat hw-bad"><b data-testid="hl-losses">{s.stats.losses}</b><span>losses</span></div>
          <span className={`hw-conn hw-conn-${connection.toLowerCase()}`} data-testid="hl-connection">
            {s.bot.environment.toLowerCase()} · {connection === "LIVE" ? "live" : connection.toLowerCase()}
          </span>
        </div>
      </header>

      <div className="hw-grid">
        <aside className="hw-agents">
          <div className="hw-label">AGENTS <span>{agents.filter((a) => a.status !== "DOWN").length} online</span></div>
          {agents.map((a) => (
            <button
              key={a.key}
              type="button"
              className={`hw-agent ${AGENT_CLASS[a.key]} hw-status-${a.status.toLowerCase()}`}
              onClick={() => setDrawer(a.key)}
              data-testid={`hl-agent-${a.key}`}
            >
              <span className="hw-ico">{AGENT_ICON[a.key]}</span>
              <span className="hw-agent-body">
                <span className="hw-agent-top"><b>{a.label}</b><em>{a.status}</em></span>
                <span className="hw-desc">{a.desc}</span>
                <span className="hw-motto">{a.motto}</span>
                <span className="hw-detail">{a.detail}</span>
              </span>
            </button>
          ))}
          <div className="hw-agent you">
            <span className="hw-ico">Ⅱ</span>
            <span className="hw-agent-body">
              <span className="hw-agent-top"><b>YOU</b><em>READING</em></span>
              <span className="hw-desc">reads the log</span>
            </span>
          </div>
        </aside>

        <section className="hw-center">
          <div className="hw-card hw-flow">
            <div className="hw-label">
              LIVE FLOW · SCAN → CHECK → SIZE → EXEC → LOG
              <span data-testid="hl-checked">checked {s.scan?.evaluated ?? DASH}</span>
            </div>
            <div className="hw-pipeline">
              <div className="hw-cands">
                {cands.length === 0 && <div className="hw-muted">no candidate yet</div>}
                {cands.map((c) => (
                  <div key={c.position} className={`hw-coin${motion ? " hw-arrive" : ""}`}>
                    <strong>{c.symbol}</strong><small>{c.spike}</small>
                  </div>
                ))}
              </div>
              <div className="hw-station rug"><span>!</span><b>RUG CHECK</b><small>{agents[1].detail}</small></div>
              <div className="hw-track" aria-hidden="true" />
              <div key={lastEvent?.event_type === "ORDER_FILLED" ? lastEvent.position : "exec"}
                className={`hw-station exec${motion && lastEvent?.event_type === "ORDER_FILLED" ? " hw-ring" : ""}`}>
                <span>✓</span><b>EXECUTOR</b><small>{agents[3].detail}</small>
              </div>
            </div>
            <div className="hw-minis">
              <div className="hw-mini filtered"><em>NO SIGNAL</em><strong>{filtered ?? DASH}</strong><small>+ {s.scan?.unknown ?? DASH} unknown</small></div>
              <div key={lastOpen?.id ?? "sizer"} className={`hw-mini sized${motion && lastOpen ? " hw-flash" : ""}`}>
                <em>SIZER</em><strong>{sizeRatio === null ? DASH : fmtPct(sizeRatio * 100, 0)} cap</strong>
                <small>{lastOpen ? fmtUsd(entryNotional(lastOpen)) : DASH}</small>
              </div>
              <div className="hw-mini logged"><em>LOGGER</em><strong>line #{s.position}</strong><small>each with a reason</small></div>
              <div className="hw-mini equity">
                <em>EQUITY</em>
                <strong className={lastClosed?.realized_pnl_net?.startsWith("-") ? "hw-neg" : ""}>
                  {lastClosed ? fmtUsd(lastClosed.realized_pnl_net) : DASH}
                </strong>
                <small>last close</small>
              </div>
            </div>
          </div>

          <div className="hw-pair">
            <div className="hw-card hw-trade" data-testid="hl-trade">
              <div className="hw-label">TRADE · ENTRY vs EXIT {trade && <span className="hw-pill">{trade.open ? "OPEN" : "CLOSED"}</span>}</div>
              {trade ? (
                <>
                  <div className="hw-trade-head">
                    <strong>{trade.symbol}</strong><span className="hw-long">LONG</span>
                    <span className={`hw-change ${trade.changeTone}`}>{trade.change}</span>
                  </div>
                  <dl className="hw-rows">
                    <dt>entry</dt><dd>{trade.entry}</dd>
                    <dt>exit</dt><dd>{trade.exit}</dd>
                    <dt>stop</dt><dd>{trade.stop}</dd>
                    <dt>target</dt><dd>{trade.target}</dd>
                    <dt>size</dt><dd>{trade.size}</dd>
                  </dl>
                  <div className="hw-reason">reason: {trade.reason}</div>
                </>
              ) : (
                <div className="hw-muted">no trade yet — the scanner is waiting for a qualifying window</div>
              )}
            </div>
            <div className="hw-card hw-result">
              <div className="hw-label">RESULT · start vs now <span>session {fmtUptime(s.session?.uptime_seconds)}</span></div>
              <div className="hw-result-row"><span>START</span><b>{fmtUsd(s.owner_capital)}</b></div>
              <div className="hw-result-row"><span>NOW</span><b data-testid="hl-now">{fmtUsd(s.equity.equity)}</b></div>
              <div className="hw-progress" data-testid="hl-result-bar">
                <span className={`hw-bar ${bar.tone}`} style={{ left: `${bar.left}%`, width: `${bar.width}%` }} />
                <i className="hw-bar-center" />
              </div>
              <div className="hw-result-pct">{bar.pct === null ? DASH : fmtPct(bar.pct, 2, true)} vs start</div>
              <div className="hw-result-foot">
                <span className="hw-good">{s.stats.wins} W / {s.stats.losses} L</span>
                <span>realized {fmtUsd(s.equity.realized_pnl_net)}</span>
              </div>
            </div>
          </div>
        </section>

        <section className="hw-card hw-tail" data-testid="hl-tail">
          <div className="hw-label hw-tail-title">$ tail -f night.log</div>
          <div className="hw-lines">
            {tail.length === 0 && <div className="hw-muted">{DASH}</div>}
            {tail.map((l) => (
              <div key={l.position} className={l.kind === "ENTER" || l.kind === "EXIT" ? "g" : l.kind === "INFO" ? "b" : ""}>
                <span>{l.time}</span> {l.kind} {l.symbol} {l.value || l.detail}
              </div>
            ))}
          </div>
        </section>

        <section className="hw-card hw-log" data-testid="hl-log">
          <div className="hw-label">LOG <span><s>would you long this?</s> → reading the log</span></div>
          {logRows.length === 0 && <div className="hw-muted">{DASH}</div>}
          {logRows.map((l) => (
            <button key={l.position} type="button" className="hw-logrow" onClick={() => props.onOpenTrace(l.correlationId)}>
              <span className="hw-time">{l.time}</span>
              <span className={`hw-event ${l.kind.toLowerCase()}`}>{l.kind}</span>
              <span className="hw-item"><b>{l.symbol}</b><small>{l.detail}</small></span>
              <span className={`hw-value ${l.tone}`}>{l.value}</span>
            </button>
          ))}
        </section>
      </div>

      <footer className="hw-ticker">
        <span>{last ? `${last.time} ${last.kind} ${last.symbol} ${last.detail}` : "waiting for the first event"}</span>
        <span>{s.feed.pairs_live}/{s.feed.pairs_subscribed} pairs live</span>
      </footer>

      {drawer && (
        <div className="hw-drawer" role="dialog" aria-label={`${drawer} details`}>
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
    </div>
  );
}
