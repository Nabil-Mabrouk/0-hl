// Display formatting (spec 03-ecrans/M1-terminal-et-bots « Accès et états communs »).
// Money is rounded on the decimal STRING with BigInt — never through a float.
// Unknown values (null/undefined) always render as "—", never as 0.

export const DASH = "—";

function splitDecimal(s: string): { neg: boolean; int: string; frac: string } | null {
  const m = /^(-?)(\d+)(?:\.(\d+))?$/.exec(s.trim());
  if (!m) return null;
  return { neg: m[1] === "-", int: m[2], frac: m[3] ?? "" };
}

/** Round a decimal string half-up to `places` decimals, exactly. */
export function roundDecimal(s: string, places: number): string | null {
  const p = splitDecimal(s);
  if (!p) return null;
  const frac = (p.frac + "0".repeat(places + 1)).slice(0, places + 1);
  let scaled = BigInt(p.int + frac.slice(0, places));
  if (Number(frac[places]) >= 5) scaled += 1n;
  const digits = scaled.toString().padStart(places + 1, "0");
  const intPart = places ? digits.slice(0, -places) : digits;
  const fracPart = places ? digits.slice(-places) : "";
  const isZero = /^0*$/.test(intPart + fracPart);
  const sign = p.neg && !isZero ? "-" : "";
  return sign + intPart + (places ? "." + fracPart : "");
}

function group(int: string): string {
  return int.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
}

export function fmtUsd(s: string | null | undefined, places = 2): string {
  if (s === null || s === undefined) return DASH;
  const r = roundDecimal(s, places);
  if (r === null) return DASH;
  const neg = r.startsWith("-");
  const [i, f] = (neg ? r.slice(1) : r).split(".");
  return `${neg ? "-" : ""}$${group(i)}${f !== undefined ? "." + f : ""}`;
}

export function fmtSignedUsd(s: string | null | undefined, places = 2): string {
  if (s === null || s === undefined) return DASH;
  const v = fmtUsd(s, places);
  return v.startsWith("-") || v === DASH || /^\$0(\.0+)?$/.test(v) ? v : `+${v}`;
}

/** Price/quantity: exact digits, trailing zeros trimmed, at most `maxPlaces` decimals
 * (12 by default: memecoin prices such as 0.000003767 must not lose digits). */
export function fmtNum(s: string | null | undefined, maxPlaces = 12): string {
  if (s === null || s === undefined) return DASH;
  const r = roundDecimal(s, maxPlaces);
  if (r === null) return DASH;
  return r.includes(".") ? r.replace(/0+$/, "").replace(/\.$/, "") : r;
}

/** Ratio/percent for display only (not money): float is acceptable here. */
export function fmtPct(x: number | null | undefined, places = 1, signed = false): string {
  if (x === null || x === undefined || !Number.isFinite(x)) return DASH;
  const v = x.toFixed(places);
  return `${signed && x > 0 ? "+" : ""}${v}%`;
}

export function ratio(a: string | null | undefined, b: string | null | undefined): number | null {
  if (a === null || a === undefined || b === null || b === undefined) return null;
  const den = Number(b);
  if (!den) return null;
  return Number(a) / den;
}

export function fmtUptime(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || seconds < 0) return DASH;
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  return [h, m, s].map((n) => String(n).padStart(2, "0")).join(":");
}

export function fmtTime(iso: string | null | undefined): string {
  if (!iso) return DASH;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return DASH;
  return `${String(d.getUTCHours()).padStart(2, "0")}:${String(d.getUTCMinutes()).padStart(2, "0")}`;
}

export function symbolOf(instrumentId: string | null | undefined): string {
  if (!instrumentId) return DASH;
  const sym = instrumentId.includes(":") ? instrumentId.split(":")[1] : instrumentId;
  return `$${sym.replace(/\/USD$/, "")}`;
}
