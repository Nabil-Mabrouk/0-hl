// UI-02 addition, M2.4 « New bot » (spec 06-agents/04 « Écrans »): a PAPER bot = a published strategy
// version + the policy it requires + a paper capital; created READY, started only by START.
import { useEffect, useState } from "react";
import { ApiError, fieldErrors, getJson, sendJson } from "../shared/client";
import type { PolicyVersionView, StrategyVersionView } from "../shared/types";

export default function CreateBotDialog({ onClose, onDone }: { onClose: () => void; onDone: (name: string) => void }) {
  const [strategies, setStrategies] = useState<StrategyVersionView[]>([]);
  const [policies, setPolicies] = useState<PolicyVersionView[]>([]);
  const [form, setForm] = useState({ name: "", sv: "", pv: "", capital: "10000", reason: "" });
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [error, setError] = useState<ApiError | null>(null);

  useEffect(() => {
    Promise.all([
      getJson<{ items: StrategyVersionView[] }>("/api/v1/strategies"),
      getJson<{ items: PolicyVersionView[] }>("/api/v1/risk-policies"),
    ]).then(([s, p]) => {
      const pub = s.items.filter((x) => x.status === "PUBLISHED");
      setStrategies(pub);
      setPolicies(p.items.filter((x) => x.status === "PUBLISHED"));
      if (pub[0]) setForm((f) => ({ ...f, sv: f.sv || pub[pub.length - 1].id }));
    }).catch((e) => setError(e instanceof ApiError ? e : new ApiError(0, "NETWORK", String(e), null)));
  }, []);

  const chosen = strategies.find((s) => s.id === form.sv);
  const allowed = policies.filter((p) => !chosen || p.policy_id === chosen.config.risk_policy_id);
  useEffect(() => {
    if (!allowed.some((p) => p.id === form.pv) && allowed.length) setForm((f) => ({ ...f, pv: allowed[allowed.length - 1].id }));
  }, [allowed, form.pv]);

  async function submit() {
    try {
      setError(null);
      await sendJson("POST", "/api/v1/bots", { name: form.name, strategy_version_id: form.sv,
        risk_policy_version_id: form.pv, capital_usd: form.capital, reason: form.reason });
      onDone(form.name);
    } catch (e) {
      const ae = e instanceof ApiError ? e : new ApiError(0, "NETWORK", String(e), null);
      setErrors(fieldErrors(ae));
      setError(ae);
    }
  }

  const field = (key: keyof typeof form, path: string, label: string, props: Record<string, unknown> = {}) => (
    <label className={`hl-field${errors[path] ? " invalid" : ""}`}>{label}
      <input aria-label={label} value={form[key]} onChange={(e) => setForm({ ...form, [key]: e.target.value })} {...props} />
      {errors[path] && <span className="hl-field-error">{errors[path]}</span>}
    </label>
  );

  return (
    <div className="hl-dialog-backdrop">
      <div className="hl-dialog" role="dialog" aria-label="New bot" style={{ maxWidth: 520 }}>
        <h3>New PAPER bot</h3>
        <p className="hl-readonly-note">Created READY with a fictitious capital; press START to run it. At most 5 bots that are not STOPPED.</p>
        {field("name", "name", "name (lowercase, digits, -)", { placeholder: "paper-momentum-02" })}
        <label className="hl-field">strategy version (published)
          <select aria-label="strategy version" value={form.sv} onChange={(e) => setForm({ ...form, sv: e.target.value })}>
            {strategies.map((s) => <option key={s.id} value={s.id}>{s.strategy_id} v{s.version}</option>)}
          </select>
        </label>
        <label className={`hl-field${errors.risk_policy_version_id ? " invalid" : ""}`}>risk policy (required by the strategy)
          <select aria-label="risk policy version" value={form.pv} onChange={(e) => setForm({ ...form, pv: e.target.value })}>
            {allowed.map((p) => <option key={p.id} value={p.id}>{p.policy_id} v{p.version}</option>)}
          </select>
          {errors.risk_policy_version_id && <span className="hl-field-error">{errors.risk_policy_version_id}</span>}
        </label>
        {field("capital", "capital_usd", "paper capital (USD, 100 – 1,000,000)", { inputMode: "decimal" })}
        {field("reason", "reason", "reason (required)")}
        {error && !Object.keys(errors).length && <div className="hl-banner error" role="alert">{error.code} — {error.detail}</div>}
        <div className="hl-actions">
          <button type="button" className="hl-btn" onClick={onClose}>cancel</button>
          <button type="button" className="hl-btn" disabled={!form.name.trim() || !form.sv || !form.pv || !form.reason.trim()}
            onClick={() => void submit()}>create bot</button>
        </div>
      </div>
    </div>
  );
}
