/** Staff pure helpers — no React, no network (unit-tested).
 *
 * - `nextBackoffDelayMs`: WebSocket reconnect backoff — 1s, 2s, 4s,
 *   … capped at 30s (api-plan.md § 9.4; same rule as the kitchen
 *   channel).
 * - `buildStaffWsUrl`: the /ws/staff URL. In dev the Vite proxy
 *   serves /ws from the page origin; in production VITE_API_URL is
 *   converted http(s) -> ws(s). The dev key travels as a query param
 *   because browsers cannot set headers on a WS handshake.
 * - `sortOrdersReadyFirst`: active orders, ready ones first (they
 *   are what a waiter can act on now), then oldest first within
 *   each group — the server already returns oldest first.
 * - `statusBadgeForTable`: the floor-grid badge for a table's own
 *   dining-table status (operational flags — orders / help calls —
 *   are separate badges, not folded into this one).
 */

import type { KitchenOrder } from "@/shared/api/types";

export function nextBackoffDelayMs(attempt: number): number {
  return Math.min(1000 * 2 ** Math.max(0, attempt), 30_000);
}

export function buildStaffWsUrl(options: {
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
  const path = `/ws/staff?${params}`;
  if (options.apiBase) {
    const base = options.apiBase.replace(/^http/, "ws").replace(/\/$/, "");
    return `${base}${path}`;
  }
  const origin = options.pageOrigin.replace(/^http/, "ws");
  return `${origin}${path}`;
}

export function sortOrdersReadyFirst(orders: KitchenOrder[]): KitchenOrder[] {
  return [...orders].sort((a, b) => {
    const aReady = a.status === "ready" ? 0 : 1;
    const bReady = b.status === "ready" ? 0 : 1;
    if (aReady !== bReady) return aReady - bReady;
    return a.placedAt.localeCompare(b.placedAt);
  });
}

export function statusBadgeForTable(
  status: string,
): { label: string; className: string } {
  switch (status) {
    case "occupied":
      return {
        label: "Occupied",
        className: "bg-amber-100 text-amber-900",
      };
    case "cleaning":
      return {
        label: "Cleaning",
        className: "bg-sky-100 text-sky-900",
      };
    case "out_of_service":
      return {
        label: "Out of service",
        className: "bg-red-100 text-red-900",
      };
    case "available":
    default:
      return {
        label: "Available",
        className: "bg-green-100 text-green-900",
      };
  }
}
