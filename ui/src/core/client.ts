/**
 * The API client — one fetch wrapper, with auth refresh and typed errors.
 *
 * Base URL is relative (`/api`) so the browser is always same-origin: in
 * development Vite proxies that prefix to the API (vite.config.ts), in
 * production nginx already serves the same path. That avoids CORS entirely
 * and means no build-time URL baked into the bundle.
 */

import {
  clearTokens,
  getAccessToken,
  getRefreshToken,
  setAccessToken,
  setRefreshToken,
} from "./auth";

export const API_BASE = "/api";

/**
 * A structured API failure. The server's own error envelope
 * (`{error, message, ...}`) is preserved rather than flattened to a string,
 * because several of its fields are genuinely useful to render — most notably
 * `candidates` on an ambiguous-area error, which lists exactly which areas the
 * caller should pick between (docs/AREA_CODE_MIGRATION_ANALYSIS.md).
 */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string | null;
  readonly body: Record<string, unknown> | null;

  constructor(status: number, message: string, code: string | null, body: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.body = (body ?? null) as Record<string, unknown> | null;
  }

  /** An old area code that DLD split into several — the user must choose one. */
  get ambiguousCandidates(): { id: number; name_en: string; dld_area_code: string | null }[] {
    if (this.code !== "ambiguous_entity") return [];
    const c = this.body?.candidates;
    return Array.isArray(c) ? c : [];
  }
}

export type QueryValue = string | number | boolean | null | undefined;

/** Drops null/undefined/"" so absent filters never become `?x=` noise. */
export function toQueryString(params: Record<string, QueryValue>): string {
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === null || v === undefined || v === "") continue;
    sp.set(k, String(v));
  }
  const s = sp.toString();
  return s ? `?${s}` : "";
}

async function parseBody(res: Response): Promise<unknown> {
  const text = await res.text();
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

function errorFrom(status: number, body: unknown): ApiError {
  if (body && typeof body === "object") {
    const b = body as Record<string, unknown>;
    // The API's own envelope: {error: "ambiguous_entity", message: "..."}.
    if (typeof b.message === "string") {
      return new ApiError(status, b.message, (b.error as string) ?? null, b);
    }
    // FastAPI's validation envelope: {detail: [...] | "..."}.
    if (typeof b.detail === "string") {
      return new ApiError(status, b.detail, null, b);
    }
    if (Array.isArray(b.detail)) {
      const first = b.detail[0] as { msg?: string; loc?: unknown[] } | undefined;
      const where = Array.isArray(first?.loc) ? ` (${first.loc.join(".")})` : "";
      return new ApiError(status, `${first?.msg ?? "Invalid request"}${where}`, null, b);
    }
  }
  return new ApiError(status, `Request failed with status ${status}`, null, body);
}

interface TokenResponse {
  access_token: string;
  refresh_token?: string | null;
  token_type?: string;
  expires_in?: number;
}

/**
 * Single-flight refresh: many queries can 401 at once (React Query refetches
 * several panels together), and each must not fire its own refresh — that
 * would race and invalidate the others' brand-new token. The first caller
 * starts the refresh; the rest await the same promise.
 */
let refreshInFlight: Promise<boolean> | null = null;

async function refreshAccessToken(): Promise<boolean> {
  const refresh = getRefreshToken();
  if (!refresh) return false;

  if (!refreshInFlight) {
    refreshInFlight = (async () => {
      try {
        const res = await fetch(`${API_BASE}/auth/refresh`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ refresh_token: refresh }),
        });
        if (!res.ok) {
          clearTokens();
          return false;
        }
        const data = (await res.json()) as TokenResponse;
        setAccessToken(data.access_token);
        // /auth/refresh does not return a new refresh token; keep the stored one.
        if (data.refresh_token) setRefreshToken(data.refresh_token);
        return true;
      } catch {
        return false;
      } finally {
        refreshInFlight = null;
      }
    })();
  }
  return refreshInFlight;
}

async function rawRequest(path: string, init: RequestInit): Promise<Response> {
  const token = getAccessToken();
  const headers = new Headers(init.headers);
  headers.set("Accept", "application/json");
  if (token) headers.set("Authorization", `Bearer ${token}`);
  return fetch(`${API_BASE}${path}`, { ...init, headers });
}

/**
 * Fetch JSON from the API, refreshing once on a 401 and retrying.
 *
 * The retry is deliberately capped at one attempt: if the refreshed token is
 * also rejected, the session is genuinely over and looping would just hammer
 * the endpoint.
 */
export async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  let res = await rawRequest(path, init);

  if (res.status === 401 && getRefreshToken()) {
    const ok = await refreshAccessToken();
    if (ok) res = await rawRequest(path, init);
  }

  const body = await parseBody(res);
  if (!res.ok) throw errorFrom(res.status, body);
  return body as T;
}

export function apiGet<T>(path: string, params: Record<string, QueryValue> = {}): Promise<T> {
  return apiFetch<T>(`${path}${toQueryString(params)}`);
}

export async function login(username: string, password: string): Promise<void> {
  const res = await fetch(`${API_BASE}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  const body = await parseBody(res);
  if (!res.ok) throw errorFrom(res.status, body);
  const data = body as TokenResponse;
  setAccessToken(data.access_token);
  if (data.refresh_token) setRefreshToken(data.refresh_token);
}

export function logout(): void {
  clearTokens();
}

/**
 * Restore a session on page load. The access token died with the last tab, so
 * this trades the stored refresh token for a fresh one. Returns false when
 * there is nothing stored or the token has expired — the caller shows login.
 */
export async function restoreSession(): Promise<boolean> {
  if (getAccessToken()) return true;
  if (!getRefreshToken()) return false;
  return refreshAccessToken();
}
