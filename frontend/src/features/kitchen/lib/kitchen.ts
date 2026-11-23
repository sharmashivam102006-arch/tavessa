/** Kitchen pure helpers — no React, no network (unit-tested).
 *
 * - `formatElapsed`: mm:ss elapsed time. No SLA/breach flag: no
 *   threshold is configured (decision D7 open — see the roadmap).
 * - `columnForStatus`: which board column an order belongs in.
 *   placed/accepted -> New; preparing -> Preparing; ready -> Ready;
 *   terminal states (served/completed/cancelled) -> null (off-board).
 * - `nextBackoffDelayMs`: WebSocket reconnect backoff — 1s, 2s, 4s,
 *   … capped at 30s (api-plan.md § 9.4).
 * - `buildKitchenWsUrl`: the /ws/kitchen URL. In dev the Vite proxy
 *   serves /ws from the page origin; in production VITE_API_URL is
 *   converted http(s) -> ws(s). The dev key travels as a query param
 *   because browsers cannot set headers on a WS handshake.
 */

import type {
  KitchenColumnId,
  OrderStatus,
} from "@/shared/api/types";

export function formatElapsed(totalSeconds: number): string {
  const seconds = Math.max(0, Math.floor(totalSeconds));
  const minutes = Math.floor(seconds / 60);
  const remainder = seconds % 60;
  return `${String(minutes).padStart(2, "0")}:${String(remainder).padStart(2, "0")}`;
}

export function columnForStatus(status: OrderStatus): KitchenColumnId | null {
  switch (status) {
    case "placed":
    case "accepted":
      return "new";
    case "preparing":
      return "preparing";
    case "ready":
      return "ready";
    default:
      return null;
  }
}

/** Reconnect delay for attempt N (0-based): 1s, 2s, 4s, … max 30s. */
export function nextBackoffDelayMs(attempt: number): number {
  return Math.min(1000 * 2 ** Math.max(0, attempt), 30_000);
}

export function buildKitchenWsUrl(options: {
  /** VITE_API_URL ("" in dev — the Vite proxy serves /ws). */
  apiBase: string;
  /** window.location.origin in the browser; explicit in tests. */
  pageOrigin: string;
  restaurantId: string;
  devKey: string;
}): string {
  const params = new URLSearchParams({
    restaurant_id: options.restaurantId,
    dev_key: options.devKey,
  });
  const path = `/ws/kitchen?${params}`;
  if (options.apiBase) {
    const base = options.apiBase.replace(/^http/, "ws").replace(/\/$/, "");
    return `${base}${path}`;
  }
  const origin = options.pageOrigin.replace(/^http/, "ws");
  return `${origin}${path}`;
}
