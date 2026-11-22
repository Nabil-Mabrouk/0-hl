// T-D1 — 0-hl header (spec 02-design/03 « Mise en page »): groups, current page, router links,
// mobile menu, fonts. The chassis Navbar.test.tsx still covers brand, Learn, Admin, login, language.
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import Navbar, { FONT_HREF } from "./Navbar";

const mockUseAuth = vi.fn();
vi.mock("../../context/AuthContext", () => ({ useAuth: () => mockUseAuth() }));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (k: string) => k, i18n: { language: "en", changeLanguage: vi.fn() } }),
}));
vi.mock("../../features/ai/AiBudgetBanner", () => ({ default: () => null }));

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(new Response(JSON.stringify({ project: "0-hl", modules: { tutorials: true } }),
    { status: 200, headers: { "Content-Type": "application/json" } }))));
  mockUseAuth.mockReturnValue({ user: { id: 1, email: "a@x.io", role: "user" }, logout: vi.fn() });
});

const at = (path: string) => render(<MemoryRouter initialEntries={[path]}><Navbar /></MemoryRouter>);

describe("0-hl header", () => {
  it("groups the links and marks the current page", async () => {
    at("/trades");
    expect(screen.getByRole("group", { name: "Trading" })).toHaveTextContent("TerminalBotsTradesStats");
    expect(screen.getByRole("group", { name: "Lab" })).toHaveTextContent("StrategiesRisk");
    expect(screen.getByRole("link", { name: "Trades" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: "Bots" })).not.toHaveAttribute("aria-current");
    await waitFor(() => expect(screen.getByRole("link", { name: "nav.learn" })).toHaveAttribute("href", "/learn"));
  });

  it("shows only Learn and Log in to a visitor", async () => {
    mockUseAuth.mockReturnValue({ user: null, logout: vi.fn() });
    at("/");
    expect(screen.queryByRole("group", { name: "Trading" })).not.toBeInTheDocument();
    await waitFor(() => expect(screen.getByText("nav.learn")).toBeInTheDocument());
    expect(screen.getByText("nav.login")).toBeInTheDocument();
  });

  it("opens the mobile menu and closes it on Escape or navigation", async () => {
    at("/terminal");
    const toggle = screen.getByRole("button", { name: "Menu" });
    await userEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(document.getElementById("site-nav-links")).toHaveClass("open");
    await userEvent.keyboard("{Escape}");
    expect(document.getElementById("site-nav-links")).not.toHaveClass("open");
    await userEvent.click(screen.getByRole("button", { name: "Menu" }));
    await userEvent.click(screen.getByRole("link", { name: "Bots" }));
    expect(document.getElementById("site-nav-links")).not.toHaveClass("open");
    expect(screen.getByRole("link", { name: "Bots" })).toHaveAttribute("aria-current", "page");
  });

  it("loads Inter and JetBrains Mono once", () => {
    at("/");
    at("/bots");
    const links = document.querySelectorAll("link[data-hl-fonts]");
    expect(links).toHaveLength(1);
    expect(links[0]).toHaveAttribute("href", FONT_HREF);
    expect(FONT_HREF).toContain("JetBrains+Mono");
  });
});
