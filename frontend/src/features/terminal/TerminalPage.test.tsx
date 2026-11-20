// T-M1.5-04 / 05 — page states (forbidden, error with correlation id, stale banner) and pause command.
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { snapshot } from "../shared/testFixtures";

vi.mock("../shared/terminalSocket", () => ({
  TerminalSocket: class {
    constructor(private o: { handlers: { onState: (s: string) => void } }) {}
    start() {
      this.o.handlers.onState("LIVE");
    }
    stop() {}
  },
}));

import TerminalPage from "./TerminalPage";

function json(body: unknown, status = 200, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json", ...headers } });
}

function route(map: Record<string, () => Response>) {
  return vi.fn((input: RequestInfo | URL, _init?: RequestInit) => {
    const url = String(input);
    const key = Object.keys(map).find((k) => url.includes(k));
    return Promise.resolve(key ? map[key]() : json({}, 404));
  });
}

function renderPage() {
  render(<MemoryRouter initialEntries={["/terminal"]}><TerminalPage /></MemoryRouter>);
}

describe("TerminalPage", () => {
  beforeEach(() => {
    localStorage.clear();
    localStorage.setItem("access_token", "tok");
    vi.restoreAllMocks();
  });

  it("shows the forbidden state without a trading role", async () => {
    vi.stubGlobal("fetch", route({ "/api/v1/me": () => json({ code: "PERMISSION_DENIED" }, 403) }));
    renderPage();
    expect(await screen.findByText(/trading role is required/)).toBeInTheDocument();
  });

  it("shows an API error with its correlation id", async () => {
    vi.stubGlobal("fetch", route({
      "/api/v1/me": () => json({ roles: ["OBSERVER"], email: "o@x.io" }),
      "/api/v1/bots": () => json({ code: "INTERNAL", detail: "boom", correlation_id: "corr-42" }, 500),
    }));
    renderPage();
    expect(await screen.findByText(/corr-42/)).toBeInTheDocument();
  });

  it("renders the terminal and a stale banner when the worker is down", async () => {
    const s = snapshot({ feed: { connection: "STALE", worker_alive: false, pairs_live: 0, pairs_subscribed: 35, updated_at: null } });
    vi.stubGlobal("fetch", route({
      "/api/v1/me": () => json({ roles: ["OBSERVER"], email: "o@x.io" }),
      "/api/v1/bots": () => json({ items: [s.bot] }),
      "/api/v1/terminal/snapshot": () => json(s),
    }));
    renderPage();
    expect(await screen.findByTestId("hl-canvas")).toBeInTheDocument();
    expect(screen.getByTestId("hl-stale")).toHaveTextContent("worker stopped");
    expect(screen.getByTestId("hl-stale")).toHaveTextContent("35 pairs stale");
  });

  it("pause sends PAUSE_ENTRIES with an Idempotency-Key and waits for the worker", async () => {
    const s = snapshot();
    const fetchMock = route({
      "/api/v1/me": () => json({ roles: ["OPERATOR", "OBSERVER"], email: "op@x.io" }),
      "/api/v1/bots/bot-1/commands": () => json({ command_id: "cmd-1", status: "PENDING" }, 202),
      "/api/v1/commands/cmd-1": () => json({ command_id: "cmd-1", status: "COMPLETED", rejection_code: null }),
      "/api/v1/bots": () => json({ items: [s.bot] }),
      "/api/v1/terminal/snapshot": () => json(s),
    });
    vi.stubGlobal("fetch", fetchMock);
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: "Pause entries" }));
    await waitFor(() => expect(fetchMock.mock.calls.some(([u]) => String(u).includes("/api/v1/commands/cmd-1"))).toBe(true));
    const post = fetchMock.mock.calls.find(([u]) => String(u).includes("/commands") && !String(u).includes("cmd-1"));
    const init = post![1] as RequestInit;
    expect(JSON.parse(String(init.body))).toEqual({ command: "PAUSE_ENTRIES", reason: "terminal button" });
    expect(new Headers(init.headers).get("Idempotency-Key")).toBeTruthy();
  });

  it("renders the single full-width terminal, without any layout switch (ADR-019)", async () => {
    const s = snapshot();
    vi.stubGlobal("fetch", route({
      "/api/v1/me": () => json({ roles: ["OBSERVER"], email: "o@x.io" }),
      "/api/v1/bots": () => json({ items: [s.bot] }),
      "/api/v1/terminal/snapshot": () => json(s),
    }));
    renderPage();
    expect(await screen.findByTestId("hl-canvas")).toBeInTheDocument();
    expect(screen.queryByRole("group", { name: "Terminal layout" })).toBeNull();
    expect(screen.queryByText(/Reference canvas/)).toBeNull();
  });

  it("shows the PAPER provenance and the entry-blocked banners (review 2026-11-19)", async () => {
    const s = snapshot({ entry_blocks: [
      { code: "MAX_DAILY_LOSS", value: "0.034", limit: "0.03", until: "2026-11-20T00:00:00Z" },
      { code: "BOT_NOT_RUNNING", value: "PAUSED", limit: "RUNNING", until: null },
    ] });
    vi.stubGlobal("fetch", route({
      "/api/v1/me": () => json({ roles: ["OBSERVER"], email: "o@x.io" }),
      "/api/v1/bots": () => json({ items: [s.bot] }),
      "/api/v1/terminal/snapshot": () => json(s),
    }));
    renderPage();
    expect(await screen.findByTestId("hl-provenance")).toHaveTextContent(
      "PAPER — real Kraken prices · simulated execution · fictitious capital $10,000.00");
    expect(screen.getByTestId("hl-blocked")).toHaveTextContent(
      "Entries blocked: daily loss 3.4% ≥ 3.0% until 00:00 UTC · bot paused");
  });
});
