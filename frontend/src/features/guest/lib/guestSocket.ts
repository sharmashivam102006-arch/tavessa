/** Guest WebSocket pure helpers — no React, no network (unit-tested).
 *
 * - `nextGuestBackoffDelayMs`: reconnect backoff WITH jitter —
 *   base 1s, 2s, 4s, … capped at 30s, then jittered ±25% so a fleet
 *   of guest phones that all drop at once does not reconnect in
 *   lock-step (api-plan.md § 9.4, Stage 8 decision 3).
 * - `buildGuestWsUrl`: the /ws/guest URL. In dev the Vite proxy
 *   serves /ws from the page origin; in production VITE_API_URL is
 *   converted http(s) -> ws(s). When VITE_WS_URL is set it takes
 *   precedence: the socket connects directly to that host
 *   (production: wss://tavessa-api.onrender.com) instead of through
 *   the Vercel proxy — Stage 10.2 decision, see docs/deployment.md
 *   §6. The single-use ticket travels as a query param because
 *   browsers cannot set headers on a WS handshake. The ticket is
 *   never logged.
 */

export function nextGuestBackoffDelayMs(attempt: number): number {
  const base = Math.min(1000 * 2 ** Math.max(0, attempt), 30_000);
  const jittered = base * (0.75 + Math.random() * 0.5);
  return Math.floor(Math.min(jittered, 30_000));
}

export function buildGuestWsUrl(options: {
  /** VITE_API_URL ("" in dev — the Vite proxy serves /ws). */
  apiBase: string;
  /** window.location.origin in the browser; explicit in tests. */
  pageOrigin: string;
  /** Single-use Stage 8 ticket from POST /guest/ws/tickets. */
  ticket: string;
  /** Optional VITE_WS_URL. When set, the socket connects directly
   * to this host (production: wss://tavessa-api.onrender.com) and
   * takes precedence over apiBase; when empty, the existing
   * same-origin fallback applies (local dev). */
  wsBase?: string;
}): string {
  const params = new URLSearchParams({ ticket: options.ticket });
  const path = `/ws/guest?${params}`;
  const base = options.wsBase || options.apiBase;
  if (base) {
    const ws = base.replace(/^http/, "ws").replace(/\/$/, "");
    return `${ws}${path}`;
  }
  const origin = options.pageOrigin.replace(/^http/, "ws");
  return `${origin}${path}`;
}
