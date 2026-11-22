// UI-01 page: access/states, bot selection, banners, pause command, trace panel
// (spec 03-ecrans/M1-terminal-et-bots).
import { useEffect, useState } from "react";
import { Navigate, useSearchParams } from "react-router-dom";
import { ApiError, getJson, sendCommand, waitForCommand } from "../shared/client";
import { fmtTime } from "../shared/format";
import { prefersReducedMotion, useTerminal, useTradingAccess } from "../shared/hooks";
import type { Bot, DomainEvent } from "../shared/types";
import TerminalWide from "./TerminalWide";
import { blockText } from "./viewModel";
import { fmtUsd } from "../shared/format";
import "../shared/ui/ui.css";

function ErrorBox({ error }: { error: ApiError }) {
  return (
    <div className="hl-state-box" role="alert">
      <strong>{error.code}</strong> — {error.detail}
      {error.correlationId && <div>correlation id: <code>{error.correlationId}</code></div>}
    </div>
  );
}

export default function TerminalPage() {
  const access = useTradingAccess();
  const [params, setParams] = useSearchParams();
  const [botId, setBotId] = useState<string | null>(params.get("bot"));
  const [bots, setBots] = useState<Bot[]>([]);
  const [botsError, setBotsError] = useState<ApiError | null>(null);
  const [noBot, setNoBot] = useState(false);
  const [pending, setPending] = useState(false);
  const [actionError, setActionError] = useState<ApiError | null>(null);
  const [trace, setTrace] = useState<DomainEvent[] | null>(null);
  const [confirmResume, setConfirmResume] = useState(false);
  const data = useTerminal(access.kind === "ready" ? botId : null);

  useEffect(() => {
    if (access.kind !== "ready") return;
    getJson<{ items: Bot[] }>("/api/v1/bots")
      .then((r) => {
        setBots(r.items);
        if (!r.items.length) setNoBot(true);
        else setBotId((current) => current ?? r.items[0].id);
      })
      .catch((e: unknown) => setBotsError(e instanceof ApiError ? e : new ApiError(0, "NETWORK", String(e), null)));
  }, [access.kind]);

  function chooseBot(id: string) {
    setBotId(id);
    setParams({ bot: id });                 // M2.4: the choice is kept in the URL
  }

  if (access.kind === "unauthenticated") return <Navigate to="/login" replace />;
  if (access.kind === "loading") return <div className="hl-state-box">loading…</div>;
  if (access.kind === "forbidden")
    return <div className="hl-state-box" role="alert">A trading role is required to open the terminal.</div>;
  if (access.kind === "error") return <ErrorBox error={access.error} />;
  if (botsError) return <ErrorBox error={botsError} />;
  if (noBot) return <div className="hl-state-box">No bot yet.</div>;
  if (data.error && !data.snapshot) return <ErrorBox error={data.error} />;
  if (!data.snapshot) return <div className="hl-state-box" data-testid="hl-loading">loading terminal…</div>;

  const s = data.snapshot;
  const canOperate = access.roles.includes("OPERATOR");
  const staleReasons = [
    !s.feed.worker_alive && "worker stopped",
    data.connection !== "LIVE" && `realtime ${data.connection.toLowerCase()}`,
    s.feed.pairs_live < s.feed.pairs_subscribed && `${s.feed.pairs_subscribed - s.feed.pairs_live} pairs stale`,
    s.equity.quality === "STALE" && "position marks stale",
  ].filter(Boolean) as string[];

  async function togglePause() {
    if (!botId) return;
    if (s.bot.status === "PAUSED" && !confirmResume) {
      setConfirmResume(true);             // resuming opens positions again: always confirmed
      return;
    }
    setConfirmResume(false);
    setPending(true);
    setActionError(null);
    try {
      const cmd = await sendCommand(botId, s.bot.status === "PAUSED" ? "RESUME_ENTRIES" : "PAUSE_ENTRIES", "terminal button");
      const done = await waitForCommand(cmd.command_id);
      if (done.status === "REJECTED")
        setActionError(new ApiError(409, done.rejection_code ?? "REJECTED", "command rejected", null));
      data.reload();
    } catch (e) {
      setActionError(e instanceof ApiError ? e : new ApiError(0, "NETWORK", String(e), null));
    } finally {
      setPending(false);
    }
  }

  async function openTrace(correlationId: string) {
    try {
      setTrace((await getJson<{ items: DomainEvent[] }>(`/api/v1/events/trace/${correlationId}`)).items);
    } catch (e) {
      setActionError(e instanceof ApiError ? e : new ApiError(0, "NETWORK", String(e), null));
    }
  }

  const viewProps = {
    snapshot: s,
    events: data.events,
    connection: data.connection,
    userLabel: access.email ? access.email.split("@")[0] : null,
    canOperate,
    pausePending: pending,
    onTogglePause: togglePause,
    onOpenTrace: openTrace,
    reducedMotion: prefersReducedMotion(),
  };

  const blocks = s.entry_blocks ?? [];

  return (
    <div className="hl-page">
      {bots.length > 1 && (
        <div className="hl-botbar" data-testid="hl-bot-picker">
          <label>bot{" "}
            <select aria-label="bot" value={botId ?? ""} onChange={(e) => chooseBot(e.target.value)}>
              {bots.map((b) => <option key={b.id} value={b.id}>{b.name} — {b.status}</option>)}
            </select>
          </label>
        </div>
      )}
      {s.bot.environment === "PAPER" && (
        <div className="hl-banner provenance" role="note" data-testid="hl-provenance">
          PAPER — real Kraken prices · simulated execution · fictitious capital {fmtUsd(s.owner_capital)}
        </div>
      )}
      {s.bot.status === "PAUSED" && (
        <div className="hl-banner blocked" role="status" data-testid="hl-paused">
          ENTRIES PAUSED since {fmtTime(s.bot.updated_at ?? null)} UTC — open positions stay protected
        </div>
      )}
      {confirmResume && (
        <div className="hl-dialog-backdrop">
          <div className="hl-dialog" role="dialog" aria-label="Confirm resume">
            <h3>Resume entries?</h3>
            <p>The bot <strong>{s.bot.name}</strong> will open new positions again, with strategy v{s.config.strategy_version ?? "—"}.</p>
            <div className="hl-actions">
              <button type="button" className="hl-btn" onClick={() => setConfirmResume(false)}>keep paused</button>
              <button type="button" className="hl-btn danger" onClick={() => void togglePause()}>confirm resume</button>
            </div>
          </div>
        </div>
      )}
      {blocks.length > 0 && (
        <div className="hl-banner blocked" role="status" data-testid="hl-blocked">
          Entries blocked: {blocks.map(blockText).join(" · ")}
        </div>
      )}
      {staleReasons.length > 0 && (
        <div className="hl-banner stale" role="status" data-testid="hl-stale">
          stale data: {staleReasons.join(" · ")}
        </div>
      )}
      {actionError && (
        <div className="hl-banner error" role="alert">
          {actionError.code} — {actionError.detail}
          {actionError.correlationId && ` (correlation id ${actionError.correlationId})`}
        </div>
      )}
      <TerminalWide {...viewProps} />
      {trace && (
        <div className="hl-dialog-backdrop" onClick={() => setTrace(null)}>
          <div className="hl-dialog" role="dialog" aria-label="Trade trace" onClick={(e) => e.stopPropagation()}>
            <h3>Trace</h3>
            <ol>
              {trace.map((e) => (
                <li key={e.position}>{fmtTime(e.occurred_at)} · {e.event_type}</li>
              ))}
            </ol>
            <button className="hl-btn" type="button" onClick={() => setTrace(null)}>close</button>
          </div>
        </div>
      )}
    </div>
  );
}
