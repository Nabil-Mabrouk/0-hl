// T-M2.4-07 — Terminal bot picker, kept in the URL (spec 06-agents/04 « Écrans »).
import { render, screen, waitFor } from "@testing-library/react";
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
