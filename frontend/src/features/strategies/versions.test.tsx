// T-M2.3-09 — Strategies, editor, Risk and « Change strategy » (spec 05-strategies/04 « Écrans »).
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import BotsPage from "../bots/BotsPage";
import RiskPage, { remaining } from "../risk/RiskPage";
import StrategiesPage, { nextVersion } from "./StrategiesPage";
import StrategyEditor from "./StrategyEditor";

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

const cond = (feature: string, unit: string, threshold = "4.0") => ({ feature, op: "gte", threshold, unit, window_seconds: 300 });
const config = (version: string) => ({
  strategy_id: "volume-spike", version, universe: { venues: ["kraken_spot"], top_n_by_volume: 40 },
  entry: { operator: "AND", conditions: [cond("volume_spike", "ratio"), cond("window_trade_count", "count", "40")] },
  exit: { initial_stop_pct: "3", take_profit_r: "2", max_hold_seconds: 3600 }, cooldown_seconds: 1800,
  risk_policy_id: "paper-default",
});
const sv = (id: string, version: string, status: string, bots: string[] = []) => ({
  id, strategy_id: "volume-spike", version, status, config_sha256: `${id}sha0123456789`, config: config(version),
  created_by: "user:1", created_at: "2026-11-20T10:00:00Z", published_by: null,
  published_at: status === "PUBLISHED" ? "2026-11-20T10:05:00Z" : null, bots,
});
const policy = (id: string, version: string, status: string, extra: Record<string, unknown> = {}) => ({
  id, policy_id: "paper-default", version, status, config_sha256: `${id}sha`,
  config: { policy_id: "paper-default", version, max_daily_loss_pct: "3", max_open_positions: 5, kill_switch: false },
  created_by: "user:1", created_at: "2026-11-20T10:00:00Z", published_by: null, published_at: null,
  pending_publication: null, bots: [], ...extra,
});
const features = { items: [
  { name: "volume_spike", unit: "ratio", description: "" },
  { name: "window_trade_count", unit: "count", description: "" },
  { name: "spread_bps", unit: "bps", description: "" },
] };

type Handler = (url: string, init?: RequestInit) => Response | undefined;
function mockFetch(roles: string[], handler: Handler) {
  const m = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const u = String(input);
    if (u.includes("/api/v1/me")) return Promise.resolve(json({ roles, email: "x@x.io" }));
    return Promise.resolve(handler(u, init) ?? json({ code: "NOT_FOUND" }, 404));
  });
  vi.stubGlobal("fetch", m);
  return m;
}
const calls = (m: ReturnType<typeof vi.fn>, method: string) =>
  m.mock.calls.filter(([, init]) => (init as RequestInit | undefined)?.method === method);

beforeEach(() => {
  localStorage.clear();
  localStorage.setItem("access_token", "tok");
  vi.restoreAllMocks();
});

describe("StrategiesPage (UI-04)", () => {
  it("lists versions, shows actions by role and compares two versions", async () => {
    const m = mockFetch(["ANALYST", "RISK_OFFICER"], (u) => {
      if (u.includes("/strategies/diff")) return json({ changes: [{ path: "cooldown_seconds", before: 1800, after: 3600 }] });
      if (u.endsWith("/api/v1/strategies")) return json({ items: [sv("a", "0.2.0", "PUBLISHED", ["paper-kraken-01"]), sv("b", "0.3.0", "DRAFT")] });
    });
    render(<MemoryRouter><StrategiesPage /></MemoryRouter>);
    const v2 = await screen.findByTestId("version-0.2.0");
    expect(within(v2).getByText("paper-kraken-01")).toBeInTheDocument();
    expect(within(v2).getByRole("button", { name: "Deprecate" })).toBeInTheDocument();
    expect(within(screen.getByTestId("version-0.3.0")).getByRole("button", { name: "Publish" })).toBeInTheDocument();
    await userEvent.click(screen.getByLabelText("compare v0.2.0"));
    await userEvent.click(screen.getByLabelText("compare v0.3.0"));
    await userEvent.click(screen.getByRole("button", { name: "Compare selected versions" }));
    expect(await screen.findByTestId("diff")).toHaveTextContent("cooldown_seconds18003600");
    expect(calls(m, "POST")).toHaveLength(0);
  });

  it("publishing needs a reason; an observer sees no action", async () => {
    const m = mockFetch(["RISK_OFFICER"], (u, init) => {
      if (u.endsWith("/publish") && init?.method === "POST") return json(sv("b", "0.3.0", "PUBLISHED"));
      if (u.endsWith("/api/v1/strategies")) return json({ items: [sv("b", "0.3.0", "DRAFT")] });
    });
    render(<MemoryRouter><StrategiesPage /></MemoryRouter>);
    await userEvent.click(await screen.findByRole("button", { name: "Publish" }));
    const dialog = screen.getByRole("dialog");
    const confirm = within(dialog).getByRole("button", { name: "confirm publish" });
    expect(confirm).toBeDisabled();
    await userEvent.type(within(dialog).getByLabelText(/reason/), "ok");
    await userEvent.click(confirm);
    await waitFor(() => expect(calls(m, "POST")).toHaveLength(1));
    expect(JSON.parse(String((calls(m, "POST")[0][1] as RequestInit).body))).toEqual({ reason: "ok" });
  });

  it("suggests the next free version", () => {
    expect(nextVersion("0.2.0", ["0.2.0", "0.3.0"])).toBe("0.4.0");
    expect(nextVersion("1", [])).toBe("1-2");
  });
});

function renderEditor(roles: string[], version: ReturnType<typeof sv>, handler: Handler = () => undefined) {
  const m = mockFetch(roles, (u, init) => handler(u, init) ?? (
    u.endsWith("/api/v1/strategies") ? json({ items: [version] })
      : u.endsWith("/api/v1/features") ? json(features)
        : u.endsWith("/api/v1/risk-policies") ? json({ items: [policy("p1", "1", "PUBLISHED")] })
          : undefined));
  render(
    <MemoryRouter initialEntries={[`/strategies/${version.id}`]}>
      <Routes><Route path="/strategies/:vid" element={<StrategyEditor validateDelayMs={0} />} /></Routes>
    </MemoryRouter>,
  );
  return m;
}

describe("StrategyEditor (UI-05)", () => {
  it("locks the unit to the feature and shows backend errors on the field", async () => {
    const m = renderEditor(["ANALYST"], sv("b", "0.3.0", "DRAFT"), (u) =>
      u.endsWith("/strategies/versions/validate")
        ? json({ valid: false, errors: [{ path: "exit.initial_stop_pct", message: "doit être > 0 et < 100" }] }) : undefined);
    const feature = await screen.findByLabelText("feature 1");
    await userEvent.selectOptions(feature, "spread_bps");
    expect(screen.getByTestId("unit-0")).toHaveTextContent("bps");
    const stop = screen.getByLabelText(/stop loss/);
    await userEvent.clear(stop);
    await userEvent.type(stop, "0");
    expect(await screen.findByText("doit être > 0 et < 100")).toBeInTheDocument();
    await waitFor(() => expect(calls(m, "POST").length).toBeGreaterThan(0));
  });

  it("saves the draft with If-Match and reports a concurrent change", async () => {
    const m = renderEditor(["ANALYST"], sv("b", "0.3.0", "DRAFT"), (u, init) => {
      if (u.endsWith("/strategies/versions/validate")) return json({ valid: true, errors: [] });
      if (init?.method === "PUT") return json({ code: "PRECONDITION_FAILED", detail: "x" }, 412);
    });
    const threshold = await screen.findByLabelText("threshold 1");
    await userEvent.clear(threshold);
    await userEvent.type(threshold, "6");
    await userEvent.click(screen.getByRole("button", { name: "Save draft" }));
    expect(await screen.findByText(/changed elsewhere/)).toBeInTheDocument();
    const put = calls(m, "PUT")[0][1] as RequestInit;
    expect(new Headers(put.headers).get("If-Match")).toBe('"bsha0123456789"');
  });

  it("adds an exit condition, removes it, and the exit has no empty block (ADR-024)", async () => {
    const m = renderEditor(["ANALYST"], sv("b", "0.3.0", "DRAFT"), (u) =>
      u.endsWith("/strategies/versions/validate") ? json({ valid: true, errors: [] }) : undefined);
    expect(await screen.findByRole("button", { name: "+ exit condition" })).toBeInTheDocument();
    expect(screen.queryByTestId("exit-conditions")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "+ exit condition" }));
    await userEvent.selectOptions(screen.getByLabelText("exit feature 1"), "spread_bps");
    expect(screen.getByTestId("exit-unit-0")).toHaveTextContent("bps");
    await waitFor(() => expect(calls(m, "POST").length).toBeGreaterThan(0));
    const sent = JSON.parse(String((calls(m, "POST").at(-1)![1] as RequestInit).body));
    expect(sent.config.exit.conditions).toEqual({ operator: "OR",
      conditions: [{ feature: "spread_bps", op: "gte", threshold: "0", unit: "bps", window_seconds: 300 }] });
    expect(sent.config.entry.conditions).toHaveLength(2);
    await userEvent.click(screen.getByRole("button", { name: "remove exit condition 1" }));
    await userEvent.click(screen.getByRole("button", { name: "+ condition" }));
    await waitFor(() => {
      const last = JSON.parse(String((calls(m, "POST").at(-1)![1] as RequestInit).body));
      expect(last.config.exit.conditions).toBeUndefined();
      expect(last.config.entry.conditions).toHaveLength(3);
    });
  });

  it("opens a published version read only", async () => {
    renderEditor(["ANALYST"], sv("a", "0.2.0", "PUBLISHED"));
    expect(await screen.findByTestId("readonly")).toHaveTextContent("never changes");
    expect(screen.getByLabelText("threshold 1")).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Save draft" })).toBeNull();
    expect(screen.getByRole("button", { name: "Duplicate" })).toBeInTheDocument();
  });
});

describe("RiskPage (UI-06)", () => {
  it("flags loosened limits and counts down to the confirmation", async () => {
    const t0 = Date.parse("2026-11-20T12:00:00Z");
    const pending = { requested_at: "2026-11-20T12:00:00Z", requested_by: "user:1", reason: "x",
      confirm_after: "2026-11-20T12:10:00Z" };
    const draft = policy("p2", "2", "DRAFT", {
      config: { policy_id: "paper-default", version: "2", max_daily_loss_pct: "5", max_open_positions: 3, kill_switch: false },
      compared_to: { id: "p1", version: "1" }, pending_publication: pending,
      changes: [{ path: "max_daily_loss_pct", before: "3", after: "5", effect: "plus souple" },
        { path: "max_open_positions", before: 5, after: 3, effect: "plus strict" }],
    });
    mockFetch(["RISK_OFFICER"], (u) => {
      if (u.endsWith("/api/v1/risk-policies")) return json({ items: [policy("p1", "1", "PUBLISHED"), draft] });
      if (u.includes("/versions/2")) return json(draft);
    });
    render(<MemoryRouter><RiskPage now={() => t0 + 4 * 60_000} /></MemoryRouter>);
    const row = await screen.findByTestId("limit-max_daily_loss_pct");
    expect(row.className).toContain("hl-effect-looser");
    expect(row).toHaveTextContent("looser");
    expect(screen.getByTestId("limit-max_open_positions")).toHaveTextContent("stricter");
    expect(screen.getByTestId("pending")).toHaveTextContent("Confirmation possible in 6:00");
    expect(screen.getByRole("button", { name: "Confirm publication" })).toBeDisabled();
    expect(remaining("2026-11-20T12:10:00Z", t0 + 11 * 60_000)).toBe(0);
  });

  it("is read only without the RISK_OFFICER role", async () => {
    mockFetch(["OBSERVER"], (u) => {
      if (u.endsWith("/api/v1/risk-policies")) return json({ items: [policy("p1", "1", "PUBLISHED")] });
      if (u.includes("/versions/1")) return json(policy("p1", "1", "PUBLISHED"));
    });
    render(<MemoryRouter><RiskPage /></MemoryRouter>);
    expect(await screen.findByLabelText("daily loss limit")).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Duplicate" })).toBeNull();
  });
});

describe("Bots « Change strategy »", () => {
  const bot = (status: string) => ({ id: "bot-1", name: "paper-kraken-01", environment: "PAPER", status, row_version: 7,
    strategy_version_id: "a", risk_policy_version_id: "p1" });

  function setup(status: string, roles = ["OPERATOR", "RISK_OFFICER"]) {
    return mockFetch(roles, (u, init) => {
      if (u.endsWith("/bind") && init?.method === "POST") return json({ ...bot("READY"), strategy_version_id: "c" });
      if (u.endsWith("/api/v1/bots")) return json({ items: [bot(status)] });
      if (u.includes("/api/v1/bots/bot-1")) return json({ ...bot(status), session: null, strategy: { strategy_id: "volume-spike", version: "0.2.0" } });
      if (u.includes("/strategies/diff")) return json({ changes: [{ path: "cooldown_seconds", before: 1800, after: 3600 }] });
      if (u.endsWith("/api/v1/strategies")) return json({ items: [sv("a", "0.2.0", "PUBLISHED"), sv("c", "0.3.0", "PUBLISHED"), sv("d", "0.4.0", "DRAFT")] });
      if (u.endsWith("/api/v1/risk-policies")) return json({ items: [policy("p1", "1", "PUBLISHED")] });
    });
  }

  it("is disabled while the bot runs", async () => {
    setup("RUNNING");
    render(<MemoryRouter><BotsPage /></MemoryRouter>);
    const b = await screen.findByRole("button", { name: "Change strategy" });
    expect(b).toBeDisabled();
    expect(b.getAttribute("title")).toMatch(/stop the bot first/);
  });

  it("binds a stopped bot to a published version with If-Match", async () => {
    const m = setup("STOPPED");
    render(<MemoryRouter><BotsPage /></MemoryRouter>);
    await userEvent.click(await screen.findByRole("button", { name: "Change strategy" }));
    const dialog = await screen.findByRole("dialog", { name: "Change strategy" });
    const select = await within(dialog).findByLabelText("strategy version");
    await waitFor(() => expect(within(select).getAllByRole("option")).toHaveLength(2));   // drafts excluded
    await userEvent.selectOptions(select, "c");
    expect(await within(dialog).findByTestId("bind-diff")).toHaveTextContent("cooldown_seconds");
    await userEvent.type(within(dialog).getByLabelText(/reason/), "v0.3.0");
    await act(async () => { await userEvent.click(within(dialog).getByRole("button", { name: "confirm change" })); });
    const post = calls(m, "POST").find(([u]) => String(u).endsWith("/bind"));
    expect(post).toBeTruthy();
    const init = post![1] as RequestInit;
    expect(new Headers(init.headers).get("If-Match")).toBe('"7"');
    expect(JSON.parse(String(init.body))).toEqual({ strategy_version_id: "c", risk_policy_version_id: "p1", reason: "v0.3.0" });
    expect(await screen.findByText(/bot READY, START to run it/)).toBeInTheDocument();
  });

  it("is hidden without both roles", async () => {
    setup("STOPPED", ["OPERATOR"]);
    render(<MemoryRouter><BotsPage /></MemoryRouter>);
    await screen.findByTestId("bot-paper-kraken-01");
    expect(screen.queryByRole("button", { name: "Change strategy" })).toBeNull();
  });
});
