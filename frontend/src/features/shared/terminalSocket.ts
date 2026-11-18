// WebSocket client for /ws/v1/terminal (spec 10-backend/04 « WebSocket », 03-ecrans/M1 « Client temps réel »).
// hello{token, bot_id, last_position} → snapshot or replay → events; 4401 ⇒ refresh token
// and reconnect from the last position; other closes ⇒ backoff 1, 2, 4… 30 s.
import type { ConnectionState, DomainEvent, Snapshot } from "./types";

export interface SocketHandlers {
  onSnapshot: (s: Snapshot) => void;
  onEvent: (e: DomainEvent) => void;
  onState: (s: ConnectionState) => void;
}

export interface SocketOptions {
  url: string;
  botId: string;
  getToken: () => string | null;
  refreshToken: () => Promise<string | null>;
  handlers: SocketHandlers;
  WebSocketImpl?: typeof WebSocket;
  schedule?: (fn: () => void, ms: number) => unknown;
  maxBackoffMs?: number;
}

export const CLOSE_UNAUTHENTICATED = 4401;
export const CLOSE_FORBIDDEN = 4403;
export const CLOSE_NOT_FOUND = 4404;

export class TerminalSocket {
  lastPosition: number | null = null;
  private ws: WebSocket | null = null;
  private stopped = false;
  private attempt = 0;

  constructor(private opts: SocketOptions) {}

  start(): void {
    this.stopped = false;
    this.connect();
  }

  stop(): void {
    this.stopped = true;
    this.ws?.close();
    this.ws = null;
  }

  private setState(s: ConnectionState): void {
    this.opts.handlers.onState(s);
  }

  private connect(): void {
    const WS = this.opts.WebSocketImpl ?? WebSocket;
    this.setState(this.attempt ? "RECONNECTING" : "CONNECTING");
    const ws = new WS(this.opts.url);
    this.ws = ws;
    ws.onopen = () => {
      ws.send(JSON.stringify({
        type: "hello", token: this.opts.getToken(), bot_id: this.opts.botId,
        last_position: this.lastPosition,
      }));
    };
    ws.onmessage = (ev: MessageEvent) => {
      let msg: { type?: string; data?: unknown; position?: number };
      try {
        msg = JSON.parse(String(ev.data));
      } catch {
        return;
      }
      if (msg.type === "hello") {
        this.attempt = 0;
        this.setState("LIVE");
      } else if (msg.type === "snapshot") {
        const snap = msg.data as Snapshot;
        this.lastPosition = snap.position;
        this.opts.handlers.onSnapshot(snap);
      } else if (msg.type === "event") {
        const e = msg.data as DomainEvent;
        if (this.lastPosition === null || e.position > this.lastPosition) {
          this.lastPosition = e.position;
          this.opts.handlers.onEvent(e);
        }
      }
    };
    ws.onclose = (ev: CloseEvent) => {
      if (this.ws !== ws || this.stopped) return;
      this.ws = null;
      if (ev.code === CLOSE_FORBIDDEN || ev.code === CLOSE_NOT_FOUND) {
        this.stopped = true;
        this.setState("OFFLINE");
        return;
      }
      this.setState("RECONNECTING");
      const schedule = this.opts.schedule ?? ((fn: () => void, ms: number) => setTimeout(fn, ms));
      if (ev.code === CLOSE_UNAUTHENTICATED) {
        void this.opts.refreshToken().then((tok) => {
          if (this.stopped) return;
          if (!tok) {
            this.stopped = true;
            this.setState("OFFLINE");
            return;
          }
          this.attempt += 1;
          this.connect();
        });
        return;
      }
      const delay = Math.min(1000 * 2 ** this.attempt, this.opts.maxBackoffMs ?? 30000);
      this.attempt += 1;
      schedule(() => {
        if (!this.stopped) this.connect();
      }, delay);
    };
  }
}
