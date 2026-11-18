// UI-01 page: access/states, bot selection, scaling of the canonical canvas, pause command,
// trace panel (spec 03-ecrans/M1-terminal-et-bots).
import { useEffect, useLayoutEffect, useState } from "react";
import { Navigate, useSearchParams } from "react-router-dom";
import { ApiError, getJson, sendCommand, waitForCommand } from "../shared/client";
import { fmtTime } from "../shared/format";
import { prefersReducedMotion, useTerminal, useTradingAccess } from "../shared/hooks";
import type { Bot, DomainEvent } from "../shared/types";
import TerminalCanvas from "./TerminalCanvas";
import TerminalWide from "./TerminalWide";
import "./terminal.css";

function ErrorBox({ error }: { error: ApiError }) {
  return (
    <div className="hl-state-box" role="alert">
      <strong>{error.code}</strong> — {error.detail}
      {error.correlationId && <div>correlation id: <code>{error.correlationId}</code></div>}
    </div>
  );
}

export type Layout = "wide" | "reference";
const LAYOUT_KEY = "hl.terminal.layout";

/** Per-browser preference (ADR-016); storage may be unavailable: default to wide. */
function readLayout(): Layout {
  try {
    return localStorage.getItem(LAYOUT_KEY) === "reference" ? "reference" : "wide";
  } catch {
    return "wide";
  }
}

function saveLayout(l: Layout): void {
  try {
    localStorage.setItem(LAYOUT_KEY, l);
  } catch {
    /* preference not persisted */
  }
}

function useCanvasScale(): number {
  const [scale, setScale] = useState(1);
  useLayoutEffect(() => {
    const update = () =>
      setScale(Math.max(0.5, Math.min((window.innerWidth - 32) / 550, (window.innerHeight - 140) / 688, 1.6)));
    update();
    window.addEventListener("resize", update);
    return () => window.removeEventListener("resize", update);
  }, []);
  return scale;
}

export default function TerminalPage() {
  const access = useTradingAccess();
  const [params] = useSearchParams();
  const [botId, setBotId] = useState<string | null>(params.get("bot"));
  const [botsError, setBotsError] = useState<ApiError | null>(null);
  const [noBot, setNoBot] = useState(false);
  const [pending, setPending] = useState(false);
  const [actionError, setActionError] = useState<ApiError | null>(null);
  const [trace, setTrace] = useState<DomainEvent[] | null>(null);
  const [layout, setLayout] = useState<Layout>(readLayout);
  const scale = useCanvasScale();
  const data = useTerminal(access.kind === "ready" ? botId : null);

  useEffect(() => {
    if (access.kind !== "ready" || botId) return;
    getJson<{ items: Bot[] }>("/api/v1/bots")
      .then((r) => (r.items.length ? setBotId(r.items[0].id) : setNoBot(true)))
      .catch((e: unknown) => setBotsError(e instanceof ApiError ? e : new ApiError(0, "NETWORK", String(e), null)));
  }, [access.kind, botId]);

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

  const canvasProps = {
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

  return (
    <div className="hl-page">
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
      <div className="hl-layout-switch" role="group" aria-label="Terminal layout">
        {(["wide", "reference"] as const).map((l) => (
          <button
            key={l}
            type="button"
            className={`hl-btn${layout === l ? " active" : ""}`}
            aria-pressed={layout === l}
            onClick={() => {
              setLayout(l);
              saveLayout(l);
            }}
          >
            {l === "wide" ? "Full width" : "Reference canvas 550×688"}
          </button>
        ))}
      </div>
      {layout === "wide" ? (
        <TerminalWide {...canvasProps} />
      ) : (
        <div className="hl-stage" style={{ height: 688 * scale + 16 }}>
          <div className="hl-scale" style={{ transform: `scale(${scale})`, width: 550, height: 688 }}>
            <TerminalCanvas {...canvasProps} />
          </div>
        </div>
      )}
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
