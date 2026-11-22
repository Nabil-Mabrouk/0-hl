// UI-01 Live Terminal — ADR-028 layout (spec 03-ecrans/M1-terminal-et-bots « Refonte ADR-028 »):
// header, Market radar with one card per bot (slot filled by the page), the step drawers of the
// selected bot, then TRADE / RESULT / tail / LOG.
import { useMemo, type ReactNode } from "react";
import { DASH, fmtPct, fmtUptime, fmtUsd } from "../shared/format";
import type { Stage } from "../shared/ui";
import { activityChip, agentCards, type AgentKey, logLines, resultBar, tradeView } from "./viewModel";
import type { ConnectionState, DomainEvent, Snapshot } from "../shared/types";
import "./terminal-wide.css";

export interface TerminalViewProps {
  snapshot: Snapshot;
  events: DomainEvent[];
  connection: ConnectionState;
  userLabel: string | null;
  canOperate: boolean;
  pausePending: boolean;
  onTogglePause: () => void;
  onOpenTrace: (correlationId: string) => void;
  reducedMotion: boolean;
  /** Market radar of all bots (built by the page); none ⇒ the section is omitted. */
  radar?: ReactNode;
  /** Agent drawer of the selected bot, opened from a pipeline step of its card. */
  drawer: AgentKey | null;
  onDrawer: (agent: AgentKey | null) => void;
}

/** Pipeline step of a bot card → agent drawer (LOGGER and YOU are reached through the log). */
export const STAGE_AGENT: Record<Stage, AgentKey> = {
  Scan: "SCANNER", Check: "RUG_CHECK", Size: "SIZER", Execute: "EXECUTOR", Equity: "EQUITY",
};

export default function TerminalWide(props: TerminalViewProps) {
  const { snapshot: s, events, connection } = props;
  const { drawer, onDrawer } = props;
  const lines = useMemo(() => logLines(events, s), [events, s]);
  const agents = useMemo(() => agentCards(s, events), [s, events]);
  const trade = useMemo(() => tradeView(s), [s]);
  const paused = s.bot.status === "PAUSED";
  const running = s.bot.status === "RUNNING";
  const motion = props.reducedMotion ? "" : " hw-motion";
  const bar = resultBar(s);
  const card = drawer ? agents.find((a) => a.key === drawer) : undefined;
  const logRows = lines.filter((l) => ["ENTER", "EXIT", "FILTERED", "REFUSED"].includes(l.kind)).slice(-12).reverse();
  const tail = lines.slice(-40);

  return (
    <div className={`hw-root${paused ? " hw-paused" : ""}${motion}`} data-testid="hl-canvas">
      <header className="hw-head">
        {/* explicit label (fix 2026-11-20: an icon-only toggle resumed entries by mistake);
            resuming is confirmed by the page */}
        <button
          className={`hw-pause${paused ? " playing hw-resume" : ""}`}
          type="button"
          aria-label={paused ? "Resume entries" : "Pause entries"}
          title={props.canOperate ? (paused ? "Resume entries (asks for confirmation)" : "Pause entries") : "Operator role required"}
          disabled={!props.canOperate || props.pausePending || !(paused || running)}
          onClick={props.onTogglePause}
        >
          <span className="hw-pause-symbol"><i /><i /></span>
          <span className="hw-pause-label">{paused ? "Resume entries" : "Pause entries"}</span>
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

      {props.radar && <div className="hw-radar">{props.radar}</div>}

      <div className="hw-grid">
        <section className="hw-center">
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
              <div key={l.position} className={l.kind === "ENTER" || l.kind === "EXIT" ? "g" : l.kind === "INFO" && l.tone === "bad" ? "b" : ""}>
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

      {drawer && card && (
        <div className="hw-drawer" role="dialog" aria-label={`${drawer} details`} data-testid="hl-drawer">
          <button type="button" aria-label="Close" onClick={() => onDrawer(null)}>×</button>
          <h3>{card.label} <small>{s.bot.name}</small></h3>
          <p className="hw-drawer-desc">{card.desc} · <b>{card.motto}</b></p>
          <p className="hw-drawer-detail">{card.detail}</p>
          <p>
            status {s.agents[drawer]?.status ?? DASH} · last {s.agents[drawer]?.last_event_type ?? DASH} at{" "}
            {s.agents[drawer]?.last_event_at ?? DASH}
          </p>
          {drawer === "SCANNER" && s.scan && (
            <p data-testid="hl-scan-fails">
              checked {s.scan.evaluated} · unknown {s.scan.unknown} · fails:{" "}
              {Object.entries(s.scan.fails).map(([k, v]) => `${k}=${v}`).join(" ") || DASH}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
