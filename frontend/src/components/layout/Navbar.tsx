import { useCallback, useEffect, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { NavLink, useInRouterContext, useLocation } from "react-router-dom";
import { useAuth } from "../../context/AuthContext";
import { apiFetch } from "../../api";
import AiBudgetBanner from "../../features/ai/AiBudgetBanner";

// Navbar partagée par la landing ET le reste de l'app (Chap 24, round
// theming) — fichier du projet, à personnaliser (AGENTS.md). Un seul GET
// /health fournit `project` (marque) ET `modules` (liens à afficher).
//
// 0-hl (02-design/03 « Mise en page », ADR-028) : navigation regroupée
// (Trading, Lab, AI, Learn), page en cours marquée (aria-current), liens du
// routeur (plus de rechargement complet), menu plein écran sous 900 px, et
// chargement des polices Inter + JetBrains Mono pour tout le site. Hors
// routeur (tests unitaires du châssis), les liens restent de simples <a>.

export const FONT_HREF =
  "https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500&display=swap";

function useSiteFonts() {
  useEffect(() => {
    if (document.querySelector("link[data-hl-fonts]")) return;
    const link = document.createElement("link");
    link.rel = "stylesheet";
    link.href = FONT_HREF;
    link.dataset.hlFonts = "1";
    document.head.append(link);
  }, []);
}

function PlainLink({ to, children }: { to: string; children: ReactNode }) {
  const current = typeof window !== "undefined" && window.location.pathname.startsWith(to);
  return <a href={to} aria-current={current ? "page" : undefined}>{children}</a>;
}

function CloseOnRouteChange({ close }: { close: () => void }) {
  const { pathname } = useLocation();
  useEffect(() => close(), [pathname, close]);
  return null;
}

export default function Navbar() {
  const { t, i18n } = useTranslation();
  const { user, logout } = useAuth();
  const inRouter = useInRouterContext();
  const [project, setProject] = useState("");
  const [modules, setModules] = useState<Record<string, boolean>>({});
  const [open, setOpen] = useState(false);
  useSiteFonts();

  useEffect(() => {
    apiFetch("/health")
      .then(async (r) =>
        r.ok
          ? ((await r.json()) as { project?: string; modules?: Record<string, boolean> })
          : null,
      )
      .then((data) => {
        setProject(data?.project ?? "");
        setModules(data?.modules ?? {});
      })
      .catch(() => setModules({}));
  }, []);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  const close = useCallback(() => setOpen(false), []);
  const link = (to: string, label: ReactNode) =>
    inRouter ? <NavLink key={to} to={to} onClick={close}>{label}</NavLink> : <PlainLink key={to} to={to}>{label}</PlainLink>;

  const toggleLang = () =>
    i18n.changeLanguage(i18n.language.startsWith("fr") ? "en" : "fr");

  return (
    <>
      <nav className="site-nav" aria-label="Main">
        {inRouter && <CloseOnRouteChange close={close} />}
        <div className="site-nav-inner">
          <a className="brand" href="/">
            {project}
          </a>
          <button type="button" className="site-nav-toggle" aria-expanded={open}
                  aria-controls="site-nav-links" onClick={() => setOpen((v) => !v)}>
            {open ? "Close" : "Menu"}
          </button>
          <div id="site-nav-links" className={`site-nav-links${open ? " open" : ""}`}>
            {user && (
              <div className="site-nav-group" role="group" aria-label="Trading">
                {link("/terminal", "Terminal")}
                {link("/bots", "Bots")}
                {link("/trades", "Trades")}
                {link("/stats", "Stats")}
              </div>
            )}
            {user && (
              <div className="site-nav-group" role="group" aria-label="Lab">
                {link("/strategies", "Strategies")}
                {link("/risk", "Risk")}
              </div>
            )}
            {(user || modules.tutorials) && (
              <div className="site-nav-group" role="group" aria-label="More">
                {user && link("/ai", "AI")}
                {modules.tutorials && link("/learn", t("nav.learn"))}
                {user?.role === "admin" && <a href="/admin">{t("admin.nav")}</a>}
              </div>
            )}
            <div className="site-nav-group" role="group" aria-label="Account">
              {modules.i18n && (
                <button className="site-nav-lang" onClick={toggleLang} type="button">
                  {i18n.language.slice(0, 2)}
                </button>
              )}
              {user ? (
                <button className="btn" onClick={logout} type="button">
                  {t("nav.logout")}
                </button>
              ) : (
                <a className="btn" href="/login">
                  {t("nav.login")}
                </a>
              )}
            </div>
          </div>
        </div>
      </nav>
      {user && <AiBudgetBanner />}
    </>
  );
}
