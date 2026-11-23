/** Staff API — the only place staff endpoints are called.
 *
 * Every function goes through the shared client (no second client).
 * Staff auth is dev-only in Stage 5 (api-plan.md § 6): the
 * ``X-Staff-Dev-Key`` header must match the backend
 * ``STAFF_DEV_KEY``. The key comes from ``VITE_STAFF_DEV_KEY``
 * (see .env.example); if it is missing the UI shows a clear
 * "staff access not configured" state — never a stack trace.
 * In production these endpoints return 503 until staff auth lands.
 */
import { api } from "@/shared/api/client";
import type {
  KitchenOrder,
  StaffHelpCall,
  StaffHelpCallListResponse,
  StaffOrderListResponse,
  StaffRestaurant,
  StaffTableDetailResponse,
  StaffTableListResponse,
} from "@/shared/api/types";

const V1 = "/api/v1";

/** The dev key, or undefined when it is not configured. */
export function getStaffDevKey(): string | undefined {
  const key = import.meta.env.VITE_STAFF_DEV_KEY as string | undefined;
  return key && key.trim() !== "" ? key : undefined;
}

export function isStaffConfigured(): boolean {
  return getStaffDevKey() !== undefined;
}

function staffHeaders(): Record<string, string> {
  const key = getStaffDevKey();
  return key ? { "X-Staff-Dev-Key": key } : {};
}

/** Dev-only restaurant picker (GET /staff/restaurants). */
export function fetchStaffRestaurants() {
  return api.get<{ data: StaffRestaurant[] }>(`${V1}/staff/restaurants`, {
    headers: staffHeaders(),
  });
}

/** The floor grid (GET /staff/tables). */
export function fetchStaffTables(restaurantId: string) {
  return api.get<StaffTableListResponse>(
    `${V1}/staff/tables?restaurant_id=${encodeURIComponent(restaurantId)}`,
    { headers: staffHeaders() },
  );
}

/** One table's detail (GET /staff/tables/:id). */
export function fetchStaffTableDetail(restaurantId: string, tableId: string) {
  return api.get<StaffTableDetailResponse>(
    `${V1}/staff/tables/${encodeURIComponent(tableId)}?restaurant_id=${encodeURIComponent(restaurantId)}`,
    { headers: staffHeaders() },
  );
}

/** Active orders, oldest first (GET /staff/orders). */
export function fetchStaffOrders(
  restaurantId: string,
  status?: "placed" | "accepted" | "preparing" | "ready",
  page = 1,
  perPage = 20,
) {
  const params = new URLSearchParams({
    restaurant_id: restaurantId,
    page: String(page),
    per_page: String(perPage),
  });
  if (status) params.set("status", status);
  return api.get<StaffOrderListResponse>(`${V1}/staff/orders?${params}`, {
    headers: staffHeaders(),
  });
}

/** Serve a ready order (PATCH /staff/orders/:id/serve, empty body).
 * Only ready -> served; anything else is a 409. Serving does NOT
 * close the session or change the table status. */
export function serveStaffOrder(orderId: string, restaurantId: string) {
  return api.patch<{ data: KitchenOrder }>(
    `${V1}/staff/orders/${encodeURIComponent(orderId)}/serve?restaurant_id=${encodeURIComponent(restaurantId)}`,
    {},
    { headers: staffHeaders() },
  );
}

/** Unresolved help calls, oldest first (GET /staff/help-calls). */
export function fetchStaffHelpCalls(
  restaurantId: string,
  status?: "open" | "acknowledged" | "resolved" | "cancelled",
) {
  const params = new URLSearchParams({ restaurant_id: restaurantId });
  if (status) params.set("status", status);
  return api.get<StaffHelpCallListResponse>(
    `${V1}/staff/help-calls?${params}`,
    { headers: staffHeaders() },
  );
}

/** Acknowledge a help call (PATCH /staff/help-calls/:id/acknowledge).
 * Only open -> acknowledged; anything else is a 409. */
export function acknowledgeHelpCall(callId: string, restaurantId: string) {
  return api.patch<{ data: StaffHelpCall }>(
    `${V1}/staff/help-calls/${encodeURIComponent(callId)}/acknowledge?restaurant_id=${encodeURIComponent(restaurantId)}`,
    {},
    { headers: staffHeaders() },
  );
}

/** Resolve a help call (PATCH /staff/help-calls/:id/resolve).
 * Only acknowledged -> resolved; resolving an open call is a 409 —
 * a waiter must acknowledge first. */
export function resolveHelpCall(callId: string, restaurantId: string) {
  return api.patch<{ data: StaffHelpCall }>(
    `${V1}/staff/help-calls/${encodeURIComponent(callId)}/resolve?restaurant_id=${encodeURIComponent(restaurantId)}`,
    {},
    { headers: staffHeaders() },
  );
}
