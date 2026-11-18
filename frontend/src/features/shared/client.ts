// Thin client over the chassis apiFetch for the 0-hl API (spec 10-backend/04).
import { apiFetch } from "../../api";
import type { CommandView } from "./types";

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    public detail: string,
    public correlationId: string | null,
  ) {
    super(`${status} ${code}: ${detail}`);
  }
}

async function parse<T>(res: Response): Promise<T> {
  if (res.ok) return (await res.json()) as T;
  let body: { code?: string; detail?: string; correlation_id?: string } = {};
  try {
    body = await res.json();
  } catch {
    /* not JSON */
  }
  throw new ApiError(
    res.status,
    body.code ?? `HTTP_${res.status}`,
    body.detail ?? res.statusText,
    body.correlation_id ?? res.headers.get("X-Correlation-ID"),
  );
}

export async function getJson<T>(path: string): Promise<T> {
  return parse<T>(await apiFetch(path));
}

export function newIdempotencyKey(): string {
  return typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `k-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

export async function sendCommand(
  botId: string,
  command: string,
  reason: string | null = null,
  idempotencyKey: string = newIdempotencyKey(),
): Promise<CommandView> {
  return parse<CommandView>(
    await apiFetch(`/api/v1/bots/${botId}/commands`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "Idempotency-Key": idempotencyKey },
      body: JSON.stringify({ command, reason }),
    }),
  );
}

/** Polls a command until it is processed by the worker (max `timeoutMs`). */
export async function waitForCommand(
  commandId: string,
  { intervalMs = 1000, timeoutMs = 30000, sleep = (ms: number) => new Promise((r) => setTimeout(r, ms)) } = {},
): Promise<CommandView> {
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    const c = await getJson<CommandView>(`/api/v1/commands/${commandId}`);
    if (c.status !== "PENDING" || Date.now() >= deadline) return c;
    await sleep(intervalMs);
  }
}

const API = import.meta.env.VITE_API_URL ?? "";

export async function refreshAccessToken(): Promise<string | null> {
  const res = await fetch(`${API}/api/auth/refresh`, { method: "POST", credentials: "include" });
  if (!res.ok) return null;
  const data = (await res.json()) as { access_token: string };
  localStorage.setItem("access_token", data.access_token);
  return data.access_token;
}

export function terminalWsUrl(base: string = API, origin: string = window.location.origin): string {
  return `${(base || origin).replace(/^http/, "ws").replace(/\/$/, "")}/ws/v1/terminal`;
}
