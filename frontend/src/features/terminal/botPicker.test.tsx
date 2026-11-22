// T-M2.4-07 — Terminal bot picker, kept in the URL (spec 06-agents/04 « Écrans »).
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { beforeEach, expect, it, vi } from "vitest";
import { snapshot } from "../shared/testFixtures";

vi.mock("../shared/terminalSocket", () => ({
  TerminalSocket: class {
    constructor(private o: { handlers: { onState: (s: string) => void } }) {}
    start() { this.o.handlers.onState("LIVE"); }
    stop() {}
  },
}));

import TerminalPage from "./TerminalPage";

const json = (body: unknown) => new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
const bot = (id: string, name: string, status: string) => ({ id, name, status, environment: "PAPER", row_version: 1,
  strategy_version_id: "a", risk_policy_version_id: "p" });

beforeEach(() => {
  localStorage.clear();
  localStorage.setItem("access_token", "tok");
  vi.restoreAllMocks();
});

it("lists the bots and keeps the choice in the URL", async () => {
  const fetchMock = vi.fn((input: RequestInfo | URL) => {
    const u = String(input);
    if (u.includes("/api/v1/me")) return Promise.resolve(json({ roles: ["OBSERVER"], email: "x@x.io" }));
    if (u.includes("/api/v1/terminal/snapshot")) return Promise.resolve(json(snapshot()));
    if (u.endsWith("/api/v1/bots")) return Promise.resolve(json({ items: [bot("bot-1", "paper-kraken-01", "PAUSED"), bot("bot-2", "paper-b", "RUNNING")] }));
    return Promise.resolve(json({}));
  });
  vi.stubGlobal("fetch", fetchMock);
  function Where() { return <div data-testid="where">{useLocation().search}</div>; }
  render(<MemoryRouter initialEntries={["/terminal"]}><Routes><Route path="*" element={<><TerminalPage /><Where /></>} /></Routes></MemoryRouter>);
  const picker = await screen.findByLabelText("bot");
  expect(picker).toHaveValue("bot-1");
  await userEvent.selectOptions(picker, "bot-2");
  await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent("?bot=bot-2"));
  await waitFor(() => expect(fetchMock.mock.calls.some(([u]) => String(u).includes("snapshot?bot_id=bot-2"))).toBe(true));
});

// ADR-028 (D.3) — the Market radar of the Terminal: one card per bot; a pipeline step opens the
// agent drawer of that bot, switching the selected bot when needed.
it("shows the radar and opens the agent drawer of the bot whose step is clicked", async () => {
  const radar = {
    generated_at: new Date().toISOString(), window_minutes: 60, window_end: new Date().toISOString(), stale: false,
    reference: { symbol: "BTC/USD", return_pct: "1.00", volatility_pct: "0.08" }, pairs: [], pairs_without_data: 0,
    bots: [
      { name: "paper-kraken-01", status: "PAUSED", strategy: { id: "volume-spike", version: "0.2.0", name: "Volume Spike" },
        net_pct: "-8.65", last_cycle: null, open_positions: [] },
      { name: "paper-b", status: "RUNNING", strategy: { id: "rebound", version: "0.1.0", name: "Rebound after excess" },
        net_pct: "0.10", last_cycle: { window_end: null, evaluated: 40, signals: 0, outcomes: {} }, open_positions: [] },
    ],
  };
  const fetchMock = vi.fn((input: RequestInfo | URL) => {
    const u = String(input);
    if (u.includes("/api/v1/me")) return Promise.resolve(json({ roles: ["OBSERVER"], email: "x@x.io" }));
    if (u.includes("/api/v1/terminal/snapshot")) return Promise.resolve(json(snapshot()));
    if (u.includes("/api/v1/market/radar")) return Promise.resolve(json(radar));
    if (u.endsWith("/api/v1/bots")) return Promise.resolve(json({ items: [bot("bot-1", "paper-kraken-01", "PAUSED"), bot("bot-2", "paper-b", "RUNNING")] }));
    return Promise.resolve(json({}));
  });
  vi.stubGlobal("fetch", fetchMock);
  render(<MemoryRouter initialEntries={["/terminal"]}><TerminalPage /></MemoryRouter>);
  const card = await screen.findByTestId("botcard-paper-b");
  expect(card).toHaveTextContent("Rebound after excess");
  expect(fetchMock.mock.calls.some(([u]) => String(u).includes("/api/v1/market/radar?window=1h"))).toBe(true);
  await userEvent.click(within(card).getByRole("button", { name: /^Size/ }));
  await waitFor(() => expect(fetchMock.mock.calls.some(([u]) => String(u).includes("snapshot?bot_id=bot-2"))).toBe(true));
  expect(await screen.findByRole("dialog", { name: "SIZER details" })).toBeInTheDocument();
  await userEvent.keyboard("{Escape}");
  expect(screen.queryByRole("dialog", { name: "SIZER details" })).not.toBeInTheDocument();
  await userEvent.selectOptions(screen.getByLabelText("Window"), "24h");
  await waitFor(() => expect(fetchMock.mock.calls.some(([u]) => String(u).includes("/api/v1/market/radar?window=24h"))).toBe(true));
});
