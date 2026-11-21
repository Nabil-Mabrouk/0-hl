// UI-16 « Alertes »: banner on every page for an ADMIN while today's AI budget alert is not acknowledged.
import { useEffect, useState } from "react";
import { getJson } from "../shared/client";
import type { Budget } from "./AiPage";
import "./ai.css";

export default function AiBudgetBanner() {
  const [alert, setAlert] = useState<Budget["alerts"][number] | null>(null);
  useEffect(() => {
    if (!localStorage.getItem("access_token")) return;
    let alive = true;
    getJson<{ roles: string[] }>("/api/v1/me")
      .then((me) => (me.roles.includes("ADMIN") ? getJson<Budget>("/api/v1/ai/budget") : null))
      .then((b) => {
        const open = (b?.alerts ?? []).filter((a) => !a.acked_at);
        if (alive) setAlert(open.length ? open[open.length - 1] : null);
      })
      .catch(() => undefined);
    return () => { alive = false; };
  }, []);
  if (!alert) return null;
  return (
    <div className="hl-ai-global-banner" role="alert" data-testid="ai-global-alert">
      AI budget {alert.threshold === 100 ? "exhausted — no AI call until 00:00 UTC" : "over 80 %"}
      {alert.email_sent ? "" : " (e-mail not sent)"} · <a href="/ai?tab=services">AI services</a>
    </div>
  );
}
