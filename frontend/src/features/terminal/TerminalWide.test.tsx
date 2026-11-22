// T-M1.5-03 / 07 / 10 — the terminal renders API values, "—" for unknown, and no motion when reduced.
// ADR-028 (D.3): agents column and central flow replaced by the Market radar; agents open as drawers.
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import TerminalWide, { type TerminalViewProps } from "./TerminalWide";
import { ev, snapshot } from "../shared/testFixtures";

function renderCanvas(overrides: Partial<TerminalViewProps> = {}) {
  const props: TerminalViewProps = {
    snapshot: snapshot(), events: [], connection: "LIVE", userLabel: "nabil", canOperate: true,
    pausePending: false, onTogglePause: vi.fn(), onOpenTrace: vi.fn(), reducedMotion: false,
    drawer: null, onDrawer: vi.fn(), ...overrides,
  };
  render(<TerminalWide {...props} />);
  return props;
}

describe("TerminalWide", () => {
  it("shows balance, trades, wins/losses, scan and the last trade", () => {
    renderCanvas();
    expect(screen.getByTestId("hl-balance")).toHaveTextContent("$10,745.21");
    expect(screen.getByTestId("hl-trades")).toHaveTextContent("61");
    expect(screen.getByTestId("hl-wins")).toHaveTextContent("40");
    expect(screen.getByTestId("hl-losses")).toHaveTextContent("21");
    const trade = screen.getByTestId("hl-trade");
    expect(trade).toHaveTextContent("$XRP");
    expect(trade).toHaveTextContent("take profit");
    expect(trade).toHaveTextContent("spike 4.1×");
    expect(screen.getByText("0-hl")).toBeInTheDocument();
    expect(screen.queryByTestId("hl-agent-EXECUTOR")).not.toBeInTheDocument();     // no agents column
  });

  it("renders the radar slot above the trade, and an agent drawer for the selected bot", async () => {
    const props = renderCanvas({ radar: <div data-testid="radar-slot">radar</div>, drawer: "EXECUTOR" });
    expect(screen.getByTestId("radar-slot")).toBeInTheDocument();
    const drawer = screen.getByRole("dialog", { name: "EXECUTOR details" });
    expect(drawer).toHaveTextContent("slippage cap 0.5%");
    await userEvent.click(within(drawer).getByRole("button", { name: "Close" }));
    expect(props.onDrawer).toHaveBeenCalledWith(null);
  });

  it("renders unknown values as — (never 0)", () => {
    const s = snapshot();
    renderCanvas({ snapshot: { ...s, equity: { ...s.equity, equity: null, quality: "STALE" }, scan: null, recent_trades: [] } });
    expect(screen.getByTestId("hl-balance")).toHaveTextContent("—");
    expect(screen.getByTestId("hl-now")).toHaveTextContent("—");
    expect(screen.getByTestId("hl-trade")).toHaveTextContent("no trade yet");
  });

  it("formats the log from events and opens the trace on click", async () => {
    const events = [
      ev("CANDIDATE_DETECTED", { instrument_id: "kraken_spot:ENA/USD", features: { volume_spike: "18.25" } }, { correlation_id: "c-ena" }),
      ev("POSITION_OPENED", { quantity: "100", avg_entry_price: "1.5" }, { correlation_id: "c-ena" }),
      ev("RISK_REJECTED", { stage: "RUG_CHECK", failed: ["spread"] }, { correlation_id: "c-x" }),
    ];
    const props = renderCanvas({ events });
    const log = screen.getByTestId("hl-log");
    expect(within(log).getByText("ENTER")).toBeInTheDocument();
    expect(within(log).getByText("$ENA")).toBeInTheDocument();
    expect(within(log).getByText("FILTERED")).toBeInTheDocument();
    expect(screen.getByTestId("hl-tail")).toHaveTextContent("ENTER $ENA");
    await userEvent.click(within(log).getAllByRole("button")[1]);
    expect(props.onOpenTrace).toHaveBeenCalledWith("c-ena");
  });

  it("pause button: disabled without operator role, calls handler otherwise", async () => {
    const props = renderCanvas();
    await userEvent.click(screen.getByRole("button", { name: "Pause entries" }));
    expect(props.onTogglePause).toHaveBeenCalledOnce();
  });

  it("pause button is disabled for a non-operator", () => {
    renderCanvas({ canOperate: false });
    expect(screen.getByRole("button", { name: "Pause entries" })).toBeDisabled();
  });

  it("applies no motion class under reduced motion", () => {
    const events = [ev("CANDIDATE_DETECTED", { instrument_id: "kraken_spot:ENA/USD", features: {} })];
    renderCanvas({ events, reducedMotion: true });
    const canvas = screen.getByTestId("hl-canvas");
    expect(canvas.className).not.toMatch(/-motion/);
    expect(canvas.querySelector("[class*='-arrive']")).toBeNull();
  });

  it("shows the scanner fails breakdown in the SCANNER drawer", () => {
    renderCanvas({ drawer: "SCANNER" });
    expect(screen.getByTestId("hl-scan-fails")).toHaveTextContent("checked 35");
    expect(screen.getByTestId("hl-scan-fails")).toHaveTextContent("volume_spike=30 window_notional_usd=21");
  });

  it("review 2026-11-19: SIZER refusals are REFUSED on the SIZER card, not RUG CHECK", () => {
    const events = [
      ev("CANDIDATE_DETECTED", { instrument_id: "kraken_spot:SUI/USD", features: { volume_spike: "36.7" } }, { correlation_id: "c-sui" }),
      ev("RISK_PASSED", { stage: "RUG_CHECK" }, { correlation_id: "c-sui" }),
      ev("RISK_REJECTED", { stage: "SIZER", reasons: ["MAX_DAILY_LOSS"] }, { correlation_id: "c-sui" }),
    ];
    renderCanvas({ events, drawer: "SIZER" });
    expect(within(screen.getByTestId("hl-log")).getByText("REFUSED")).toBeInTheDocument();
    expect(screen.getByTestId("hl-drawer")).toHaveTextContent("refused $SUI: max_daily_loss");
  });

  it("review 2026-11-19: closed trade size and result use the amount committed at entry", () => {
    renderCanvas();
    const trade = screen.getByTestId("hl-trade");
    expect(trade).toHaveTextContent("13% of capital ($1,310.67)");
    expect(trade).toHaveTextContent("+26.1% closed");            // 342.52 / 1310.67
  });

  it("review 2026-11-19: RESULT bar is centred on the starting capital", () => {
    const s = snapshot();
    renderCanvas({ snapshot: { ...s, equity: { ...s.equity, equity: "9462.05" } } });
    const bar = screen.getByTestId("hl-result-bar").querySelector("span") as HTMLElement;
    expect(bar.className).toContain("bad");
    expect(bar.style.left).toBe("23.1%");                         // −5.38 % ⇒ half-bar 26.9 % left of centre
  });

  it("2026-11-20: an EXIT always shows its symbol, even when the candidate event is gone", () => {
    const closed = ev("POSITION_CLOSED", { reason: "TIME", realized_pnl_net: "-24.52" },
      { aggregate_type: "position", aggregate_id: "p1", correlation_id: "c-old" });  // p1 = $XRP in the snapshot
    renderCanvas({ events: [closed] });
    const log = screen.getByTestId("hl-log");
    expect(within(log).getByText("$XRP")).toBeInTheDocument();
    expect(screen.getByTestId("hl-activity")).toHaveTextContent("EXECUTOR · EXITED $XRP");
  });
});
