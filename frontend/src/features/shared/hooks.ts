// React hooks for the 0-hl screens (spec 03-ecrans/M1 « Accès et états communs », « Client temps réel »).
import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, getJson, refreshAccessToken, terminalWsUrl } from "./client";
import { TerminalSocket } from "./terminalSocket";
import type { ConnectionState, DomainEvent, Snapshot } from "./types";

export type AccessState =
  | { kind: "loading" }
  | { kind: "ready"; roles: string[]; email: string | null }
  | { kind: "forbidden" }
  | { kind: "unauthenticated" }
  | { kind: "error"; error: ApiError };

export function useTradingAccess(): AccessState {
  const [state, setState] = useState<AccessState>({ kind: "loading" });
  useEffect(() => {
    if (!localStorage.getItem("access_token")) {
      setState({ kind: "unauthenticated" });
      return;
    }
    getJson<{ roles: string[]; email: string | null }>("/api/v1/me")
      .then((me) => setState({ kind: "ready", roles: me.roles, email: me.email }))
      .catch((e: unknown) => {
        if (e instanceof ApiError && e.status === 403) setState({ kind: "forbidden" });
        else if (e instanceof ApiError && e.status === 401) setState({ kind: "unauthenticated" });
        else if (e instanceof ApiError) setState({ kind: "error", error: e });
        else setState({ kind: "error", error: new ApiError(0, "NETWORK", String(e), null) });
      });
  }, []);
  return state;
}

/** Event types that change displayed state: they trigger a (debounced) snapshot reload. */
export const STATE_EVENT = /^(ORDER_|POSITION_|EQUITY_UPDATED|BOT_|COMMAND_|SCAN_CYCLE_COMPLETED|RECONCILIATION_|DATA_|PROVIDER_)/;
const MAX_EVENTS = 200;

export interface TerminalData {
  snapshot: Snapshot | null;
  events: DomainEvent[];
  connection: ConnectionState;
  error: ApiError | null;
  reload: () => void;
}

export function useTerminal(
  botId: string | null,
  socketFactory: (botId: string, handlers: ConstructorParameters<typeof TerminalSocket>[0]["handlers"]) => { start(): void; stop(): void } =
    (id, handlers) => new TerminalSocket({
      url: terminalWsUrl(), botId: id, handlers,
      getToken: () => localStorage.getItem("access_token"),
      refreshToken: refreshAccessToken,
    }),
  debounceMs = 1000,
): TerminalData {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [events, setEvents] = useState<DomainEvent[]>([]);
  const [connection, setConnection] = useState<ConnectionState>("CONNECTING");
  const [error, setError] = useState<ApiError | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const load = useCallback(() => {
    if (!botId) return;
    getJson<Snapshot>(`/api/v1/terminal/snapshot?bot_id=${botId}`)
      .then((s) => {
        setSnapshot(s);
        setError(null);
      })
      .catch((e: unknown) => setError(e instanceof ApiError ? e : new ApiError(0, "NETWORK", String(e), null)));
  }, [botId]);

  useEffect(() => {
    if (!botId) return;
    load();
    const socket = socketFactory(botId, {
      onSnapshot: (s) => {
        setSnapshot(s);
        setEvents(s.recent_events.slice(-MAX_EVENTS));
      },
      onEvent: (e) => {
        setEvents((prev) => [...prev, e].slice(-MAX_EVENTS));
        if (STATE_EVENT.test(e.event_type) && !timer.current) {
          timer.current = setTimeout(() => {
            timer.current = null;
            load();
          }, debounceMs);
        }
      },
      onState: setConnection,
    });
    socket.start();
    return () => {
      socket.stop();
      if (timer.current) clearTimeout(timer.current);
      timer.current = null;
    };
    // socketFactory/debounceMs are injection points for tests, stable by contract
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [botId, load]);

  return { snapshot, events, connection, error, reload: load };
}

export function prefersReducedMotion(): boolean {
  return typeof window !== "undefined" && typeof window.matchMedia === "function"
    && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}
