// T-MR-07 — market report screens (spec 03-ecrans/UI-18 « Écrans », ADR-030).
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ChartView } from "./charts";
import { LatestReportCard, MarketListPage, MarketReportPage } from "./MarketPages";
import type { Report, ReportSummary } from "./types";

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
afterEach(() => vi.unstubAllGlobals());

const summary = (over: Partial<ReportSummary> = {}): ReportSummary => ({
  id: "r1", cadence: "4h", period_start: "2026-11-23T08:00:00+00:00", period_end: "2026-11-23T12:00:00+00:00",
  status: "PUBLISHED", generated_by: "llm", title: "A falling market widens", summary: ["BTC fell -1.84 %.", "b", "c"],
  tags: [{ tag: "broad-selloff", source: "rules" }, { tag: "high-volatility", source: "model" }],
  btc_return_pct: "-1.84", breadth_pct: "19.4", dispersion_pp: "1.92", btc_vol_ratio: "1.62", volume_ratio: "1.48", ...over,
});
const CHARTS = {
  leaders: { bars: [{ symbol: "TRX/USD", excess_pp: "2.66", vol_ratio: "0.61" }, { symbol: "PEPE/USD", excess_pp: "-4.12", vol_ratio: "2.84" }] },
  "btc-breadth": { points: [{ t: "2026-11-23T08:02:00+00:00", btc: "86420", up_pct: "45.0" }, { t: "2026-11-23T12:00:00+00:00", btc: "84610", up_pct: "19.4" }] },
  distribution: { bins: [{ from: "-5.00", to: "-4.50", count: 3 }, { from: "0.00", to: "0.50", count: 2 }] },
  volume: { bucket_minutes: 30, bars: [{ t: "2026-11-23T08:00:00+00:00", vs_normal: "1.20" }, { t: "2026-11-23T08:30:00+00:00", vs_normal: "0.80" }] },
  volatility: { bars: [{ symbol: "ETH/USD", vol_ratio: "1.55" }] },
  trend3: { points: [{ period_end: "2026-11-23T04:00:00+00:00", btc_return_pct: "0.48", breadth_pct: "61.3", dispersion_pp: "0.94" },
                     { period_end: "2026-11-23T12:00:00+00:00", btc_return_pct: "-1.84", breadth_pct: "19.4", dispersion_pp: "1.92" }] },
  liquidity: { pairs: [{ symbol: "BTC/USD", spread_start: "3.0", spread_end: "4.1", depth_change_pct: "-18.0" }] },
  bots: { bots: [{ name: "paper-kraken-03", strategy: "Rebound after excess", strategy_id: "rebound", signals: 2, refusals: { spread: 1 }, opened: 1, closed: 0, net_pct: "-0.31" }] },
  radar: { window_minutes: 240, pairs: [{ symbol: "SOL/USD", excess_pp: "1.40", y: "1.0000", judgeable: true }],
           bots: [{ name: "paper-kraken-04", strategy: "rotation", open_positions: ["SOL/USD"] }] },
};
const report = (over: Partial<Report> = {}): Report => ({
  ...summary(), model: "groq-gpt-oss-120b", previous_id: "r0", next_id: null,
  sections: [{ id: "leaders", heading: "Who held up", body: "Calmer pairs did better.\n\n{{chart:leaders}}\n\nThat is all." }],
  charts: [{ id: "leaders", caption: "Calmer pairs beat BTC.", data: CHARTS.leaders }], ...over,
});

describe("charts", () => {
  it("draws every chart of the catalogue with a text alternative", () => {
    for (const [id, data] of Object.entries(CHARTS)) {
      const { unmount } = render(<ChartView chart={{ id, caption: `caption ${id}`, data }} />);
      const fig = screen.getByTestId(`chart-${id}`);
      expect(fig).toHaveTextContent(`caption ${id}`);
      if (!["liquidity", "bots"].includes(id)) expect(within(fig).getByRole("img").getAttribute("aria-label")).toBeTruthy();
      unmount();
    }
    render(<ChartView chart={{ id: "leaders", caption: "c", data: CHARTS.leaders }} />);
    expect(screen.getByRole("img").getAttribute("aria-label")).toContain("TRX/USD +2.66 pp");
    expect(screen.getByText("−4.12 pp")).toBeInTheDocument();
  });
});

describe("pages", () => {
  it("lists reports with figures and tags, and filters through the URL", async () => {
    const f = vi.fn((u: RequestInfo | URL) => Promise.resolve(json({
      items: String(u).includes("cadence=24h") ? [] : [summary(), summary({ id: "m1", status: "MISSED", title: null, generated_by: null })],
      total: 2, offset: 0 })));
    vi.stubGlobal("fetch", f);
    render(<MemoryRouter initialEntries={["/market"]}><Routes><Route path="/market" element={<MarketListPage />} /></Routes></MemoryRouter>);
    const item = await screen.findByTestId("report-r1");
    expect(item).toHaveTextContent("A falling market widens");
    expect(item).toHaveTextContent("BTC −1.84%");
    expect(item).toHaveTextContent("Broad sell-off");
    expect(screen.getByTestId("report-m1")).toHaveTextContent("No report for this period");
    await userEvent.selectOptions(screen.getByLabelText("Series"), "24h");
    await waitFor(() => expect(f.mock.calls.some(([u]) => String(u).includes("cadence=24h"))).toBe(true));
    expect(await screen.findByText("No report matches these filters.")).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("Search"), "selloff{Enter}");
    await waitFor(() => expect(f.mock.calls.some(([u]) => String(u).includes("q=selloff"))).toBe(true));
  });

  it("renders a report with its charts at their place, and the series navigation", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(json(report()))));
    render(<MemoryRouter initialEntries={["/market/r1"]}><Routes><Route path="/market/:id" element={<MarketReportPage />} /></Routes></MemoryRouter>);
    const section = await screen.findByRole("region", { name: "Who held up" }, { timeout: 4000 });
    expect(within(section).getByTestId("chart-leaders")).toBeInTheDocument();
    expect(section).toHaveTextContent("That is all.");
    expect(screen.getByText("BTC fell -1.84 %.")).toBeInTheDocument();
    expect(screen.getByText(/Generated by an AI model \(groq-gpt-oss-120b\)/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "← Previous 4h" })).toHaveAttribute("href", "/market/r0");
    expect(document.title).toContain("A falling market widens");
  });

  it("renders a data-only report with its charts", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(json(report({ generated_by: "data-only", status: "DATA_ONLY", sections: [],
      charts: [{ id: "btc-breadth", caption: "BTC and breadth.", data: CHARTS["btc-breadth"] }] })))));
    render(<MemoryRouter initialEntries={["/market/r1"]}><Routes><Route path="/market/:id" element={<MarketReportPage />} /></Routes></MemoryRouter>);
    expect(await screen.findByTestId("chart-btc-breadth")).toBeInTheDocument();
    expect(screen.getByText(/Data-only report/)).toBeInTheDocument();
  });

  it("home-page card shows the latest report of the chosen series", async () => {
    const f = vi.fn((u: RequestInfo | URL) => Promise.resolve(String(u).includes("cadence=24h")
      ? json({ code: "NOT_FOUND" }, 404) : json({ ...summary(), charts: [{ id: "leaders", caption: "c", data: CHARTS.leaders }] })));
    vi.stubGlobal("fetch", f);
    render(<MemoryRouter><LatestReportCard /></MemoryRouter>);
    const card = await screen.findByTestId("latest-report");
    expect(await within(card).findByRole("link", { name: "A falling market widens" })).toHaveAttribute("href", "/market/r1");
    expect(within(card).getByTestId("chart-leaders")).toBeInTheDocument();
    await userEvent.click(within(card).getByRole("button", { name: "24h" }));
    expect(await within(card).findByText("No 24h report yet.")).toBeInTheDocument();
  });
});
