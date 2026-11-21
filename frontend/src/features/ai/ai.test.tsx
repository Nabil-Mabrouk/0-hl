// T-AI-12 — AI services screen (spec 03-ecrans/UI-16-services-ia « Écran »).
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import AiBudgetBanner from "./AiBudgetBanner";
import AiPage, { BudgetBar, MarkdownText, type Budget, type Run } from "./AiPage";

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

const budget = (over: Partial<Budget> = {}): Budget => ({
  daily_budget_usd: "10", spent_usd: "0.0012", spent_actual_usd: "0.0012", spent_estimated_usd: "0",
  spent_pct: "0.0", exhausted: false, reset_at: "2026-11-22T00:00:00+00:00",
  proxy: { spend: "0.0011", max_budget: "12.0" }, proxy_gap: false, alerts: [], email_configured: true, ...over,
});
const SERVICES = [
  { id: "explain-trade", name: "Expliquer un trade", description: "d1", model: "groq-gpt-oss-120b", output: "text",
    agent: { id: "trade-analyst", version: "1.0.0", tools: ["get_trade"] } },
  { id: "bot-review", name: "Bilan d'un bot", description: "d2", model: "groq-gpt-oss-120b", output: "text",
    agent: { id: "bot-reviewer", version: "1.0.0", tools: ["get_stats"] } },
  { id: "strategy-draft", name: "Brouillon de stratégie", description: "d3", model: "groq-gpt-oss-120b",
    output: "strategy_json", agent: { id: "strategy-drafter", version: "1.0.0", tools: [] } },
];
const run = (over: Partial<Run> = {}): Run => ({
  id: "r1", service_id: "bot-review", agent_id: "bot-reviewer", agent_version: "1.0.0", instructions_sha256: "a".repeat(64),
  model: "groq-gpt-oss-120b", input: { bot_id: "bot-1", days: 7 }, status: "SUCCEEDED", output_text: "## Bilan\n- **net** : −5 $",
  output_json: null, tokens_in: 400, tokens_out: 100, cost_usd: "0.0003", cost_estimated_only: false, model_calls: 2,
  tool_calls: 1, started_at: "2026-11-21T10:00:00+00:00", finished_at: "2026-11-21T10:00:04+00:00", duration_ms: 4000,
  error_code: null, error_detail: null,
  steps: [
    { seq: 1, kind: "MODEL_CALL", at: "2026-11-21T10:00:01+00:00", detail: { attempt: 1, messages: [] }, tokens_in: 100,
      tokens_out: 20, cost_actual_usd: "0.0001", cost_estimated_usd: null, duration_ms: 900, error_code: null },
    { seq: 2, kind: "TOOL_CALL", at: "2026-11-21T10:00:02+00:00", detail: { tool: "get_stats", arguments: "{}", result: { overall: {} } },
      tokens_in: 0, tokens_out: 0, cost_actual_usd: null, cost_estimated_usd: null, duration_ms: 30, error_code: null },
  ], ...over,
});

function mockFetch(roles: string[], handler: (u: string, init?: RequestInit) => Response | undefined) {
  const m = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const u = String(input);
    if (u.includes("/api/v1/me")) return Promise.resolve(json({ roles, email: "x@x.io" }));
    if (u.endsWith("/api/v1/bots")) return Promise.resolve(json({ items: [{ id: "bot-1", name: "paper-kraken-01" }] }));
    if (u.endsWith("/api/v1/ai/services")) return Promise.resolve(json({ items: SERVICES }));
    return Promise.resolve(handler(u, init) ?? json({ code: "NOT_FOUND" }, 404));
  });
  vi.stubGlobal("fetch", m);
  return m;
}

const page = (path = "/ai") => render(
  <MemoryRouter initialEntries={[path]}><Routes><Route path="/ai" element={<AiPage />} /></Routes></MemoryRouter>);

beforeEach(() => {
  localStorage.setItem("access_token", "t");
  vi.unstubAllGlobals();
});

describe("AI services screen", () => {
  it("shows the budget bar with its level, in text as well as colour", () => {
    const { rerender } = render(<BudgetBar budget={budget()} />);
    expect(screen.getByTestId("budget")).toHaveTextContent("0.0012 $ / 10.00 $");
    rerender(<BudgetBar budget={budget({ spent_usd: "8.5", spent_pct: "85.0" })} />);
    expect(screen.getByTestId("budget")).toHaveClass("orange");
    expect(screen.getByTestId("budget")).toHaveTextContent("over 80 %");
    rerender(<BudgetBar budget={budget({ spent_usd: "10", spent_pct: "100.0", exhausted: true })} />);
    expect(screen.getByTestId("budget")).toHaveTextContent("exhausted");
  });

  it("renders Markdown without HTML", () => {
    render(<MarkdownText text={"# Titre\n- **gras** <b>x</b>"} />);
    expect(screen.getByText("gras").tagName).toBe("STRONG");
    expect(screen.getByText(/<b>x<\/b>/)).toBeInTheDocument();
  });

  it("runs a service and shows the steps as they arrive", async () => {
    let polls = 0;
    const m = mockFetch(["ANALYST", "OBSERVER"], (u, init) => {
      if (u.endsWith("/api/v1/ai/budget")) return json(budget());
      if (u.endsWith("/api/v1/ai/runs") && init?.method === "POST") return json(run({ status: "RUNNING", steps: [] }), 202);
      if (u.endsWith("/api/v1/ai/runs/r1")) {
        polls += 1;
        return json(polls === 1 ? run({ status: "RUNNING", steps: run().steps!.slice(0, 1), output_text: null, finished_at: null, duration_ms: null }) : run());
      }
      return undefined;
    });
    page("/ai?service=bot-review&bot_id=bot-1");
    const user = userEvent.setup();
    const form = await screen.findByRole("form", { name: "Bilan d'un bot" });
    await user.click(form.querySelector("button[type=submit]")!);
    const post = m.mock.calls.find(([u, i]) => String(u).endsWith("/api/v1/ai/runs") && i?.method === "POST")!;
    expect(JSON.parse(String(post[1]!.body))).toEqual({ service_id: "bot-review", input: { bot_id: "bot-1", days: 7 } });
    expect(new Headers(post[1]!.headers).get("Idempotency-Key")).toBeTruthy();
    expect(await screen.findByTestId("step-1")).toHaveTextContent("model call");
    expect(await screen.findByTestId("step-2", {}, { timeout: 4000 })).toHaveTextContent("tool get_stats");
    expect(screen.getByTestId("answer")).toHaveTextContent("Bilan");
  });

  it("disables Run when the budget is exhausted or without the ANALYST role", async () => {
    mockFetch(["ANALYST"], (u) => (u.endsWith("/api/v1/ai/budget") ? json(budget({ exhausted: true, spent_pct: "100.0" })) : undefined));
    page();
    await screen.findByRole("form", { name: "Bilan d'un bot" });
    await waitFor(() => expect(screen.getAllByText(/daily budget exhausted/).length).toBeGreaterThan(0));
    vi.unstubAllGlobals();
    mockFetch(["OBSERVER"], (u) => (u.endsWith("/api/v1/ai/budget") ? json(budget()) : undefined));
    page();
    expect((await screen.findAllByText(/ANALYST role is required/)).length).toBeGreaterThan(0);
  });

  it("lists the journal and shows the admin alert banner", async () => {
    mockFetch(["ADMIN", "OBSERVER"], (u) => {
      if (u.endsWith("/api/v1/ai/budget")) return json(budget({ alerts: [{ id: "a1", threshold: 80, source: "app", spent_usd: "8",
        created_at: "2026-11-21T10:00:00+00:00", email_sent: true, email_error: null, acked_at: null }] }));
      if (u.includes("/api/v1/ai/runs?")) return json({ items: [run()] });
      return undefined;
    });
    page("/ai?tab=journal");
    expect(await screen.findByTestId("run-r1")).toHaveTextContent("SUCCEEDED");
    expect(await screen.findByTestId("ai-alert")).toHaveTextContent("over 80 %");
    expect(screen.getByRole("tab", { name: "Settings" })).toBeInTheDocument();
    render(<AiBudgetBanner />);
    expect(await screen.findByTestId("ai-global-alert")).toHaveTextContent("over 80 %");
  });
});
