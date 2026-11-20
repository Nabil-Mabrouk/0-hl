// Filter bar for Trades / Stats: every change is written to the URL (shareable, survives reload).
import { useEffect, useState } from "react";
import { getJson } from "../shared/client";
import type { Bot, StrategyVersionView } from "../shared/types";
import { EXIT_REASONS, type Filters, type Period } from "./filters";

export default function FiltersBar({ value, onChange, pairs = [], full = true }: {
  value: Filters; onChange: (f: Filters) => void; pairs?: string[]; full?: boolean;
}) {
  const [bots, setBots] = useState<Bot[]>([]);
  const [versions, setVersions] = useState<StrategyVersionView[]>([]);
  useEffect(() => {
    getJson<{ items: Bot[] }>("/api/v1/bots").then((r) => setBots(r.items)).catch(() => undefined);
    if (full) getJson<{ items: StrategyVersionView[] }>("/api/v1/strategies").then((r) => setVersions(r.items)).catch(() => undefined);
  }, [full]);
  const set = (patch: Partial<Filters>) => onChange({ ...value, ...patch });
  return (
    <div className="hl-filters" role="search">
      <label className="hl-field">bot
        <select aria-label="bot" value={value.bot} onChange={(e) => set({ bot: e.target.value })}>
          <option value="">all bots</option>
          {bots.map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}
        </select>
      </label>
      <label className="hl-field">period
        <select aria-label="period" value={value.period} onChange={(e) => set({ period: e.target.value as Period })}>
          <option value="today">today (UTC)</option>
          <option value="7d">last 7 days</option>
          <option value="30d">last 30 days</option>
          <option value="all">all</option>
          <option value="custom">dates…</option>
        </select>
      </label>
      {value.period === "custom" && (
        <>
          <label className="hl-field">from<input type="date" aria-label="from" value={value.since} onChange={(e) => set({ since: e.target.value })} /></label>
          <label className="hl-field">to<input type="date" aria-label="to" value={value.until} onChange={(e) => set({ until: e.target.value })} /></label>
        </>
      )}
      {full && (
        <>
          <label className="hl-field">pair
            <input aria-label="pair" list="hl-pairs" placeholder="kraken_spot:BTC/USD" value={value.pair}
              onChange={(e) => set({ pair: e.target.value })} />
            <datalist id="hl-pairs">{pairs.map((p) => <option key={p} value={p} />)}</datalist>
          </label>
          <label className="hl-field">exit
            <select aria-label="exit reason" value={value.reason} onChange={(e) => set({ reason: e.target.value })}>
              <option value="">all exits</option>
              {EXIT_REASONS.map((r) => <option key={r} value={r}>{r}</option>)}
            </select>
          </label>
          <label className="hl-field">strategy version
            <select aria-label="strategy version" value={value.version} onChange={(e) => set({ version: e.target.value })}>
              <option value="">all versions</option>
              {versions.map((v) => <option key={v.id} value={v.id}>{v.strategy_id} v{v.version}</option>)}
            </select>
          </label>
        </>
      )}
    </div>
  );
}
