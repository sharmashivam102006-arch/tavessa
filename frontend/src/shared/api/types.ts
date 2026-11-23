/** Shared API types — Stage 1: health only; Stage 2: guest contracts.
 *
 * Guest/menu/order/bill/help types below are hand-written to match
 * docs/api-plan.md (§§ 3, 4, 5, 7) field-for-field.
 * TODO (Stage 3): generate these from the backend OpenAPI schema
 * (openapi-typescript) and delete the hand-written copies. They are
 * not a permanent alternative to generated types.
 *
 * Money is integer paise in every type. The frontend never sends a
 * price or total — see OrderPlacementRequest (no price fields). */

export interface HealthResponse {
  status: "ok" | "degraded";
  database: "ok" | "unavailable";
  environment: string;
  version: string;
}

export interface ApiErrorBody {
  error: {
    code: string;
    message: string;
    details?: Record<string, unknown>;
  };
}

/* ------------------------------------------------------------------ */
/* Menu (api-plan.md § 3: GET /guest/menu; § 8: GET /menu/*)          */
/* ------------------------------------------------------------------ */

export interface MenuCategory {
  id: string;
  name: string;
  description?: string;
  sortOrder: number;
  isActive: boolean;
}

export interface ItemVariant {
  id: string;
  name: string;
  /** Price added to the item base price, in paise. 0 for no-price
   * options such as a spice level. */
  priceDeltaPaise: number;
  isAvailable: boolean;
}

export interface AddOn {
  id: string;
  name: string;
  pricePaise: number;
  isAvailable: boolean;
}

export type KitchenStation = "Main Kitchen" | "Tandoor" | "Beverages" | "Dessert";

export interface MenuItem {
  id: string;
  categoryId: string;
  name: string;
  description: string;
  /** Base price in paise. The backend recomputes every price from
   * the menu at order time; this value is display-only. */
  pricePaise: number;
  isVeg: boolean;
  isAvailable: boolean;
  isPopular: boolean;
  imageUrl?: string;
  station: KitchenStation;
  /** Label for the single variant group, e.g. "Size" or "Spice level".
   * Absent when the item has no variants. */
  variantGroupLabel?: string;
  variants?: ItemVariant[];
  addOns?: AddOn[];
}

export interface GuestMenuResponse {
  categories: MenuCategory[];
  items: MenuItem[];
}

/* ------------------------------------------------------------------ */
/* Guest session (api-plan.md § 3)                                     */
/* ------------------------------------------------------------------ */

export interface GuestSession {
  id: string;
  restaurantId: string;
  tableId: string;
  /** Table number as shown to the guest — never a database id. */
  tableNumber: number;
  status: "active" | "billing_requested" | "closed" | "expired";
  openedAt: string;
}

export interface RestaurantInfo {
  id: string;
  name: string;
  tagline?: string;
  cuisine?: string;
  address?: string;
  openingHours?: string;
  phone?: string;
  rating?: number;
}

export interface GuestSessionResponse {
  session: GuestSession;
  restaurant: RestaurantInfo;
  table: { number: number };
  menuUrl: string;
}

/* ------------------------------------------------------------------ */
/* Orders (api-plan.md § 4)                                            */
/* ------------------------------------------------------------------ */

/** Order statuses shown in the guest tracker. api-plan.md § 4.2 uses
 * `preparing` for the cooking step (the task brief calls it "Cooking");
 * `accepted` is the kitchen-acknowledged step. `completed` is a
 * staff-side terminal state and is not a guest-tracking step. */
export type OrderStatus =
  | "placed"
  | "accepted"
  | "preparing"
  | "ready"
  | "served"
  | "completed"
  | "cancelled";

export const ORDER_STATUS_LABELS: Record<OrderStatus, string> = {
  placed: "Placed",
  accepted: "Accepted",
  preparing: "Cooking",
  ready: "Ready",
  served: "Served",
  completed: "Completed",
  cancelled: "Cancelled",
};

/** The five forward steps of the guest tracking timeline, in order.
 * `cancelled` is a terminal branch, not a step. */
export const ORDER_TRACKING_STEPS: OrderStatus[] = [
  "placed",
  "accepted",
  "preparing",
  "ready",
  "served",
];

export interface OrderPlacementItem {
  menuItemId: string;
  variantId?: string;
  addOnIds?: string[];
  quantity: number;
  specialInstructions?: string;
  /** NOTE: no price fields — the backend recomputes every price
   * from the menu (api-plan.md § 4.1). Any price sent is ignored. */
}

export interface OrderPlacementRequest {
  items: OrderPlacementItem[];
}

export interface OrderItemSnapshot {
  id: string;
  menuItemId: string;
  name: string;
  variantName?: string;
  addOnNames?: string[];
  quantity: number;
  unitPricePaise: number;
  totalPaise: number;
  specialInstructions?: string;
  status: "queued" | "preparing" | "ready" | "served" | "cancelled";
}

export interface Order {
  id: string;
  orderNumber: string;
  tableSessionId: string;
  status: OrderStatus;
  items: OrderItemSnapshot[];
  subtotalPaise: number;
  /** Tax is undecided (decision D1). Until Stage 3/6 settles it,
   * the backend returns 0 and the UI says so explicitly. */
  taxPaise: number;
  totalPaise: number;
  placedAt: string;
  acceptedAt?: string;
  readyAt?: string;
  servedAt?: string;
  cancelledAt?: string;
}

export interface OrderPlacementResponse {
  order: Order;
}

/* ------------------------------------------------------------------ */
/* Help calls (api-plan.md § 5: POST /guest/help-calls)                */
/* ------------------------------------------------------------------ */

/** Guest-facing assistance types. api-plan.md § 5 lists the request
 * body as `{ "note": "…" }` only; `type` is a Stage 2 contract
 * proposal for Stage 3 — the client sends both, and the note always
 * carries the type label in plain text so nothing is lost if the
 * backend only reads `note` today. */
export type HelpCallType = "waiter" | "water" | "plates" | "order_help";

export const HELP_CALL_LABELS: Record<HelpCallType, string> = {
  waiter: "Request a waiter",
  water: "Request water",
  plates: "Request plates / cutlery",
  order_help: "Help with my order",
};

export interface HelpCallRequest {
  type: HelpCallType;
  note?: string;
}

export interface HelpCall {
  id: string;
  tableSessionId: string;
  type?: HelpCallType;
  note?: string;
  status: "open" | "acknowledged" | "resolved" | "cancelled";
  createdAt: string;
}

/* ------------------------------------------------------------------ */
/* Bills & splits (api-plan.md § 7) — cash only in v1                  */
/* ------------------------------------------------------------------ */

export type BillStatus =
  | "issued"
  | "payment_requested"
  | "paid"
  | "partially_paid"
  | "voided";

export interface BillItem {
  id: string;
  name: string;
  quantity: number;
  unitPricePaise: number;
  totalPaise: number;
}

export interface BillSplit {
  id: string;
  label?: string;
  amountPaise: number;
  status: "unpaid" | "payment_requested" | "paid";
}

export interface Bill {
  id: string;
  tableSessionId: string;
  status: BillStatus;
  items: BillItem[];
  subtotalPaise: number;
  /** Tax undecided (D1) — 0 until the backend configures it. */
  taxPaise: number;
  totalPaise: number;
  amountPaidPaise: number;
  splits?: BillSplit[];
  receiptNumber?: string;
  issuedAt: string;
  paidAt?: string;
}

export type SplitMode = "equal" | "custom";

export interface BillSplitRequest {
  mode: SplitMode;
  /** Equal mode: number of parts (>= 2). */
  parts?: number;
  /** Custom mode: amounts in paise; must sum exactly to the bill total. */
  amountsPaise?: number[];
}

/* ------------------------------------------------------------------ */
/* Reviews (api-plan.md § 8: POST /guest/reviews)                       */
/* ------------------------------------------------------------------ */

export interface ReviewRequest {
  rating: number; // 1–5
  comment?: string;
}

/* ------------------------------------------------------------------ */
/* Kitchen (api-plan.md § 6 as built in Stage 4)                       */
/* ------------------------------------------------------------------ */

/** A restaurant the KDS can pick (GET /kitchen/restaurants). */
export interface KitchenRestaurant {
  id: string;
  name: string;
}

/** An order as the KDS sees it: the guest Order plus the table
 * number (ticket header) and server-computed elapsed seconds. */
export interface KitchenOrder extends Order {
  tableNumber: number;
  elapsedSeconds: number;
}

/** Column-name status filter values for GET /kitchen/orders:
 * `queued` -> placed/accepted, `preparing` -> preparing,
 * `ready` -> ready. */
export type KitchenStatusFilter = "queued" | "preparing" | "ready";

/** The three board columns. `placed`/`accepted` both land in New;
 * terminal states (served/completed/cancelled) are not on the board. */
export type KitchenColumnId = "new" | "preparing" | "ready";

export const KITCHEN_COLUMNS: { id: KitchenColumnId; title: string }[] = [
  { id: "new", title: "New" },
  { id: "preparing", title: "Preparing" },
  { id: "ready", title: "Ready" },
];

/** WebSocket event envelope (api-plan.md § 9.3). `data` carries
 * identifiers + a summary only — receivers re-fetch via REST. */
export interface KitchenEvent {
  type: "order.created" | "order.status_changed" | string;
  restaurant_id: string;
  table_id: string | null;
  data: {
    order_id: string;
    order_number: string;
    status: string;
    table_number: number;
  };
  version: number;
  occurred_at: string;
}

export type KitchenConnectionStatus =
  | "live"
  | "connecting"
  | "reconnecting"
  | "offline";

export interface PaginationInfo {
  page: number;
  per_page: number;
  total: number;
  total_pages: number;
}

export interface KitchenOrderListResponse {
  data: KitchenOrder[];
  pagination: PaginationInfo;
}
