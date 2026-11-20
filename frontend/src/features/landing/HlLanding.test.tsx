// T-UI00-02 — public landing sections (spec 03-ecrans/UI-00-accueil-public).
import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const auth = vi.hoisted(() => ({ user: null as null | { email: string } }));
vi.mock("../../context/AuthContext", () => ({ useAuth: () => ({ user: auth.user, logout: vi.fn() }) }));

import HlLanding, { ago } from "./HlLanding";

const stats = {
  bots_total: 2, bots_running: 1, trades_total: 189, trades_24h: 12, open_positions: 1,
  pairs_live: 38, pairs_subscribed: 40, worker_alive: true, evaluations_24h: 51840,
  market_data_days: "3.1", last_trade_at: "2026-11-20T20:00:00Z", generated_at: "2026-11-20T21:00:00Z",
};
const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

beforeEach(() => {
  localStorage.clear();
  vi.restoreAllMocks();
  auth.user = null;
  // reduced motion ⇒ counters show their final value at once
  vi.stubGlobal("matchMedia", (q: string) => ({ matches: q.includes("reduce"), media: q, addEventListener() {}, removeEventListener() {} }));
});

describe("HlLanding", () => {
  it("shows the live figures from the public endpoint", async () => {
    const fetchMock = vi.fn(() => Promise.resolve(json(stats)));
    vi.stubGlobal("fetch", fetchMock);
    render(<HlLanding />);
    await waitFor(() => expect(screen.getByTestId("hl-live")).toHaveTextContent("LIVE"));
    await waitFor(() => expect(screen.getByTestId("stat-bots")).toHaveTextContent("1 / 2"));
    expect(screen.getByTestId("stat-trades")).toHaveTextContent("189");
    expect(screen.getByTestId("stat-trades24")).toHaveTextContent("12");
    expect(screen.getByTestId("stat-trades24")).toHaveTextContent("1 position(s) open now");
    expect(screen.getByTestId("stat-pairs")).toHaveTextContent("38 / 40");
    expect(screen.getByTestId("stat-evals")).toHaveTextContent("51,840");
    expect(screen.getByTestId("stat-days")).toHaveTextContent("3.1");
    expect(String((fetchMock.mock.calls[0] as unknown[])[0])).toContain("/api/v1/public/stats");
  });

  it("shows — (never 0) when the endpoint fails, and OFFLINE when the worker is down", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(json({ code: "X" }, 500))));
    const { unmount } = render(<HlLanding />);
    await waitFor(() => expect(screen.getByTestId("hl-live")).toHaveTextContent("UNAVAILABLE"));
    expect(screen.getByTestId("stat-trades")).toHaveTextContent("—");
    expect(screen.getByTestId("stat-trades")).not.toHaveTextContent("0");
    unmount();
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(json({ ...stats, worker_alive: false }))));
    render(<HlLanding />);
    await waitFor(() => expect(screen.getByTestId("hl-live")).toHaveTextContent("OFFLINE"));
  });

  it("sends visitors to /login and signed-in users to /terminal", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(json(stats))));
    const { unmount } = render(<HlLanding />);
    expect(screen.getByRole("link", { name: "Open the terminal" })).toHaveAttribute("href", "/login");
    unmount();
    auth.user = { email: "x@x.io" };
    render(<HlLanding />);
    expect(screen.getByRole("link", { name: "Open the terminal" })).toHaveAttribute("href", "/terminal");
    expect(screen.getByRole("link", { name: "How it works" })).toHaveAttribute("href", "#how");
  });

  it("formats relative times", () => {
    const now = Date.parse("2026-11-20T21:00:00Z");
    expect(ago("2026-11-20T20:59:30Z", now)).toBe("30s ago");
    expect(ago("2026-11-20T20:00:00Z", now)).toBe("1 h ago");
    expect(ago(null, now)).toBe("—");
  });
});
