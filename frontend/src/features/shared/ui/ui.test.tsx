// T-D1 — shared components (spec 02-design/03 « Composants partagés »).
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { BotCard, Dialog, Money, Pct, StatusBadge, Tabs, strategyColor } from ".";

describe("shared UI", () => {
  it("maps strategies to their token, unknown ids to the fallback", () => {
    expect(strategyColor("rebound")).toBe("var(--hl-s-rebound)");
    expect(strategyColor("my-new-one")).toBe("var(--hl-s-other)");
    expect(strategyColor(null)).toBe("var(--hl-s-other)");
  });

  it("formats money and percentages with sign, colour and dash", () => {
    const { container, rerender } = render(<Money value="-3.104" signed />);
    expect(container.textContent).toBe("−$3.10");
    expect(container.firstChild).toHaveClass("hl-num", "hl-loss");
    rerender(<Money value="12.4" signed />);
    expect(container.textContent).toBe("+$12.40");
    expect(container.firstChild).toHaveClass("hl-gain");
    rerender(<Money value={null} />);
    expect(container.textContent).toBe("—");
    rerender(<Pct value="0.4567" signed />);
    expect(container.textContent).toBe("+0.46%");
    rerender(<Pct value={undefined} />);
    expect(container.textContent).toBe("—");
  });

  it("writes the status, never colour alone", () => {
    render(<StatusBadge status="PAUSED" />);
    expect(screen.getByText("PAUSED")).toHaveClass("hl-badge", "hl-tone-warn");
  });

  it("renders a bot card with its strategy and pipeline steps", async () => {
    const onStage = vi.fn();
    render(<BotCard name="paper-kraken-02" strategyId="breakout-1h" strategyName="Breakout 1h" status="RUNNING"
                    line="SOL/USD · Spread too wide" stages={{ Scan: "on", Check: "stop" }} onStage={onStage} />);
    const card = screen.getByTestId("botcard-paper-kraken-02");
    expect(card).toHaveStyle({ "--accent": "var(--hl-s-breakout-1h)" });
    expect(card).toHaveTextContent("Breakout 1h");
    expect(screen.getByText("Check").closest("li")).toHaveClass("stop");
    expect(card).toHaveTextContent("Check (stopped)");
    await userEvent.click(screen.getByRole("button", { name: /Size/ }));
    expect(onStage).toHaveBeenCalledWith("Size");
  });

  it("dialog takes the focus, keeps Tab inside and closes on Escape", async () => {
    const onClose = vi.fn();
    render(<Dialog title="Confirm" onClose={onClose} actions={<><button>Cancel</button><button>OK</button></>}>body</Dialog>);
    expect(screen.getByRole("dialog", { name: "Confirm" })).toBeInTheDocument();
    expect(screen.getByText("Cancel")).toHaveFocus();
    await userEvent.tab();
    expect(screen.getByText("OK")).toHaveFocus();
    await userEvent.tab();
    expect(screen.getByText("Cancel")).toHaveFocus();
    await userEvent.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalled();
  });

  it("tabs report the selection", async () => {
    const onChange = vi.fn();
    render(<Tabs label="Views" value="a" onChange={onChange} tabs={[{ id: "a", label: "A" }, { id: "b", label: "B" }]} />);
    expect(screen.getByRole("tab", { name: "A" })).toHaveAttribute("aria-selected", "true");
    await userEvent.click(screen.getByRole("tab", { name: "B" }));
    expect(onChange).toHaveBeenCalledWith("b");
  });
});
