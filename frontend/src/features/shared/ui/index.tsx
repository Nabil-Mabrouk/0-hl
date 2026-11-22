// Shared UI components — spec 02-design/03 « Composants partagés » (ADR-028).
// Every page uses these instead of its own styles; colours come from the tokens of
// branding.local.css only.
import {
  useEffect, useId, useRef,
  type ButtonHTMLAttributes, type CSSProperties, type InputHTMLAttributes, type ReactNode,
  type SelectHTMLAttributes, type TextareaHTMLAttributes,
} from "react";
import { DASH, fmtSignedUsd, fmtUsd } from "../format";
import "./ui.css";

const cx = (...c: (string | false | null | undefined)[]) => c.filter(Boolean).join(" ");

// ---------------------------------------------------------------- strategy colours
const KNOWN_STRATEGIES = new Set(["volume-spike", "breakout-1h", "rebound", "rotation", "pullback"]);

/** CSS colour of a strategy (`--hl-s-<id>`); unknown id ⇒ `--hl-s-other`. Always shown with its name. */
export function strategyColor(strategyId: string | null | undefined): string {
  return strategyId && KNOWN_STRATEGIES.has(strategyId) ? `var(--hl-s-${strategyId})` : "var(--hl-s-other)";
}

// ---------------------------------------------------------------- layout
export function Board({ title, subtitle, actions, footer, children, className, labelledBy }: {
  title?: ReactNode; subtitle?: ReactNode; actions?: ReactNode; footer?: ReactNode; children: ReactNode;
  className?: string; labelledBy?: string;
}) {
  const autoId = useId();
  const id = labelledBy ?? autoId;
  return (
    <section className={cx("hl-board", className)} aria-labelledby={title ? id : undefined}>
      {(title || actions) && (
        <div className="hl-board-bar">
          {title && <h2 id={id}>{title}{subtitle && <small> · {subtitle}</small>}</h2>}
          {actions && <div className="hl-board-actions">{actions}</div>}
        </div>
      )}
      <div className="hl-board-body">{children}</div>
      {footer && <div className="hl-board-foot">{footer}</div>}
    </section>
  );
}

export function Card({ accent, selected, children, className, as: Tag = "article", ...rest }: {
  accent?: string; selected?: boolean; children: ReactNode; className?: string; as?: "article" | "div" | "section";
} & Record<string, unknown>) {
  return (
    <Tag className={cx("hl-card", accent && "hl-card-accent", selected && "hl-card-selected", className)}
         style={accent ? ({ "--accent": accent } as CSSProperties) : undefined} {...rest}>
      {children}
    </Tag>
  );
}

// ---------------------------------------------------------------- bot card
export const STAGES = ["Scan", "Check", "Size", "Execute", "Equity"] as const;
export type Stage = (typeof STAGES)[number];
export type StageState = "off" | "on" | "stop";

export function BotCard({ name, strategyId, strategyName, status, line, stages, net, selected, onSelect, onStage }: {
  name: string; strategyId: string; strategyName?: string; status: string; line?: ReactNode;
  stages?: Partial<Record<Stage, StageState>>; net?: ReactNode; selected?: boolean;
  onSelect?: () => void; onStage?: (s: Stage) => void;
}) {
  return (
    <Card accent={strategyColor(strategyId)} selected={selected} className="hl-botcard" data-testid={`botcard-${name}`}>
      <header>
        <h3>{onSelect ? <button type="button" className="hl-linklike" onClick={onSelect}>{name}</button> : name}</h3>
        <StatusBadge status={status} />
      </header>
      <p className="hl-botcard-strategy"><span className="hl-dot" aria-hidden="true" />{strategyName ?? strategyId}
        {net !== undefined && <span className="hl-botcard-net">{net}</span>}</p>
      {line !== undefined && <p className="hl-botcard-line">{line}</p>}
      <ol className="hl-pipe" aria-label="Pipeline">
        {STAGES.map((s) => {
          const st = stages?.[s] ?? "off";
          const label = <>{s}<span className="hl-sr">{st === "on" ? " (done)" : st === "stop" ? " (stopped)" : ""}</span></>;
          return (
            <li key={s} className={st === "off" ? undefined : st}>
              {onStage ? <button type="button" onClick={() => onStage(s)}>{label}</button> : label}
            </li>
          );
        })}
      </ol>
    </Card>
  );
}

// ---------------------------------------------------------------- status
const TONE: Record<string, "ok" | "warn" | "bad" | "info" | "idle"> = {
  RUNNING: "ok", LIVE: "ok", SUCCEEDED: "ok", PUBLISHED: "ok", ACTIVE: "ok", OPEN: "info", PASS: "ok",
  PAUSED: "warn", STALE: "warn", PENDING: "warn", LIMIT_REACHED: "warn", DRAFT: "idle", READY: "info",
  STOPPED: "idle", CLOSED: "idle", IDLE: "idle", UNKNOWN: "idle", DEPRECATED: "idle", CREATED: "idle",
  FAILED: "bad", ERROR: "bad", DOWN: "bad", OFFLINE: "bad", BUDGET_BLOCKED: "bad", ABANDONED: "bad", REJECTED: "bad",
};

export function StatusBadge({ status, children }: { status: string; children?: ReactNode }) {
  const tone = TONE[status.toUpperCase()] ?? "idle";
  return <span className={`hl-badge hl-tone-${tone}`}><span className="hl-dot" aria-hidden="true" />{children ?? status}</span>;
}

// ---------------------------------------------------------------- numbers
function signClass(v: string | number | null | undefined): string | undefined {
  if (v === null || v === undefined) return undefined;
  const n = typeof v === "number" ? v : Number(v);
  if (!Number.isFinite(n) || n === 0) return undefined;
  return n > 0 ? "hl-gain" : "hl-loss";
}

/** Money from a decimal string; `signed` adds + and the gain/loss colour; unknown ⇒ "—". */
export function Money({ value, signed = false, places = 2 }: { value: string | null | undefined; signed?: boolean; places?: number }) {
  const text = signed ? fmtSignedUsd(value, places) : fmtUsd(value, places);
  return <span className={cx("hl-num", signed && signClass(value))}>{text.replace(/^-/, "−")}</span>;
}

/** Percentage (display only); `signed` adds + and the gain/loss colour; unknown ⇒ "—". */
export function Pct({ value, signed = false, places = 2 }: { value: string | number | null | undefined; signed?: boolean; places?: number }) {
  const n = value === null || value === undefined || value === "" ? NaN : Number(value);
  if (!Number.isFinite(n)) return <span className="hl-num">{DASH}</span>;
  const text = `${signed && n > 0 ? "+" : ""}${n.toFixed(places)}%`.replace(/^-/, "−");
  return <span className={cx("hl-num", signed && signClass(n))}>{text}</span>;
}

/** Any other number or count, mono and tabular; unknown ⇒ "—". */
export function Num({ value, children }: { value?: string | number | null; children?: ReactNode }) {
  return <span className="hl-num">{children ?? (value === null || value === undefined || value === "" ? DASH : String(value))}</span>;
}

// ---------------------------------------------------------------- table
export function DataTable({ children, label, className }: { children: ReactNode; label?: string; className?: string }) {
  return <div className="hl-table-wrap"><table className={cx("hl-table", className)} aria-label={label}>{children}</table></div>;
}

// ---------------------------------------------------------------- controls
export function Button({ variant = "secondary", className, ...rest }: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary" | "danger" | "ghost";
}) {
  return <button type="button" className={cx("hl-btn", variant !== "secondary" && variant, className)} {...rest} />;
}

export function Field({ label, hint, error, children }: { label: ReactNode; hint?: ReactNode; error?: ReactNode; children: ReactNode }) {
  return (
    <label className={cx("hl-field", Boolean(error) && "hl-field-invalid")}>
      <span className="hl-caps">{label}</span>
      {children}
      {hint && !error && <span className="hl-field-hint">{hint}</span>}
      {error && <span className="hl-field-error" role="alert">{error}</span>}
    </label>
  );
}

export const Input = ({ className, ...rest }: InputHTMLAttributes<HTMLInputElement>) =>
  <input className={cx("hl-input", className)} {...rest} />;
export const Select = ({ className, ...rest }: SelectHTMLAttributes<HTMLSelectElement>) =>
  <select className={cx("hl-input", className)} {...rest} />;
export const Textarea = ({ className, ...rest }: TextareaHTMLAttributes<HTMLTextAreaElement>) =>
  <textarea className={cx("hl-input", className)} {...rest} />;

// ---------------------------------------------------------------- dialog
const FOCUSABLE = "a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex='-1'])";

/** Modal dialog: focus moves inside, Tab stays inside, Escape closes, focus returns on close. */
export function Dialog({ title, onClose, children, actions, width = 520 }: {
  title: ReactNode; onClose: () => void; children: ReactNode; actions?: ReactNode; width?: number;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const titleId = useId();
  useEffect(() => {
    const before = document.activeElement as HTMLElement | null;
    const node = ref.current;
    (node?.querySelector<HTMLElement>(FOCUSABLE) ?? node)?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") { e.stopPropagation(); onClose(); return; }
      if (e.key !== "Tab" || !node) return;
      const items = [...node.querySelectorAll<HTMLElement>(FOCUSABLE)];
      if (!items.length) return;
      const first = items[0], last = items[items.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    };
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("keydown", onKey); before?.focus?.(); };
  }, [onClose]);
  return (
    <div className="hl-dialog-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div ref={ref} className="hl-dialog" role="dialog" aria-modal="true" aria-labelledby={titleId}
           tabIndex={-1} style={{ maxWidth: width }}>
        <h2 id={titleId}>{title}</h2>
        <div className="hl-dialog-body">{children}</div>
        {actions && <div className="hl-dialog-actions">{actions}</div>}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- tabs
export function Tabs<T extends string>({ tabs, value, onChange, label }: {
  tabs: { id: T; label: ReactNode }[]; value: T; onChange: (id: T) => void; label: string;
}) {
  return (
    <div className="hl-tabs" role="tablist" aria-label={label}>
      {tabs.map((t) => (
        <button key={t.id} type="button" role="tab" aria-selected={t.id === value}
                className="hl-tab" onClick={() => onChange(t.id)}>{t.label}</button>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------- states and banners
export function EmptyState({ title, children }: { title: ReactNode; children?: ReactNode }) {
  return <div className="hl-state hl-state-empty"><p className="hl-state-title">{title}</p>{children}</div>;
}

export function ErrorState({ title = "Something went wrong", children, onRetry }: {
  title?: ReactNode; children?: ReactNode; onRetry?: () => void;
}) {
  return (
    <div className="hl-state hl-state-error" role="alert">
      <p className="hl-state-title">{title}</p>{children}
      {onRetry && <Button onClick={onRetry}>Retry</Button>}
    </div>
  );
}

export function Banner({ tone = "info", children }: { tone?: "info" | "warn" | "bad" | "ok"; children: ReactNode }) {
  return <div className={`hl-banner hl-tone-${tone}`} role={tone === "bad" ? "alert" : "status"}>{children}</div>;
}
