// T-M1.5-03 / 07 — the canvas renders API values, "—" for unknown, and no motion when reduced.
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import TerminalCanvas, { type TerminalCanvasProps } from "./TerminalCanvas";
import TerminalWide from "./TerminalWide";
import { ev, snapshot } from "../shared/testFixtures";

// ADR-016: the full-width layout and the reference canvas must show the same data.
describe.each([
  ["reference canvas", TerminalCanvas],
  ["full width", TerminalWide],
] as const)("%s", (_name, View) => {
function renderCanvas(overrides: Partial<TerminalCanvasProps> = {}) {
  const props: TerminalCanvasProps = {
    snapshot: snapshot(), events: [], connection: "LIVE", userLabel: "nabil", canOperate: true,
    pausePending: false, onTogglePause: vi.fn(), onOpenTrace: vi.fn(), reducedMotion: false, ...overrides,
  };
  render(<View {...props} />);
  return props;
}

describe("TerminalCanvas", () => {
  it("shows balance, trades, wins/losses, scan and the last trade", () => {
    renderCanvas();
    expect(screen.getByTestId("hl-balance")).toHaveTextContent("$10,745.21");
    expect(screen.getByTestId("hl-trades")).toHaveTextContent("61");
    expect(screen.getByTestId("hl-wins")).toHaveTextContent("40");
    expect(screen.getByTestId("hl-losses")).toHaveTextContent("21");
    expect(screen.getByTestId("hl-checked")).toHaveTextContent("checked 35");
    const trade = screen.getByTestId("hl-trade");
    expect(trade).toHaveTextContent("$XRP");
    expect(trade).toHaveTextContent("take profit");
    expect(trade).toHaveTextContent("spike 4.1×");
    expect(screen.getByText("0-hl")).toBeInTheDocument();
    expect(screen.getByTestId("hl-agent-EXECUTOR")).toHaveTextContent("slippage cap 0.5%");
  });

  it("renders unknown values as — (never 0)", () => {
    const s = snapshot();
    renderCanvas({ snapshot: { ...s, equity: { ...s.equity, equity: null, quality: "STALE" }, scan: null, recent_trades: [] } });
    expect(screen.getByTestId("hl-balance")).toHaveTextContent("—");
    expect(screen.getByTestId("hl-now")).toHaveTextContent("—");
    expect(screen.getByTestId("hl-checked")).toHaveTextContent("checked —");
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

  it("shows the scanner fails breakdown in the agent drawer", async () => {
    renderCanvas();
    await userEvent.click(screen.getByTestId("hl-agent-SCANNER"));
    expect(screen.getByTestId("hl-scan-fails")).toHaveTextContent("volume_spike=30 window_notional_usd=21");
  });
});

  it("review 2026-11-19: SIZER refusals are REFUSED on the SIZER card, not RUG CHECK", () => {
    const events = [
      ev("CANDIDATE_DETECTED", { instrument_id: "kraken_spot:SUI/USD", features: { volume_spike: "36.7" } }, { correlation_id: "c-sui" }),
      ev("RISK_PASSED", { stage: "RUG_CHECK" }, { correlation_id: "c-sui" }),
      ev("RISK_REJECTED", { stage: "SIZER", reasons: ["MAX_DAILY_LOSS"] }, { correlation_id: "c-sui" }),
    ];
    renderCanvas({ events });
    expect(within(screen.getByTestId("hl-log")).getByText("REFUSED")).toBeInTheDocument();
    expect(screen.getByTestId("hl-agent-SIZER")).toHaveTextContent("refused $SUI: max_daily_loss");
    expect(screen.getByTestId("hl-agent-RUG_CHECK")).not.toHaveTextContent("$SUI");
  });

  it("review 2026-11-19: closed trade size and result use the amount committed at entry", () => {
    renderCanvas();
    const trade = screen.getByTestId("hl-trade");
    expect(trade).toHaveTextContent("13% of capital ($1,310.67)");
    expect(trade).toHaveTextContent("+26.1% closed");            // 342.52 / 1310.67
    expect(screen.getByText("NO SIGNAL")).toBeInTheDocument();
  });

  it("review 2026-11-19: RESULT bar is centred on the starting capital", () => {
    const s = snapshot();
    renderCanvas({ snapshot: { ...s, equity: { ...s.equity, equity: "9462.05" } } });
    const bar = screen.getByTestId("hl-result-bar").querySelector("span") as HTMLElement;
    expect(bar.className).toContain("bad");
    expect(bar.style.left).toBe("23.1%");                         // −5.38 % ⇒ half-bar 26.9 % left of centre
  });
});
