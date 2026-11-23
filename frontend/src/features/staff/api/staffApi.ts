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
import { api, createIdempotencyKey } from "@/shared/api/client";
import type {
  Bill,
  KitchenOrder,
  Receipt,
  StaffBillListResponse,
  StaffHelpCall,
  StaffHelpCallListResponse,
  StaffOrderListResponse,
  StaffPaymentListResponse,
  StaffPaymentResult,
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

/* ------------------------------------------------------------------ */
/* Billing (Stage 6: GET /staff/bills, cash confirmation, receipts)   */
/* ------------------------------------------------------------------ */

/** The bill queue (GET /staff/bills): payment_requested first,
 * then issued, newest first. */
export function fetchStaffBills(
  restaurantId: string,
  status?: "issued" | "payment_requested" | "paid",
  page = 1,
  perPage = 20,
) {
  const params = new URLSearchParams({
    restaurant_id: restaurantId,
    page: String(page),
    per_page: String(perPage),
  });
  if (status) params.set("status", status);
  return api.get<StaffBillListResponse>(`${V1}/staff/bills?${params}`, {
    headers: staffHeaders(),
  });
}

/** One bill in full (GET /staff/bills/:id): items, splits, payment
 * history, totals, outstanding. */
export function fetchStaffBill(restaurantId: string, billId: string) {
  return api.get<{ data: Bill }>(
    `${V1}/staff/bills/${encodeURIComponent(billId)}?restaurant_id=${encodeURIComponent(restaurantId)}`,
    { headers: staffHeaders() },
  );
}

/** Confirm cash collection (POST /staff/bills/:id/payments).
 * Cash only. The idempotency key is generated per confirmation
 * ATTEMPT and reused across retries of the same attempt, so a
 * double-tap or a flaky retry cannot create two payments. */
export function recordStaffPayment(
  restaurantId: string,
  billId: string,
  body: { amountPaise: number; billSplitId?: string; note?: string },
  idempotencyKey: string = createIdempotencyKey(),
) {
  return api.post<{ data: StaffPaymentResult }>(
    `${V1}/staff/bills/${encodeURIComponent(billId)}/payments?restaurant_id=${encodeURIComponent(restaurantId)}`,
    body,
    {
      headers: { ...staffHeaders(), "Idempotency-Key": idempotencyKey },
    },
  );
}

/** Payment history for a bill, newest first
 * (GET /staff/bills/:id/payments). */
export function fetchStaffBillPayments(restaurantId: string, billId: string) {
  return api.get<StaffPaymentListResponse>(
    `${V1}/staff/bills/${encodeURIComponent(billId)}/payments?restaurant_id=${encodeURIComponent(restaurantId)}`,
    { headers: staffHeaders() },
  );
}

/** Receipt data (GET /staff/bills/:id/receipt). Only after the bill
 * is paid — otherwise a 409. */
export function fetchStaffBillReceipt(restaurantId: string, billId: string) {
  return api.get<{ data: Receipt }>(
    `${V1}/staff/bills/${encodeURIComponent(billId)}/receipt?restaurant_id=${encodeURIComponent(restaurantId)}`,
    { headers: staffHeaders() },
  );
}
