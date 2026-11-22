import { useEffect, useState } from "react";
import { apiFetch } from "../../api";

// Footer partagé par la landing ET le reste de l'app (Chap 24, round
// theming) — fichier du projet (AGENTS.md). Autonome (GET /health, même
// patron que Navbar). 0-hl (02-design/03, ADR-028) : copyright et rappel
// factuel du mode PAPER, rien d'inventé.
export default function Footer() {
  const [project, setProject] = useState("");

  useEffect(() => {
    apiFetch("/health")
      .then(async (r) => (r.ok ? ((await r.json()) as { project?: string }) : null))
      .then((data) => setProject(data?.project ?? ""))
      .catch(() => {});
  }, []);

  return (
    <footer className="site-footer">
      <div className="site-footer-inner">
        <p>
          © {new Date().getFullYear()} {project}
        </p>
        <p>Paper trading on real Kraken prices · simulated execution · not investment advice</p>
      </div>
    </footer>
  );
}
