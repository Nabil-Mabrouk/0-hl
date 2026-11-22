// UI-02 Bots & Sessions — minimal M1 (spec 03-ecrans/M1-terminal-et-bots « UI-02 »).
// Buttons are a convenience only: the backend enforces roles and transitions.
import { useCallback, useEffect, useState } from "react";
import { Navigate } from "react-router-dom";
import { ApiError, getJson, sendCommand, waitForCommand } from "../shared/client";
import BindDialog, { REBINDABLE } from "./BindDialog";
import CreateBotDialog from "./CreateBotDialog";
import { DASH, fmtUptime, fmtUsd } from "../shared/format";
import { useTradingAccess } from "../shared/hooks";
import type { Bot, Session } from "../shared/types";
import "../shared/ui/ui.css";

interface BotDetail extends Bot {
  session: Session | null;
  strategy: { strategy_id: string; version: string };
  risk_policy?: { policy_id: string; version: string };
  capital_usd?: string | null;
}

type Cmd = "START" | "PAUSE_ENTRIES" | "RESUME_ENTRIES" | "STOP" | "EMERGENCY_STOP";

export const ACTIONS_BY_STATUS: Record<string, Cmd[]> = {
  READY: ["START"],
  RUNNING: ["PAUSE_ENTRIES", "STOP", "EMERGENCY_STOP"],
  PAUSED: ["RESUME_ENTRIES", "STOP", "EMERGENCY_STOP"],
  DEGRADED: ["STOP", "EMERGENCY_STOP"],
};
const NEEDS_CONFIRM: Cmd[] = ["RESUME_ENTRIES", "STOP", "EMERGENCY_STOP"];   // resume: fix 2026-11-20
const EFFECT: Record<Cmd, string> = {
  START: "starts a new session; entries allowed",
  PAUSE_ENTRIES: "no new entries; open positions stay protected",
  RESUME_ENTRIES: "entries allowed again",
  STOP: "no new entries; open positions follow their exit rules, then the session closes",
  EMERGENCY_STOP: "no new entries AND immediate liquidation of every open position",
};

export default function BotsPage() {
  const access = useTradingAccess();
  const [bots, setBots] = useState<BotDetail[] | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [confirm, setConfirm] = useState<{ bot: BotDetail; cmd: Cmd } | null>(null);
  const [status, setStatus] = useState<Record<string, string>>({});
  const [binding, setBinding] = useState<BotDetail | null>(null);
  const [creating, setCreating] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const list = await getJson<{ items: (Bot & { capital_usd?: string | null })[] }>("/api/v1/bots");
      setBots(await Promise.all(list.items.map(async (b) => ({
        ...(await getJson<BotDetail>(`/api/v1/bots/${b.id}`)), capital_usd: b.capital_usd ?? null,
      }))));
    } catch (e) {
      setError(e instanceof ApiError ? e : new ApiError(0, "NETWORK", String(e), null));
    }
  }, []);

  useEffect(() => {
    if (access.kind === "ready") void load();
  }, [access.kind, load]);

  if (access.kind === "unauthenticated") return <Navigate to="/login" replace />;
  if (access.kind === "loading") return <div className="hl-state-box">loading…</div>;
  if (access.kind === "forbidden") return <div className="hl-state-box" role="alert">A trading role is required.</div>;
  if (access.kind === "error") return <div className="hl-state-box" role="alert">{access.error.code}</div>;

  const roles = access.roles;
  const allowed = (cmd: Cmd) => roles.includes("OPERATOR") || (cmd === "EMERGENCY_STOP" && roles.includes("RISK_OFFICER"));
  const canBind = roles.includes("OPERATOR") && roles.includes("RISK_OFFICER");

  async function run(bot: BotDetail, cmd: Cmd) {
    setConfirm(null);
    setStatus((s) => ({ ...s, [bot.id]: `${cmd}: PENDING` }));
    try {
      const sent = await sendCommand(bot.id, cmd, "bots page");
      const done = await waitForCommand(sent.command_id);
      setStatus((s) => ({ ...s, [bot.id]: `${cmd}: ${done.status}${done.rejection_code ? ` (${done.rejection_code})` : ""}` }));
      await load();
    } catch (e) {
      const err = e instanceof ApiError ? e : new ApiError(0, "NETWORK", String(e), null);
      setStatus((s) => ({ ...s, [bot.id]: `${cmd}: ${err.code}` }));
    }
  }

  return (
    <div className="hl-page">
      <div style={{ maxWidth: 1000, margin: "0 auto 12px", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <h1 style={{ fontSize: 20, margin: 0 }}>Bots &amp; Sessions</h1>
        {canBind && <button type="button" className="hl-btn" onClick={() => setCreating(true)}>New bot</button>}
      </div>
      {notice && <div className="hl-banner provenance" role="status">{notice}</div>}
      {error && <div className="hl-banner error" role="alert">{error.code} — {error.detail}</div>}
      {!bots && !error && <div className="hl-state-box">loading bots…</div>}
      {bots && bots.length === 0 && <div className="hl-state-box">No bot yet.</div>}
      {bots && bots.length > 0 && (
        <table className="hl-table">
          <thead>
            <tr><th>bot</th><th>env</th><th>status</th><th>strategy</th><th>policy</th><th>capital</th><th>uptime</th><th>actions</th></tr>
          </thead>
          <tbody>
            {bots.map((b) => (
              <tr key={b.id} data-testid={`bot-${b.name}`}>
                <td><a href={`/terminal?bot=${b.id}`}>{b.name}</a></td>
                <td>{b.environment}</td>
                <td>{b.status}</td>
                <td>{b.strategy ? `${b.strategy.strategy_id} v${b.strategy.version}` : DASH}</td>
                <td>{b.risk_policy ? `${b.risk_policy.policy_id} v${b.risk_policy.version}` : DASH}</td>
                <td>{fmtUsd(b.capital_usd ?? null, 0)}</td>
                <td>{fmtUptime(b.session?.uptime_seconds)}</td>
                <td>
                  <div className="hl-actions">
                    {(ACTIONS_BY_STATUS[b.status] ?? []).filter(allowed).map((cmd) => (
                      <button
                        key={cmd}
                        type="button"
                        className={`hl-btn${cmd === "EMERGENCY_STOP" ? " danger" : ""}`}
                        onClick={() => (NEEDS_CONFIRM.includes(cmd) ? setConfirm({ bot: b, cmd }) : void run(b, cmd))}
                      >
                        {cmd === "EMERGENCY_STOP" ? "E-STOP" : cmd.replace("_ENTRIES", "").replace("_", " ")}
                      </button>
                    ))}
                    <a className="hl-btn" href={`/ai?service=bot-review&bot_id=${encodeURIComponent(b.id)}`}>AI review</a>
                    {canBind && b.environment === "PAPER" && (
                      <button type="button" className="hl-btn" disabled={!REBINDABLE.includes(b.status)}
                        title={REBINDABLE.includes(b.status) ? "attach published versions" : "stop the bot first (STOP, then wait for STOPPED)"}
                        onClick={() => setBinding(b)}>
                        Change strategy
                      </button>
                    )}
                  </div>
                  {status[b.id] && <div role="status">{status[b.id]}</div>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {creating && (
        <CreateBotDialog onClose={() => setCreating(false)}
          onDone={(name) => { setCreating(false); setNotice(`bot ${name} created — READY, press START to run it`); void load(); }} />
      )}
      {binding && (
        <BindDialog bot={binding} onClose={() => setBinding(null)}
          onDone={(msg) => { setBinding(null); setStatus((s) => ({ ...s, [binding.id]: msg })); void load(); }} />
      )}
      {confirm && (
        <div className="hl-dialog-backdrop">
          <div className="hl-dialog" role="dialog" aria-label="Confirm command">
            <h3>Confirm {confirm.cmd === "EMERGENCY_STOP" ? "EMERGENCY STOP" : confirm.cmd}</h3>
            <dl>
              <dt>bot</dt><dd>{confirm.bot.name}</dd>
              <dt>environment</dt><dd>{confirm.bot.environment}</dd>
              <dt>strategy</dt><dd>{confirm.bot.strategy ? `${confirm.bot.strategy.strategy_id} v${confirm.bot.strategy.version}` : DASH}</dd>
              <dt>effect</dt><dd>{EFFECT[confirm.cmd]}</dd>
            </dl>
            <div className="hl-actions">
              <button type="button" className="hl-btn" onClick={() => setConfirm(null)}>cancel</button>
              <button type="button" className="hl-btn danger" onClick={() => void run(confirm.bot, confirm.cmd)}>
                confirm {confirm.cmd === "EMERGENCY_STOP" ? "E-STOP" : confirm.cmd}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
