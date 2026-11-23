// T-L03..L06 — Learn section (spec 03-ecrans/UI-17, ADR-029).
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { article, articles, parseArticle, segments } from "./content";
import { GuidePage, StrategySheet, type StrategyDetail } from "./GuidePages";
import { rules, sentence, value } from "./rules";

vi.mock("../../context/AuthContext", () => ({ useAuth: () => ({ user: null }) }));

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
afterEach(() => vi.unstubAllGlobals());

const breakout = (): StrategyDetail => ({
  id: "breakout-1h", name: "Breakout 1h (STRAT-02)", version: "0.1.0", published_at: "2026-11-22T10:00:00Z",
  bot: { name: "paper-kraken-02", status: "RUNNING", since: "2026-11-22T10:05:00Z", net_pct: "-0.42", trades: 3 },
  config_version: "0.1.0",
  config: {
    universe: { top_n_by_volume: 40, quote_currencies: ["USD"], exclude_stablecoins: true, min_volume_24h_usd: "1000000" },
    entry: { operator: "AND", conditions: [
      { feature: "volume_spike", op: "gte", threshold: "2.0", unit: "ratio" },
      { feature: "breakout_1h", op: "gt", threshold: "0", unit: "log_return" },
      { feature: "breakout_1h", op: "lte", threshold: "0.01", unit: "log_return" },
      { feature: "trend_4h", op: "gt", threshold: "0", unit: "log_return" },
      { feature: "spread_bps", op: "lte", threshold: "30", unit: "bps" }] },
    exit: { initial_stop_pct: "2.5", take_profit_r: "2", max_hold_seconds: 21600, exit_on_status_change: true },
    cooldown_seconds: 14400, risk_per_trade_pct: "0.5", max_open_positions: 5,
  },
  stats: { net_pct: "-0.42", trades: 3, wins: 1, losses: 2, win_rate_pct: "33.33", best_pct: "4.10", worst_pct: "-2.71",
           max_drawdown_pct: "1.20", as_of: "2026-11-22T14:00:00Z" },
  trades: [{ symbol: "SOL/USD", opened_at: "2026-11-22T11:00:00Z", closed_at: "2026-11-22T12:10:00Z", result_pct: "4.10", exit_reason: "TAKE_PROFIT" }],
  versions: [{ version: "0.1.0", published_at: "2026-11-22T10:00:00Z", reason: "ADR-027 observation paper" }],
});

describe("Learn content", () => {
  it("reads the front matter and splits the body at the reserved lines", () => {
    const a = parseArticle("/x/content/en/strategies/demo.md",
      '---\nid: demo\ntitle: Demo\norder: 3\nradar_zone: { x: [0, 3], y: [0, 1.5] }\nstudy: { date: 2026-11-21, verdict: "No launch" }\n---\nIntro\n\n{{live}}\n\nMore\n');
    expect(a).toMatchObject({ id: "demo", title: "Demo", order: 3, radarZone: { x: [0, 3], y: [0, 1.5] } });
    expect(a?.study?.verdict).toBe("No launch");
    expect(segments(a!.body)).toEqual([{ text: "Intro\n\n" }, { block: "live" }, { text: "\n\nMore\n" }]);
  });

  it("ships the Breakout 1h article and falls back to English", () => {
    expect(articles("strategies").map((a) => a.id)).toEqual(["volume-spike", "breakout-1h", "rebound", "rotation", "pullback"]);
    expect(article("strategies", "breakout-1h", "de")?.lang).toBe("en");                  // T-L06
  });

  it("T-L05: the shipped articles are the validated drafts of the spec", async () => {
    const fsName = "node:fs";
    const fs = (await import(/* @vite-ignore */ fsName)) as { readFileSync(p: string, e: string): string };
    const cwd = (globalThis as unknown as { process: { cwd(): string } }).process.cwd();
    const norm = (s: string) => s.replace(/\r\n/g, "\n");
    for (const a of articles("strategies")) {
      const shipped = fs.readFileSync(`${cwd}/src/domain/content/en/strategies/${a.id}.md`, "utf-8");
      const draft = fs.readFileSync(`${cwd}/../docs/GROK_BOT_TERMINAL_BIBLE_V2_COMPLETE/grok-bot-bible-v2/examples/learn/en/strategies/${a.id}.md`, "utf-8");
      expect(norm(shipped)).toBe(norm(draft));
    }
  });
});

describe("rules in plain English (T-L03)", () => {
  it("writes one sentence per condition, log returns in %", () => {
    expect(sentence({ feature: "volume_spike", op: "gte", threshold: "2.0" })).toBe("trading volume over the last 5 minutes is at least 2× its usual level");
    expect(sentence({ feature: "breakout_1h", op: "gt", threshold: "0" })).toBe("the price closes above the highest price of the previous hour");
    expect(sentence({ feature: "breakout_1h", op: "lte", threshold: "0.01" })).toBe("the price is at most 1 % above the highest price of the previous hour");
    expect(sentence({ feature: "brand_new", op: "lt", threshold: "3" })).toBe("brand_new lt 3");
    expect(value("25000", "USD")).toBe("$25,000");
    const r = rules(breakout().config);
    expect(r.entry).toHaveLength(5);
    expect(r.exit).toEqual(expect.arrayContaining([
      "Stop-loss: it sells if the price falls 2.5 % below the entry price.",
      "Target: it sells at 2R, i.e. 5 % above the entry price.",
      "Time limit: it sells after 6 hours at most."]));
    expect(r.other.join(" ")).toContain("waits 4 hours before trading the same pair again");
    expect(r.other.join(" ")).toContain("risks at most 0.5 % of the bot");
  });
});

describe("pages (T-L04)", () => {
  it("lists the strategies with their live result", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(json({ items: [breakout()] }))));
    render(<MemoryRouter><GuidePage /></MemoryRouter>);
    const card = await screen.findByTestId("guide-breakout-1h");
    expect(card).toHaveTextContent("Breakout 1h");
    expect(card).toHaveTextContent("−0.42%");
    expect(card).toHaveTextContent("Buy a pair when it breaks above");
  });

  it("renders the article with its live blocks at the reserved lines, without raw HTML", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(json(breakout()))));
    render(<MemoryRouter initialEntries={["/guide/strategies/breakout-1h"]}>
      <Routes><Route path="/guide/strategies/:id" element={<StrategySheet />} /></Routes></MemoryRouter>);
    expect(await screen.findByTestId("block-live")).toHaveTextContent("+4.10%");
    expect(screen.getByTestId("block-rules")).toHaveTextContent("the price closes above the highest price of the previous hour");
    expect(screen.getByTestId("block-trades")).toHaveTextContent("SOL/USD");
    expect(screen.getByTestId("block-study")).toHaveTextContent("No launch — 0 of 5 criteria passed");
    expect(screen.getByTestId("block-versions")).toHaveTextContent("0.1.0");
    expect(screen.getByTestId("block-radar")).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 2, name: "The idea in one sentence" })).toBeInTheDocument();
    expect(document.title).toContain("Breakout 1h");
  });

  it("says when no bot runs the strategy, and renders a strategy without article", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(json({ ...breakout(), id: "brand-new", name: "Brand new", bot: null, stats: null, trades: [] }))));
    render(<MemoryRouter initialEntries={["/guide/strategies/brand-new"]}>
      <Routes><Route path="/guide/strategies/:id" element={<StrategySheet />} /></Routes></MemoryRouter>);
    expect(await screen.findByText("No paper bot running this strategy.")).toBeInTheDocument();
    expect(screen.getByText("The full article for this strategy is coming soon.")).toBeInTheDocument();
    expect(screen.getByTestId("block-trades")).toHaveTextContent("No trade yet.");
  });
});
