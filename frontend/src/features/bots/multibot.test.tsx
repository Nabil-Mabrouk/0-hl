// T-M2.4-07 — « New bot » on Bots (spec 06-agents/04 « Écrans »); the Terminal picker is in terminal/botPicker.test.tsx.
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import BotsPage from "./BotsPage";

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}
const bot = { id: "bot-1", name: "paper-kraken-01", environment: "PAPER", status: "PAUSED", row_version: 3,
  strategy_version_id: "a", risk_policy_version_id: "p1", capital_usd: "10000" };
const sv = (id: string, version: string, policy = "paper-default") => ({ id, strategy_id: "volume-spike", version,
  status: "PUBLISHED", config_sha256: "s", config: { risk_policy_id: policy }, bots: [] });
const pv = (id: string, policyId: string, version: string) => ({ id, policy_id: policyId, version, status: "PUBLISHED", bots: [] });

function setup(roles: string[], onCreate?: (body: unknown) => Response) {
  const m = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const u = String(input);
    if (u.includes("/api/v1/me")) return Promise.resolve(json({ roles, email: "x@x.io" }));
    if (u.endsWith("/api/v1/bots") && init?.method === "POST") return Promise.resolve(onCreate!(JSON.parse(String(init.body))));
    if (u.endsWith("/api/v1/bots")) return Promise.resolve(json({ items: [bot] }));
    if (u.includes("/api/v1/bots/bot-1")) return Promise.resolve(json({ ...bot, session: null,
      strategy: { strategy_id: "volume-spike", version: "0.2.0" }, risk_policy: { policy_id: "paper-default", version: "1" } }));
    if (u.endsWith("/api/v1/strategies")) return Promise.resolve(json({ items: [sv("a", "0.2.0"), sv("b", "0.9.0", "other")] }));
    if (u.endsWith("/api/v1/risk-policies")) return Promise.resolve(json({ items: [pv("p1", "paper-default", "1"), pv("p9", "other", "1")] }));
    return Promise.resolve(json({}, 404));
  });
  vi.stubGlobal("fetch", m);
  render(<MemoryRouter><BotsPage /></MemoryRouter>);
  return m;
}

beforeEach(() => {
  localStorage.clear();
  localStorage.setItem("access_token", "tok");
  vi.restoreAllMocks();
});

describe("New bot", () => {
  it("filters the policy on the strategy, shows field errors, then creates", async () => {
    let attempt = 0;
    const m = setup(["OPERATOR", "RISK_OFFICER"], (body) => {
      attempt += 1;
      return attempt === 1
        ? json({ code: "VALIDATION_FAILED", detail: "invalid bot", extra: { errors: [{ path: "capital_usd", message: "entre 100 et 1000000 USD" }] } }, 422)
        : json({ ...bot, id: "bot-2", name: (body as { name: string }).name, status: "READY" }, 201);
    });
    const row = await screen.findByTestId("bot-paper-kraken-01");
    expect(row).toHaveTextContent("paper-default v1");
    expect(row).toHaveTextContent("$10,000");
    await userEvent.click(screen.getByRole("button", { name: "New bot" }));
    const dialog = await screen.findByRole("dialog", { name: "New bot" });
    const strategy = within(dialog).getByLabelText("strategy version");
    await waitFor(() => expect(within(strategy).getAllByRole("option")).toHaveLength(2));
    await userEvent.selectOptions(strategy, "a");
    const policy = within(dialog).getByLabelText("risk policy version");
    await waitFor(() => expect(within(policy).getAllByRole("option").map((o) => o.textContent)).toEqual(["paper-default v1"]));
    await userEvent.type(within(dialog).getByLabelText(/^name/), "paper-b");
    const capital = within(dialog).getByLabelText(/paper capital/);
    await userEvent.clear(capital);
    await userEvent.type(capital, "50");
    await userEvent.type(within(dialog).getByLabelText(/reason/), "comparaison");
    await userEvent.click(within(dialog).getByRole("button", { name: "create bot" }));
    expect(await within(dialog).findByText("entre 100 et 1000000 USD")).toBeInTheDocument();
    await userEvent.clear(capital);
    await userEvent.type(capital, "5000");
    await userEvent.click(within(dialog).getByRole("button", { name: "create bot" }));
    expect(await screen.findByText(/bot paper-b created — READY/)).toBeInTheDocument();
    const post = m.mock.calls.filter(([u, i]) => String(u).endsWith("/api/v1/bots") && (i as RequestInit)?.method === "POST").pop()!;
    expect(JSON.parse(String((post[1] as RequestInit).body))).toEqual({ name: "paper-b", strategy_version_id: "a",
      risk_policy_version_id: "p1", capital_usd: "5000", reason: "comparaison" });
  });

  it("is hidden without both roles", async () => {
    setup(["OPERATOR"]);
    await screen.findByTestId("bot-paper-kraken-01");
    expect(screen.queryByRole("button", { name: "New bot" })).toBeNull();
  });
});
