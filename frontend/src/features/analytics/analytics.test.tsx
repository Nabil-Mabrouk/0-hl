// T-M2.5-07 — Trades (UI-03) and Stats (UI-12) screens (spec 03-ecrans/M2-trades-et-statistiques).
import type React from "react";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { apiQuery, periodRange, readFilters, writeFilters } from "./filters";
import StatsPage, { CurveChart } from "./StatsPage";
import TradesPage, { sortTrades, summarise, type TradeView } from "./TradesPage";

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

const trade = (id: string, net: string, closed: string, extra: Partial<TradeView> = {}): TradeView => ({
  id, instrument_id: "kraken_spot:SOL/USD", quantity: "0", avg_entry_price: "100", avg_exit_price: "101",
  entry_notional: "1300", fees_paid: "10.4", realized_pnl_net: net, net_pct: String(Number(net) / 13),
  r_multiple: "0.5", exit_reason: "TIME", opened_at: closed, closed_at: closed, hold_seconds: 3600,
  entry_slippage_bps: "4.2", correlation_id: `c-${id}`, strategy: { version_id: "v1", strategy_id: "volume-spike", version: "0.2.0" },
  ...extra,
});
const stats = (over: Record<string, unknown> = {}) => ({
  trades: 2, wins: 1, losses: 1, win_rate_pct: "50", net_usd: "-5.5", gross_usd: "15.3", fees_usd: "20.8",
  fees_share_of_gross_pct: "135.9", avg_win_usd: "5", avg_loss_usd: "-10.5", expectancy_usd: "-2.75",
  expectancy_pct: "-0.2", profit_factor: "0.47", avg_r: "0.1", avg_hold_seconds: 3600, avg_entry_slippage_bps: "4",
  exits: { TIME: 2 }, sample_too_small: true, ...over,
});

function mockFetch(handler: (u: string) => Response | undefined) {
  const m = vi.fn((input: RequestInfo | URL) => {
    const u = String(input);
    if (u.includes("/api/v1/me")) return Promise.resolve(json({ roles: ["OBSERVER"], email: "x@x.io" }));
    if (u.endsWith("/api/v1/bots")) return Promise.resolve(json({ items: [{ id: "bot-1", name: "paper-kraken-01" }] }));
    if (u.endsWith("/api/v1/strategies")) return Promise.resolve(json({ items: [] }));
    return Promise.resolve(handler(u) ?? json({ code: "NOT_FOUND" }, 404));
  });
  vi.stubGlobal("fetch", m);
  return m;
}

function Where() {
  const l = useLocation();
  return <div data-testid="where">{l.search}</div>;
}
function renderAt(path: string, el: React.ReactElement) {
  render(<MemoryRouter initialEntries={[path]}><Routes><Route path="*" element={<>{el}<Where /></>} /></Routes></MemoryRouter>);
}

beforeEach(() => {
  localStorage.clear();
  localStorage.setItem("access_token", "tok");
  vi.restoreAllMocks();
});

describe("filters", () => {
  it("round-trips through the URL and builds the API query", () => {
    const f = readFilters(new URLSearchParams("bot=b1&period=custom&since=2026-11-18&until=2026-11-19&reason=STOP"));
    expect(writeFilters(f).toString()).toBe("bot=b1&period=custom&since=2026-11-18&until=2026-11-19&reason=STOP");
    expect(periodRange(f)).toEqual({ since: "2026-11-18T00:00:00.000Z", until: "2026-11-20T00:00:00.000Z" });
    expect(apiQuery(readFilters(new URLSearchParams("period=all&pair=kraken_spot:BTC/USD")))).toBe("instrument_id=kraken_spot%3ABTC%2FUSD");
    expect(readFilters(new URLSearchParams("")).period).toBe("7d");
  });
});

describe("TradesPage (UI-03)", () => {
  it("shows realised and unrealised apart, sorts, keeps filters in the URL and opens the trace", async () => {
    const m = mockFetch((u) => {
      if (u.includes("/api/v1/trades?")) return json({ items: [trade("t1", "5", "2026-11-20T10:00:00Z"), trade("t2", "-10.5", "2026-11-20T12:00:00Z")], total: 2 });
      if (u.includes("/api/v1/stats?")) return json({ overall: { trades: 2, net_usd: "-5.5", fees_usd: "20.8" } });
      if (u.includes("/api/v1/positions?bot_id=bot-1")) return json({ items: [{ id: "p", instrument_id: "kraken_spot:BTC/USD", quantity: "0.01",
        avg_entry_price: "61000", mark_price: "61100", unrealized_pnl: null, opened_at: "2026-11-20T12:00:00Z" }] });
      if (u.includes("/events/trace/c-t2")) return json({ items: [
        { event_id: "e1", event_type: "CANDIDATE_DETECTED", occurred_at: "2026-11-20T11:00:00Z", payload: { features: { volume_spike: "5.1" } } },
        { event_id: "e2", event_type: "POSITION_CLOSED", occurred_at: "2026-11-20T12:00:00Z", payload: { reason: "TIME", realized_pnl_net: "-10.5" } }] });
    });
    renderAt("/trades?bot=bot-1", <TradesPage />);
    const summary = await screen.findByTestId("summary");
    expect(summary).toHaveTextContent("realised net -$5.50");
    expect(summary).toHaveTextContent("unrealised —");                   // unknown mark ⇒ —, never 0
    const rows = () => within(screen.getByTestId("trades")).getAllByRole("row").slice(1);
    expect(rows()[0]).toHaveTextContent("-$10.50");                      // newest first
    await userEvent.click(screen.getByRole("button", { name: /^net$/ }));
    expect(rows()[0]).toHaveTextContent("+$5.00");                       // net, descending
    await userEvent.click(rows()[1]);
    const trace = await screen.findByTestId("trace");
    expect(trace).toHaveTextContent("CANDIDATE_DETECTED");
    expect(trace).toHaveTextContent("realized_pnl_net=-10.5");
    await userEvent.click(screen.getByRole("button", { name: "close" }));
    await userEvent.selectOptions(screen.getByLabelText("exit reason"), "STOP");
    await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent("reason=STOP"));
    expect(m.mock.calls.some(([u]) => String(u).includes("exit_reason=STOP"))).toBe(true);
  });

  it("empty and error states", async () => {
    mockFetch((u) => {
      if (u.includes("/api/v1/trades?")) return json({ code: "INTERNAL", detail: "boom", correlation_id: "x" }, 500);
    });
    renderAt("/trades", <TradesPage />);
    expect(await screen.findByRole("alert")).toHaveTextContent("INTERNAL");
  });

  it("sorts and summarises", () => {
    const s = sortTrades([trade("a", "1", "2026-11-20T10:00:00Z"), trade("b", "-2", "2026-11-20T11:00:00Z")], "realized_pnl_net", true);
    expect(s.map((t) => t.id)).toEqual(["b", "a"]);
    expect(summarise({ event_type: "POSITION_SIZED", payload: { quantity: "1", binding_limit: "risk" } } as never)).toBe("quantity=1  binding_limit=risk");
  });
});

describe("StatsPage (UI-12)", () => {
  it("cards, version comparison flagged as small, curve with drawdown point", async () => {
    mockFetch((u) => {
      if (u.includes("/api/v1/stats?")) return json({
        overall: stats(),
        by_version: [{ version_id: "v1", strategy_id: "volume-spike", version: "0.2.0", ...stats() },
          { version_id: "v2", strategy_id: "volume-spike", version: "0.3.0", ...stats({ trades: 25, sample_too_small: false }) }],
        by_pair: [{ instrument_id: "kraken_spot:SOL/USD", trades: 2, net_usd: "-5.5", win_rate_pct: "50", fees_usd: "20.8" }],
        by_exit_reason: { TIME: 2 },
      });
      if (u.includes("/api/v1/stats/curve?bot_id=bot-1")) return json({
        points: [{ at: "2026-11-20T10:00:00Z", performance: "0" }, { at: "2026-11-20T11:00:00Z", performance: "-50" },
          { at: "2026-11-20T12:00:00Z", performance: "-20" }],
        max_drawdown_pct: "0.5", max_drawdown_at: "2026-11-20T11:00:00Z" });
    });
    renderAt("/stats?bot=bot-1", <StatsPage />);
    const cards = await screen.findByTestId("cards");
    expect(cards).toHaveTextContent("net result-$5.50");
    expect(cards).toHaveTextContent("136% of gross");
    expect(await screen.findByTestId("curve")).toBeInTheDocument();
    expect(screen.getByTestId("trough")).toBeInTheDocument();
    expect(cards).toHaveTextContent("max drawdown0.50%");
    const table = screen.getByTestId("by-version");
    expect(within(table).getAllByText(/sample too small/)).toHaveLength(1);
    expect(table).toHaveTextContent("volume-spike v0.3.0");
    expect(screen.getByTestId("exits")).toHaveTextContent("TIME 2");
  });

  it("asks for a bot before drawing a curve and shows the empty state", async () => {
    mockFetch((u) => (u.includes("/api/v1/stats?") ? json({ overall: stats({ trades: 0 }), by_version: [], by_pair: [], by_exit_reason: {} }) : undefined));
    renderAt("/stats", <StatsPage />);
    expect(await screen.findByText("No closed trade for these filters.")).toBeInTheDocument();
    expect(screen.getByText(/Select a bot/)).toBeInTheDocument();
  });

  it("curve needs two points", () => {
    render(<CurveChart curve={{ points: [{ at: "2026-11-20T10:00:00Z", performance: "0" }], max_drawdown_pct: null, max_drawdown_at: null }} />);
    expect(screen.getByTestId("curve-empty")).toBeInTheDocument();
  });
});
