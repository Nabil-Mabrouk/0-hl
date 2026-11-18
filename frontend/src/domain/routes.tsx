// 0-hl project routes (ADR-015, spec 03-ecrans/M1-terminal-et-bots « Intégration au châssis »).
// Relative to the chassis AppShell. Mounted by App.tsx once the gitsky template provides
// this extension point — App.tsx (chassis) is never patched locally.
import { Route } from "react-router-dom";
import BotsPage from "../features/bots/BotsPage";
import TerminalPage from "../features/terminal/TerminalPage";

export const domainRoutes = (
  <>
    <Route path="terminal" element={<TerminalPage />} />
    <Route path="bots" element={<BotsPage />} />
  </>
);
