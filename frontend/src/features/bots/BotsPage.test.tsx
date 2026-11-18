// T-M1.5-06 — bots: actions by status, confirmation for STOP/E-STOP, nothing sent without it.
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import BotsPage, { ACTIONS_BY_STATUS } from "./BotsPage";

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

const bot = { id: "bot-1", name: "paper-kraken-01", environment: "PAPER", status: "RUNNING", row_version: 3,
  strategy_version_id: "sv", risk_policy_version_id: "pv" };

function setup(roles: string[]) {
  const fetchMock = vi.fn((input: RequestInfo | URL, _init?: RequestInit) => {
    const u = String(input);
    if (u.includes("/api/v1/me")) return Promise.resolve(json({ roles, email: "x@x.io" }));
    if (u.includes("/commands/cmd-1")) return Promise.resolve(json({ command_id: "cmd-1", status: "COMPLETED", rejection_code: null }));
    if (u.includes("/commands")) return Promise.resolve(json({ command_id: "cmd-1", status: "PENDING" }, 202));
    if (u.endsWith("/api/v1/bots")) return Promise.resolve(json({ items: [bot] }));
    if (u.includes("/api/v1/bots/bot-1")) return Promise.resolve(json({ ...bot, session: { uptime_seconds: 65 },
      strategy: { strategy_id: "volume-spike", version: "0.2.0" } }));
    return Promise.resolve(json({}, 404));
  });
  vi.stubGlobal("fetch", fetchMock);
  render(<MemoryRouter><BotsPage /></MemoryRouter>);
  return fetchMock;
}

const posts = (m: ReturnType<typeof vi.fn>) =>
  m.mock.calls.filter(([, init]) => (init as RequestInit | undefined)?.method === "POST");

describe("BotsPage", () => {
  beforeEach(() => {
    localStorage.clear();
    localStorage.setItem("access_token", "tok");
    vi.restoreAllMocks();
  });

  it("maps statuses to the allowed commands", () => {
    expect(ACTIONS_BY_STATUS.READY).toEqual(["START"]);
    expect(ACTIONS_BY_STATUS.PAUSED).toContain("RESUME_ENTRIES");
    expect(ACTIONS_BY_STATUS.STOPPED).toBeUndefined();
  });

  it("E-STOP asks for confirmation and sends nothing on cancel", async () => {
    const fetchMock = setup(["OPERATOR"]);
    const row = await screen.findByTestId("bot-paper-kraken-01");
    expect(within(row).getByText("volume-spike v0.2.0")).toBeInTheDocument();
    await userEvent.click(within(row).getByRole("button", { name: "E-STOP" }));
    const dialog = screen.getByRole("dialog", { name: "Confirm command" });
    expect(dialog).toHaveTextContent("immediate liquidation");
    await userEvent.click(within(dialog).getByRole("button", { name: "cancel" }));
    expect(posts(fetchMock)).toHaveLength(0);
  });

  it("confirmed E-STOP is sent and its outcome shown", async () => {
    const fetchMock = setup(["OPERATOR"]);
    await userEvent.click(within(await screen.findByTestId("bot-paper-kraken-01")).getByRole("button", { name: "E-STOP" }));
    await userEvent.click(screen.getByRole("button", { name: "confirm E-STOP" }));
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("EMERGENCY_STOP: COMPLETED"));
    expect(posts(fetchMock)).toHaveLength(1);
    expect(JSON.parse(String((posts(fetchMock)[0][1] as RequestInit).body)).command).toBe("EMERGENCY_STOP");
  });

  it("observer sees no action; risk officer only sees E-STOP", async () => {
    setup(["OBSERVER"]);
    const row = await screen.findByTestId("bot-paper-kraken-01");
    expect(within(row).queryAllByRole("button")).toHaveLength(0);
  });

  it("risk officer only sees E-STOP", async () => {
    setup(["RISK_OFFICER"]);
    const row = await screen.findByTestId("bot-paper-kraken-01");
    expect(within(row).getAllByRole("button").map((b) => b.textContent)).toEqual(["E-STOP"]);
  });
});
