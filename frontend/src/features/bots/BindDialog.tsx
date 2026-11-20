// UI-02 addition, M2.3 « Changer de stratégie » (spec 05-strategies/04 « Règles » 7): attach a bot at rest
// (READY or STOPPED) to published versions; a STOPPED bot becomes READY and can be started again.
import { useEffect, useState } from "react";
import { ApiError, getJson, sendJson } from "../shared/client";
import type { Bot, Change, PolicyVersionView, StrategyVersionView } from "../shared/types";
import { fmt } from "../strategies/StrategiesPage";

export const REBINDABLE = ["READY", "STOPPED"];

export default function BindDialog({ bot, onClose, onDone }: {
  bot: Bot & { strategy?: { strategy_id: string; version: string } };
  onClose: () => void;
  onDone: (message: string) => void;
}) {
  const [strategies, setStrategies] = useState<StrategyVersionView[]>([]);
  const [policies, setPolicies] = useState<PolicyVersionView[]>([]);
  const [sv, setSv] = useState(bot.strategy_version_id);
  const [pv, setPv] = useState(bot.risk_policy_version_id);
  const [reason, setReason] = useState("");
  const [changes, setChanges] = useState<Change[] | null>(null);
  const [error, setError] = useState<ApiError | null>(null);

  useEffect(() => {
    Promise.all([
      getJson<{ items: StrategyVersionView[] }>("/api/v1/strategies"),
      getJson<{ items: PolicyVersionView[] }>("/api/v1/risk-policies"),
    ]).then(([s, p]) => {
      setStrategies(s.items.filter((x) => x.status === "PUBLISHED"));
      setPolicies(p.items.filter((x) => x.status === "PUBLISHED"));
    }).catch((e) => setError(e instanceof ApiError ? e : new ApiError(0, "NETWORK", String(e), null)));
  }, []);

  const chosen = strategies.find((x) => x.id === sv);
  const current = strategies.find((x) => x.id === bot.strategy_version_id);
  const allowedPolicies = policies.filter((p) => !chosen || p.policy_id === chosen.config.risk_policy_id);

  useEffect(() => {
    if (chosen && !allowedPolicies.some((p) => p.id === pv) && allowedPolicies[0]) setPv(allowedPolicies[0].id);
  }, [chosen, allowedPolicies, pv]);

  useEffect(() => {
    setChanges(null);
    if (!chosen || !current || chosen.strategy_id !== current.strategy_id || chosen.id === current.id) return;
    getJson<{ changes: Change[] }>(`/api/v1/strategies/diff?a=${current.id}&b=${chosen.id}`)
      .then((r) => setChanges(r.changes)).catch(() => undefined);
  }, [chosen, current]);

  async function submit() {
    try {
      await sendJson("POST", `/api/v1/bots/${bot.id}/bind`,
        { strategy_version_id: sv, risk_policy_version_id: pv, reason }, { "If-Match": `"${bot.row_version}"` });
      onDone(`bound to ${chosen?.strategy_id ?? ""} v${chosen?.version ?? ""} — bot READY, START to run it`);
    } catch (e) {
      setError(e instanceof ApiError ? e : new ApiError(0, "NETWORK", String(e), null));
    }
  }

  return (
    <div className="hl-dialog-backdrop">
      <div className="hl-dialog" role="dialog" aria-label="Change strategy" style={{ maxWidth: 560 }}>
        <h3>Change strategy — {bot.name}</h3>
        <p>Current: {bot.strategy ? `${bot.strategy.strategy_id} v${bot.strategy.version}` : bot.strategy_version_id}. The next START uses the new versions.</p>
        <label className="hl-field">strategy version (published)
          <select aria-label="strategy version" value={sv} onChange={(e) => setSv(e.target.value)}>
            {strategies.map((s) => <option key={s.id} value={s.id}>{s.strategy_id} v{s.version}</option>)}
          </select>
        </label>
        <label className="hl-field">risk policy version (published, required by the strategy)
          <select aria-label="risk policy version" value={pv} onChange={(e) => setPv(e.target.value)}>
            {allowedPolicies.map((p) => <option key={p.id} value={p.id}>{p.policy_id} v{p.version}</option>)}
          </select>
        </label>
        {changes && (
          <table className="hl-table hl-diff" data-testid="bind-diff">
            <thead><tr><th>field</th><th>current</th><th>new</th></tr></thead>
            <tbody>{changes.map((c) => <tr key={c.path}><td><code>{c.path}</code></td><td>{fmt(c.before)}</td><td className="changed">{fmt(c.after)}</td></tr>)}</tbody>
          </table>
        )}
        {sv === bot.strategy_version_id && pv === bot.risk_policy_version_id && (
          <p className="hl-readonly-note">Same versions: this only re-arms the bot (STOPPED → READY).</p>
        )}
        <label className="hl-field">reason (required)
          <input value={reason} onChange={(e) => setReason(e.target.value)} />
        </label>
        {error && <div className="hl-banner error" role="alert">{error.code} — {error.detail}</div>}
        <div className="hl-actions">
          <button type="button" className="hl-btn" onClick={onClose}>cancel</button>
          <button type="button" className="hl-btn" disabled={!reason.trim() || !sv || !pv} onClick={() => void submit()}>confirm change</button>
        </div>
      </div>
    </div>
  );
}
