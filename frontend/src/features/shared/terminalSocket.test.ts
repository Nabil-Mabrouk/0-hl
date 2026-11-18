// T-M1.5-02 — WebSocket client protocol with a fake WebSocket.
import { describe, expect, it, vi } from "vitest";
import { TerminalSocket } from "./terminalSocket";
import type { ConnectionState } from "./types";
import { ev, snapshot } from "./testFixtures";

class FakeWS {
  static instances: FakeWS[] = [];
  sent: unknown[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((e: MessageEvent) => void) | null = null;
  onclose: ((e: CloseEvent) => void) | null = null;
  constructor(public url: string) {
    FakeWS.instances.push(this);
  }
  send(data: string) {
    this.sent.push(JSON.parse(data));
  }
  close() {}
  open() {
    this.onopen?.();
  }
  push(msg: unknown) {
    this.onmessage?.({ data: JSON.stringify(msg) } as MessageEvent);
  }
  drop(code: number) {
    this.onclose?.({ code } as CloseEvent);
  }
}

function setup(refresh = vi.fn().mockResolvedValue("tok-2")) {
  FakeWS.instances = [];
  const states: ConnectionState[] = [];
  const onSnapshot = vi.fn();
  const onEvent = vi.fn();
  const scheduled: (() => void)[] = [];
  let token = "tok-1";
  const sock = new TerminalSocket({
    url: "wss://api.0-hl.com/ws/v1/terminal", botId: "bot-1",
    getToken: () => token, refreshToken: async () => {
      token = (await refresh()) ?? token;
      return token;
    },
    handlers: { onSnapshot, onEvent, onState: (s) => states.push(s) },
    WebSocketImpl: FakeWS as unknown as typeof WebSocket,
    schedule: (fn) => scheduled.push(fn),
  });
  return { sock, states, onSnapshot, onEvent, scheduled, refresh };
}

describe("TerminalSocket", () => {
  it("sends hello, applies snapshot then events in order", () => {
    const { sock, states, onSnapshot, onEvent } = setup();
    sock.start();
    const ws = FakeWS.instances[0];
    ws.open();
    expect(ws.sent[0]).toEqual({ type: "hello", token: "tok-1", bot_id: "bot-1", last_position: null });
    ws.push({ type: "hello", position: 151 });
    ws.push({ type: "snapshot", data: snapshot() });
    const e1 = ev("ORDER_FILLED", {}, { position: 152 });
    ws.push({ type: "event", data: e1 });
    ws.push({ type: "event", data: e1 }); // duplicate ignored
    expect(onSnapshot).toHaveBeenCalledTimes(1);
    expect(onEvent).toHaveBeenCalledTimes(1);
    expect(sock.lastPosition).toBe(152);
    expect(states).toEqual(["CONNECTING", "LIVE"]);
  });

  it("reconnects with backoff and resumes from the last position", () => {
    const { sock, states, scheduled } = setup();
    sock.start();
    const ws = FakeWS.instances[0];
    ws.open();
    ws.push({ type: "snapshot", data: snapshot({ position: 200 }) });
    ws.drop(1006);
    expect(states.at(-1)).toBe("RECONNECTING");
    expect(scheduled).toHaveLength(1);
    scheduled[0]();
    const ws2 = FakeWS.instances[1];
    ws2.open();
    expect((ws2.sent[0] as { last_position: number }).last_position).toBe(200);
  });

  it("refreshes the token on 4401 then reconnects", async () => {
    const { sock, refresh } = setup();
    sock.start();
    FakeWS.instances[0].open();
    FakeWS.instances[0].drop(4401);
    await vi.waitFor(() => expect(FakeWS.instances).toHaveLength(2));
    expect(refresh).toHaveBeenCalledOnce();
    FakeWS.instances[1].open();
    expect((FakeWS.instances[1].sent[0] as { token: string }).token).toBe("tok-2");
  });

  it("goes OFFLINE on 4403 without retry", () => {
    const { sock, states, scheduled } = setup();
    sock.start();
    FakeWS.instances[0].drop(4403);
    expect(states.at(-1)).toBe("OFFLINE");
    expect(scheduled).toHaveLength(0);
  });
});
