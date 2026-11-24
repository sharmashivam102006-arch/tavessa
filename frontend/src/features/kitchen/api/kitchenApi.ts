/** Kitchen API — the only place kitchen endpoints are called.
 *
 * Every function goes through the shared client (no second client).
 * Kitchen auth is dev-only in Stage 4 (api-plan.md § 6): the
 * ``X-Kitchen-Dev-Key`` header must match the backend
 * ``KITCHEN_DEV_KEY``. The key comes from ``VITE_KITCHEN_DEV_KEY``
 * (see .env.example); if it is missing the UI shows a clear
 * "kitchen access not configured" state — never a stack trace.
 * In production these endpoints return 503 until staff auth lands.
 */
import { api } from "@/shared/api/client";
import { useAuthStore } from "@/shared/stores/authStore";
import type {
  KitchenOrder,
  KitchenOrderListResponse,
  KitchenRestaurant,
  KitchenStatusFilter,
} from "@/shared/api/types";

const V1 = "/api/v1";

/** The dev key, or undefined when it is not configured.
 *
 * Stage 7: dev keys are sent ONLY in DEV builds — the code is
 * gated so a production build never includes or sends them. In
 * production the kitchen/staff pages require a JWT login instead.
 */
export function getKitchenDevKey(): string | undefined {
  if (!import.meta.env.DEV) return undefined;
  const key = import.meta.env.VITE_KITCHEN_DEV_KEY as string | undefined;
  return key && key.trim() !== "" ? key : undefined;
}

export function isKitchenConfigured(): boolean {
  if (import.meta.env.DEV) return getKitchenDevKey() !== undefined;
  return useAuthStore.getState().accessToken !== null;
}

function kitchenHeaders(): Record<string, string> {
  const key = getKitchenDevKey();
  return key ? { "X-Kitchen-Dev-Key": key } : {};
}

/** Dev-only restaurant picker (GET /kitchen/restaurants). */
export function fetchKitchenRestaurants() {
  return api.get<{ data: KitchenRestaurant[] }>(`${V1}/kitchen/restaurants`, {
    headers: kitchenHeaders(),
  });
}

/** The active queue, oldest first (GET /kitchen/orders). */
export function fetchKitchenOrders(
  restaurantId: string,
  status?: KitchenStatusFilter,
  page = 1,
  perPage = 20,
) {
  const params = new URLSearchParams({
    restaurant_id: restaurantId,
    page: String(page),
    per_page: String(perPage),
  });
  if (status) params.set("status", status);
  return api.get<KitchenOrderListResponse>(`${V1}/kitchen/orders?${params}`, {
    headers: kitchenHeaders(),
  });
}

/** One order for the KDS (GET /kitchen/orders/:id). */
export function fetchKitchenOrder(restaurantId: string, orderId: string) {
  return api.get<{ data: KitchenOrder }>(
    `${V1}/kitchen/orders/${encodeURIComponent(orderId)}?restaurant_id=${encodeURIComponent(restaurantId)}`,
    { headers: kitchenHeaders() },
  );
}

/** Move an order one kitchen step (PATCH /kitchen/orders/:id/status).
 * Only "preparing" | "ready" — anything else is a 422. */
export function patchKitchenOrderStatus(
  orderId: string,
  restaurantId: string,
  status: "preparing" | "ready",
) {
  return api.patch<{ data: KitchenOrder }>(
    `${V1}/kitchen/orders/${encodeURIComponent(orderId)}/status?restaurant_id=${encodeURIComponent(restaurantId)}`,
    { status },
    { headers: kitchenHeaders() },
  );
}
