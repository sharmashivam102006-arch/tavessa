/**
 * API client — one fetch wrapper for the whole app.
 *
 * - Base URL from VITE_API_URL (production) or the Vite dev proxy
 *   (relative /api in development, see vite.config.ts).
 * - Parses the backend error envelope { error: { code, message } }.
 * - Never sends prices/totals: the backend recomputes them (Stage 3+).
 * - Per-request headers (Stage 2): pass `headers` for X-Table-Token
 *   and Idempotency-Key — see guestApi.ts for the call sites.
 * - Stage 7 auth: attaches the in-memory Bearer token when present
 *   and sends cookies (the HttpOnly refresh cookie) on every
 *   request. On a 401 from an authenticated call it tries ONE
 *   silent refresh (POST /api/v1/auth/refresh) and retries once;
 *   if the refresh fails the auth state is cleared and the caller
 *   gets the 401 (route guards redirect to /login).
 */
import type { ApiErrorBody } from "./types";
import { useAuthStore, type AuthUser } from "@/shared/stores/authStore";

const API_BASE = import.meta.env.VITE_API_URL ?? "";

export class ApiError extends Error {
  status: number;
  code: string;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

export interface RequestOptions {
  headers?: Record<string, string>;
  /** Skip the silent-refresh retry (used by the refresh call itself). */
  noRefresh?: boolean;
}

let refreshInFlight: Promise<boolean> | null = null;

async function tryRefresh(): Promise<boolean> {
  if (refreshInFlight) return refreshInFlight;
  refreshInFlight = (async () => {
    try {
      const response = await fetch(`${API_BASE}/api/v1/auth/refresh`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: "{}",
      });
      if (!response.ok) return false;
      const body = (await response.json()) as {
        data?: { accessToken?: string; user?: AuthUser };
      };
      const token = body.data?.accessToken;
      const user = body.data?.user;
      if (!token || !user) return false;
      useAuthStore.getState().setAuth(token, user);
      return true;
    } catch {
      return false;
    } finally {
      refreshInFlight = null;
    }
  })();
  return refreshInFlight;
}

async function request<T>(
  path: string,
  init?: RequestInit,
  options?: RequestOptions,
): Promise<T> {
  const doFetch = async (): Promise<Response> => {
    const token = useAuthStore.getState().accessToken;
    return fetch(`${API_BASE}${path}`, {
      ...init,
      credentials: "include",
      headers: {
        "Content-Type": "application/json",
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...options?.headers,
        ...init?.headers,
      },
    });
  };

  let response = await doFetch();
  if (
    response.status === 401 &&
    !options?.noRefresh &&
    !path.startsWith("/api/v1/auth/") &&
    useAuthStore.getState().accessToken
  ) {
    if (await tryRefresh()) {
      response = await doFetch();
    } else {
      useAuthStore.getState().clearAuth(true);
    }
  }
  if (!response.ok) {
    let code = "request_failed";
    let message = `Request failed (${response.status}).`;
    try {
      const body = (await response.json()) as ApiErrorBody;
      code = body.error?.code ?? code;
      message = body.error?.message ?? message;
    } catch {
      // Non-JSON error body — keep the generic message.
    }
    throw new ApiError(response.status, code, message);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export const api = {
  get: <T>(path: string, options?: RequestOptions) =>
    request<T>(path, undefined, options),
  post: <T>(path: string, body?: unknown, options?: RequestOptions) =>
    request<T>(path, { method: "POST", body: JSON.stringify(body ?? {}) }, options),
  patch: <T>(path: string, body?: unknown, options?: RequestOptions) =>
    request<T>(path, { method: "PATCH", body: JSON.stringify(body ?? {}) }, options),
  delete: <T>(path: string, options?: RequestOptions) =>
    request<T>(path, { method: "DELETE" }, options),
};

/** One UUID per order-placement attempt (api-plan.md § 10).
 * Reused on retry of the same attempt so a double-tap creates
 * exactly one order server-side. */
export function createIdempotencyKey(): string {
  return crypto.randomUUID();
}

/** Map a guest-session failure to a UI state. The backend contract
 * (api-plan.md § 3): unknown/revoked token → 401 `invalid_table_token`,
 * expired session → 410 `session_expired`. Anything else (including a
 * 404 because the endpoint does not exist yet) is "not connected". */
export type SessionErrorKind =
  | "invalid_token"
  | "expired"
  | "not_connected"
  | "forbidden";

export function mapSessionError(error: unknown): SessionErrorKind {
  if (error instanceof ApiError) {
    if (error.status === 401) return "invalid_token";
    if (error.status === 410) return "expired";
    if (error.status === 403) return "forbidden";
  }
  return "not_connected";
}
