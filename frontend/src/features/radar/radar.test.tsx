// T-RAD-04 / T-RAD-05 — Market radar component (spec 02-design/05, ADR-028).
import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import MarketRadar from "./MarketRadar";
import { headline, normalise, stagesFor, wiredPairs, type RadarBot, type RadarData } from "./model";

const bot = (over: Partial<RadarBot> = {}): RadarBot => ({
  name: "paper-kraken-02", status: "RUNNING", strategy: { id: "breakout-1h", version: "0.1.0", name: "Breakout 1h" },
  net_pct: "-0.42", open_positions: [],
  last_cycle: { window_end: "2026-11-22T14:32:00Z", evaluated: 40, signals: 1,
                outcomes: { "SOL/USD": { r: "FILTERED", why: "spread" }, "ETH/USD": { r: "NO_SIGNAL" } } },
  ...over,
});
const data = (over: Partial<RadarData> = {}): RadarData => ({
  generated_at: "2026-11-22T14:32:45Z", window_minutes: 60, window_end: new Date().toISOString(), stale: false,
  reference: { symbol: "BTC/USD", return_pct: "1.00", volatility_pct: "0.0800" },
  pairs: [
    { symbol: "SOL/USD", return_pct: "2.40", excess_pp: "1.40", vol_ratio: "2.00", y: "1.0000", minutes_traded: 58, judgeable: true },
    { symbol: "ETH/USD", return_pct: "0.50", excess_pp: "-0.50", vol_ratio: "0.90", y: "-0.1520", minutes_traded: 60, judgeable: true },
    { symbol: "PUMP/USD", return_pct: "12.00", excess_pp: "11.00", vol_ratio: "9.00", y: "3.1699", minutes_traded: 40, judgeable: true },
    { symbol: "THIN/USD", return_pct: "0.00", excess_pp: "-1.00", vol_ratio: null, y: "0", minutes_traded: 8, judgeable: false },
  ],
  pairs_without_data: 0, bots: [bot()], ...over,
});

function motion(reduce: boolean) {
  vi.stubGlobal("matchMedia", (q: string) => ({ matches: reduce && q.includes("reduce"), media: q,
    addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {} }));
}
afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });

describe("radar model", () => {
  it("places pairs on fixed scales and pins outliers to the edge", () => {
    const [sol, , pump, thin] = data().pairs.map((p) => normalise(p, 60));
    expect(sol).toMatchObject({ nx: 1.4 / 5, ny: 0.5, clippedX: 0, hollow: false });
    expect(pump).toMatchObject({ nx: 1, ny: 1, clippedX: 1, clippedY: 1 });
    expect(thin).toMatchObject({ ny: 0, hollow: true });
    expect(normalise(data().pairs[0], 1440).nx).toBeCloseTo(1.4 / 20);
  });

  it("lights the steps of an outcome and stops at the refusing step", () => {
    expect(stagesFor("FILTERED")).toEqual({ Scan: "on", Check: "stop" });
    expect(stagesFor("REFUSED")).toEqual({ Scan: "on", Check: "on", Size: "stop" });
    expect(stagesFor("ENTERED", 2)).toEqual({ Scan: "on", Check: "on" });
    expect(stagesFor("NO_SIGNAL")).toEqual({ Scan: "on" });
  });

  it("wires only signals, positions and the selected pair; headline shows the motive", () => {
    const b = bot({ open_positions: ["ADA/USD"] });
    expect(wiredPairs(b).sort()).toEqual(["ADA/USD", "SOL/USD"]);
    expect(wiredPairs(b, "ETH/USD").sort()).toEqual(["ADA/USD", "ETH/USD", "SOL/USD"]);
    expect(headline(b).line).toBe("SOL/USD · Filtered · Spread too wide");
    expect(headline(bot({ last_cycle: { window_end: null, evaluated: 40, signals: 0, outcomes: { "ETH/USD": { r: "NO_SIGNAL" } } } })).line)
      .toBe("40 scanned · 0 signals");
    expect(headline(bot({ last_cycle: null })).line).toBe("Waiting for scan");
  });
});

describe("MarketRadar", () => {
  it("renders the bot cards, the exact readout and the selection by keyboard (reduced motion)", async () => {
    motion(true);
    const raf = vi.fn();
    vi.stubGlobal("requestAnimationFrame", raf);
    render(<MarketRadar data={data()} />);
    expect(raf).not.toHaveBeenCalled();                                   // T-RAD-05: no animation loop
    expect(screen.queryByRole("button", { name: "Pause" })).not.toBeInTheDocument();
    const card = screen.getByTestId("botcard-paper-kraken-02");
    expect(card).toHaveTextContent("Breakout 1h");
    expect(card).toHaveTextContent("SOL/USD · Filtered · Spread too wide");   // final state at once
    expect(card).toHaveTextContent("−0.42%");
    expect(screen.getByText("Check").closest("li")).toHaveClass("stop");
    await userEvent.selectOptions(screen.getByLabelText("Inspect pair"), "SOL/USD");
    expect(screen.getByTestId("radar-readout")).toHaveTextContent("SOL/USD · +1.40 pp vs BTC · 2.00× BTC volatility · 58 min traded");
    await userEvent.selectOptions(screen.getByLabelText("Inspect pair"), "THIN/USD");
    expect(screen.getByTestId("radar-readout")).toHaveTextContent("thin trading");
    expect(screen.getByRole("img", { name: /Horizontal axis: return versus BTC/ })).toBeInTheDocument();
  });

  it("replays a cycle: steps appear after the sweep, Pause and Replay control the loop", async () => {
    motion(false);
    let cb: FrameRequestCallback | null = null;
    vi.stubGlobal("requestAnimationFrame", (f: FrameRequestCallback) => { cb = f; return 1; });
    vi.stubGlobal("cancelAnimationFrame", () => {});
    const vis = vi.spyOn(document, "visibilityState", "get").mockReturnValue("visible");   // jsdom: "hidden"
    render(<MarketRadar data={data()} />);
    const card = screen.getByTestId("botcard-paper-kraken-02");
    expect(card).toHaveTextContent("Waiting for scan");                    // during the sweep
    let t = 0;
    await act(async () => { for (let i = 0; i < 40; i++) { t += 100; cb?.(t); } });   // ≈ 3.9 s
    expect(card).toHaveTextContent("SOL/USD · Filtered");
    expect(screen.getByText("Check").closest("li")).toHaveClass("stop");
    await userEvent.click(screen.getByRole("button", { name: "Pause" }));
    expect(screen.getByRole("button", { name: "Play" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Replay" }));
    expect(card).toHaveTextContent("Waiting for scan");
    vis.mockRestore();
  });

  it("stops the loop while the tab is hidden", async () => {
    motion(false);
    let cb: FrameRequestCallback | null = null;
    vi.stubGlobal("requestAnimationFrame", (f: FrameRequestCallback) => { cb = f; return 1; });
    vi.stubGlobal("cancelAnimationFrame", () => {});
    const vis = vi.spyOn(document, "visibilityState", "get").mockReturnValue("hidden");
    render(<MarketRadar data={data()} />);
    await act(async () => { for (let t = 100; t < 4000; t += 100) cb?.(t); });
    expect(screen.getByTestId("botcard-paper-kraken-02")).toHaveTextContent("Waiting for scan");
    vis.mockRestore();
  });

  it("shows states: unavailable, delayed, no bot", () => {
    motion(true);
    const { rerender } = render(<MarketRadar data={null} failed />);
    expect(screen.getByRole("alert")).toHaveTextContent("Radar unavailable");
    rerender(<MarketRadar data={data({ stale: true, bots: [] })} />);
    expect(screen.getByText("Market data delayed")).toBeInTheDocument();
    expect(screen.getByText("No bot running")).toBeInTheDocument();
  });
});
