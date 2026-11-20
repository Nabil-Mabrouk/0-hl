// 0-hl project routes (ADR-015, spec 03-ecrans/M1-terminal-et-bots « Intégration au châssis »).
// Relative to the chassis AppShell. Mounted by App.tsx once the gitsky template provides
// this extension point — App.tsx (chassis) is never patched locally.
import { Route } from "react-router-dom";
import BotsPage from "../features/bots/BotsPage";
import StatsPage from "../features/analytics/StatsPage";
import TradesPage from "../features/analytics/TradesPage";
import RiskPage from "../features/risk/RiskPage";
import StrategiesPage from "../features/strategies/StrategiesPage";
import StrategyEditor from "../features/strategies/StrategyEditor";
import TerminalPage from "../features/terminal/TerminalPage";

export const domainRoutes = (
  <>
    <Route path="terminal" element={<TerminalPage />} />
    <Route path="bots" element={<BotsPage />} />
    {/* M2.5 (spec 03-ecrans/M2-trades-et-statistiques) */}
    <Route path="trades" element={<TradesPage />} />
    <Route path="stats" element={<StatsPage />} />
    {/* M2.3 (spec 05-strategies/04 « Écrans ») */}
    <Route path="strategies" element={<StrategiesPage />} />
    <Route path="strategies/:vid" element={<StrategyEditor />} />
    <Route path="risk" element={<RiskPage />} />
  </>
);
