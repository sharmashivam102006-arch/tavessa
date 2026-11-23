# Tavessa — API Plan

> Status: Blueprint v1 — 23 Nov 2026. No application code built yet.
> Companion docs: `project-overview.md`, `architecture.md`,
> `database-design.md`, `development-roadmap.md`.

## 1. Conventions

- **Base URL:** `/api/v1` (versioned from day one; unversioned routes do not exist).
- **Format:** JSON everywhere. `Content-Type: application/json`.
- **Money:** integer paise in every request and response (`totalPaise`,
  `unitPricePaise`, …). No floats, no decimal strings. The frontend formats
  paise for display only.
- **Wire naming (Stage 3 reconciliation, 23 Nov 2026):** guest endpoints
  are **camelCase** on the wire, matching the Stage 2 frontend types in
  `frontend/src/shared/api/types.ts` field-for-field (`orderNumber`,
  `tableSessionId`, `subtotalPaise`, `pricePaise`, `isVeg`, `sortOrder`,
  `openedAt`, `placedAt`, …). The snake_case field names in the examples
  in this document are superseded for guest endpoints — the Pydantic
  schemas (camelCase aliases, `populate_by_name`) are the source of
  truth, and `GET /openapi.json` shows the actual wire names. The one
  exception is the list pagination object, which stays snake_case
  exactly as specified: `{ "page": 1, "per_page": 20, "total": 57,
  "total_pages": 3 }`. Errors keep the existing envelope
  `{ "error": { "code", "message", "details" } }`.
- **Response shapes (Stage 3):** session/menu/order-placement are
  **bare** (`{ "session", "restaurant", "table", "menuUrl" }`,
  `{ "categories", "items" }`, `{ "order" }`); single-resource reads
  and actions use `{ "data": … }` (`GET /guest/orders/:id`,
  `POST /orders/:id/cancel`, `POST /guest/help-calls`); lists use
  `{ "data": […], "pagination": {…} }` (`GET /guest/orders`).
- **Status/type values (Stage 3 reconciliation):** all statuses and
  the help-call type are **lowercase** on the wire. Mapping from the
  brief's UPPERCASE names:

  | Entity | UPPERCASE (brief) | Wire (lowercase) |
  |---|---|---|
  | Order | PLACED, ACCEPTED, COOKING, READY, SERVED, CANCELLED | `placed`, `accepted`, `preparing`, `ready`, `served`, `cancelled` (+ `completed`, staff-side, not a guest step) |
  | Order item | PENDING, ACCEPTED, PREPARING, READY, SERVED, CANCELLED | `queued`, `accepted`, `preparing`, `ready`, `served`, `cancelled` |
  | Help call type | — | `waiter`, `water`, `plates`, `order_help` (input is case-insensitive; `plates_cutlery` is accepted as an alias for `plates`; always returned lowercase) |

  Stored as TEXT with CHECK constraints (keeps SQLite test parity
  with PostgreSQL).
- **IDs:** UUID strings.
- **Timestamps:** ISO 8601 UTC (`2026-11-23T15:00:00Z`).
- **Auth:**
  - Staff/owner: `Authorization: Bearer <access-jwt>`.
  - Guest: `X-Table-Token: <raw-qr-token>` header on guest endpoints, **or**
    the token in the path for the session-open call (see § 3). The raw token
    is never put in a query string in normal use (query strings are logged).
  - Refresh: `POST /auth/refresh` reads the HttpOnly refresh cookie.
- **Pagination:** `?page=1&per_page=20` (default 20, max 100). Response:
  `{ "data": [...], "pagination": { "page": 1, "per_page": 20, "total": 57,
  "total_pages": 3 } }`.
- **Filtering/sorting:** `?status=placed&table_id=…&from=…&to=…&sort=placed_at&order=desc`.
  Exact filter sets are listed per endpoint below; unknown filters are ignored,
  not errors (forward-compatible).
- **Errors:** one envelope (see § 8). No endpoint returns a bare string or a
  stack trace.

### Response shape

Single resource:

```json
{ "data": { "id": "…", "order_number": "A-104", "status": "placed", "total_paise": 104000 } }
```

List: as in Pagination above. Actions that return the changed resource return
it in `data` (so the client does not need a second fetch).

## 2. Authentication — `POST /auth/*`

| Method & path | Auth | Body / notes | Returns |
|---|---|---|---|
| `POST /auth/login` | None (rate-limited) | `{ email, password }` | `{ access_token, token_type: "Bearer", expires_in: 900, user: { id, name, email, role, restaurant_id } }` + HttpOnly refresh cookie |
| `POST /auth/refresh` | Refresh cookie | — | New access token + rotated refresh cookie. Reuse of a revoked refresh token revokes the family → `401`. |
| `POST /auth/logout` | JWT | — | Revokes refresh token family; `204`. |
| `GET /auth/me` | JWT | — | Current user (id, name, email, role, restaurant_id, branch_id). |

Login failures return `401` with the generic code `invalid_credentials`
(no "email not found" vs "wrong password" distinction). After 5 failed
attempts per IP+email in 15 minutes, `429` (rate limit).

## 3. Guest — table sessions & menu

> **Stage 3 status (23 Nov 2026): implemented and verified** —
> `GET /guest/t/:tableToken`, `GET /guest/session`, `GET /guest/menu`
> (with `?categoryId=` and `?search=`), `POST /guest/help-calls`
> (see § 5). `GET /guest/menu/categories/:id/items` and
> `POST /guest/session/close` are NOT implemented in Stage 3.
> Demo QR tokens expire in **30 days — provisional, pending D5**
> (session/token expiry). Guest cancellation is **only while the
> order status is `placed` — D8 still open**.

Guest endpoints are scoped by the QR token. The token is validated on every
call; the restaurant and table are derived from it, never from a client
-supplied `restaurant_id` or `table_id`.

| Method & path | Auth | Notes |
|---|---|---|
| `GET /guest/t/:tableToken` | Token in path | **Session open.** Validates the token, creates or resumes the `table_sessions` row, returns `{ session, restaurant, table, menu_url }`. This is the only guest endpoint that takes the token in the path. Rate-limited per IP (brute-force guard). |
| `GET /guest/session` | `X-Table-Token` | Current session (restaurant, table, status, open orders, open bill if any). The guest app calls this on load/reconnect. |
| `GET /guest/menu` | `X-Table-Token` | Categories + items + variants + add-ons for this restaurant. Only `is_active` categories/items; unavailable items returned with `is_available: false` (so the UI can show "sold out" rather than vanishing). |
| `GET /guest/menu/categories/:id/items` | `X-Table-Token` | Optional per-category fetch; the full menu call is the default. |
| `POST /guest/session/close` | `X-Table-Token` | Guest-initiated close (only when no unpaid bill — else `409`). Staff can also close (see § 5). |

Guest token errors: unknown token → `401` (`invalid_table_token`);
revoked or expired token → `410` (`session_expired`) — Stage 3
reconciliation: the blueprint's "revoked → 401" is superseded; revoked
and expired are both `410` because the frontend maps 401 to
"invalid QR" and 410 to "session expired". A token for another table
used against a session-scoped path → `404` (not `403`) for guest
order reads, per § 11 (no existence leakage).

## 4. Orders

> **Stage 3 status (23 Nov 2026): guest half implemented and
> verified** — `POST /orders` (idempotent, server-priced),
> `GET /guest/orders`, `GET /guest/orders/:id`,
> `POST /orders/:id/cancel` (guest, only while `placed`, else `409`).
> Staff `GET /orders`, `PATCH /orders/:id/status` and item-status
> endpoints are NOT in Stage 3 (Stages 4–5). No `PATCH` status
> endpoint exists yet; status transitions are enforced by a
> transition map in `order_service` and the only guest transition is
> cancel-from-placed. Order numbers are per-restaurant running
> numbers, format `ZAIKA-0001` (slug upper-cased + zero-padded
> sequence, allocated under a row lock on the restaurant). Tax is 0
> (D1 undecided — no rate invented).

### 4.1 Placement — the idempotent endpoint

```
POST /api/v1/orders
Authorization: (guest: X-Table-Token)   — v1: guests only
Idempotency-Key: <uuid generated by the client, one per placement attempt>
Content-Type: application/json

{
  "items": [
    {
      "menu_item_id": "…",
      "variant_id": "…",            // optional
      "add_on_ids": ["…"],          // optional
      "quantity": 2,
      "special_instructions": "less spicy"
    }
  ]
}
```

**Server behaviour (all in one transaction):**
> Wire note (Stage 3): the request body on the wire is camelCase —
> `{ "items": [{ "menuItemId": "…", "variantId": "…", "addOnIds": ["…"],
> "quantity": 2, "specialInstructions": "less spicy" }] }` — and any
> price/total field in the body is rejected with `422` (the schema
> forbids extra fields), not silently ignored.

1. Resolve the session from the token; reject if the session is not `active`.
2. Validate every item exists, belongs to this restaurant, is available;
   validate the variant belongs to the item; validate every add-on is offered
   for that item and is active.
3. Recompute every price from the menu (`base + variant delta + add-ons`) ×
   quantity. Ignore any price/total sent by the client (the schema does not
   accept price fields at all).
4. Create the order, order items (with snapshots), and one kitchen ticket per
   station present. Set the table to `occupied` if needed.
5. Commit, then publish `order.created` (see § 6).
6. Return `201` with the created order (including `order_number`, item
   snapshots, and computed totals).

See § 7 for the idempotency contract. Validation failure → `422` with
per-item details (`{ "field": "items[1].variant_id", "code": "invalid_variant" }`).

### 4.2 Order reads & status

| Method & path | Auth | Notes |
|---|---|---|
| `GET /orders` | JWT (waiter/kitchen/owner) | List for the caller's restaurant. Filters: `status`, `table_id`, `session_id`, `from`, `to`. Sorted `placed_at desc` by default. |
| `GET /orders/:id` | JWT or guest (own session) | Full order with items, ticket statuses, bill link if any. Guest: only if the order belongs to their session, else `403`. |
| `GET /guest/orders` | `X-Table-Token` | Orders for this session (active + recent). **Stage 3: implemented** — `{ "data": […], "pagination": { "page", "per_page", "total", "total_pages" } }`, `placed_at` desc. |
| `GET /guest/orders/:id` | `X-Table-Token` | One order of this session, for live tracking. **Stage 3: implemented** — another session's order → `404`. |
| `PATCH /orders/:id/status` | JWT (kitchen/owner; waiter for `served` only) | Body `{ "status": "accepted" \| "preparing" \| "ready" \| "served" \| "completed" \| "cancelled", "reason": "…" }`. Transition validated against the machine (`database-design.md` § 6); illegal → `409`. Sets the matching `*_at` timestamp. **Not in Stage 3** (Stage 4). |
| `POST /orders/:id/cancel` | JWT or guest (own session, before `accepted` — per D8) | Body `{ "reason": "…" }`. Cancels the order and its non-served items; recomputes totals; if a bill already exists, the bill must be regenerated (Stage 6 rule). **Stage 3: guest cancel implemented — only while `placed` (D8 still open), else `409`; cancels its items too, same transaction.** |
| `PATCH /orders/:orderId/items/:itemId/status` | JWT (kitchen/owner; waiter for `served` only) | Body `{ "status": "preparing" \| "ready" \| "served" \| "cancelled", "reason": "…" }`. Item transition; order + ticket statuses recomputed in the same transaction; `order.item_ready` / `order.ready` events published after commit. |

**Status-transition rule:** the backend is the only authority. A request to
move `queued → served` directly is rejected (`409`) — the item must pass
through `ready`. The frontend offers only the transitions legal from the
current state (it can read them from the order's `available_actions` field in
the response, computed server-side).

## 5. Tables, floor & help calls

| Method & path | Auth | Notes |
|---|---|---|
| `GET /tables` | JWT (waiter/owner) | Floor map: all tables for the restaurant with status, current session id, open order count, open help call flag. Sorted by zone, then number. |
| `GET /tables/:id` | JWT (waiter/owner) | Table + active session + its orders + its open bill. |
| `PATCH /tables/:id/status` | JWT (waiter/owner) | `{ "status": "cleaning" \| "available" \| "out_of_service" }`. `occupied` is system-set, not manually settable (except by owner override — audited, Stage 5). |
| `POST /tables/:id/close-session` | JWT (waiter/owner) | Close the active session (only when no unpaid bill — else `409` with the bill id). Sets table `cleaning`. |
| `POST /guest/help-calls` | `X-Table-Token` | **Stage 3: implemented.** Body `{ "type": "waiter" \| "water" \| "plates" \| "order_help", "note": "…" }` — type is case-insensitive, `plates_cutlery` is an alias for `plates`. One open/acknowledged call per session (second call → `409` with the existing call id in `error.details`). Status starts `open`; nothing claims a waiter has accepted it. |
| `GET /help-calls` | JWT (waiter/owner) | Filters: `status`, `table_id`, `assigned_to=me`. |
| `GET /help-calls/:id` | JWT | One call. |
| `PATCH /help-calls/:id/accept` | JWT (waiter/owner) | Assigns to the caller, `open → acknowledged`. Publishes `help_call.acknowledged`. |
| `PATCH /help-calls/:id/resolve` | JWT (waiter/owner) | `acknowledged → resolved`. Publishes `help_call.resolved`. |
| `PATCH /help-calls/:id/cancel` | JWT or guest (own session) | `open/acknowledged → cancelled`. |

## 6. Kitchen (KDS)

| Method & path | Auth | Notes |
|---|---|---|
| `GET /kitchen/tickets` | JWT (kitchen/owner) | Active queue. Filters: `station_id`, `status`. Sorted by `created_at` (oldest first — the kitchen works the oldest ticket first). Each ticket includes its order items for that station, table number, elapsed seconds, and `sla_breached` flag. |
| `GET /kitchen/tickets/:id` | JWT (kitchen/owner) | One ticket with items. |
| `PATCH /kitchen/tickets/:id/status` | JWT (kitchen/owner) | `{ "status": "preparing" \| "ready" \| "served" }`. Derived-status guard: a ticket cannot be set `ready` while any non-cancelled item is still `queued`/`preparing` — the service either rejects (`409`) or, preferably, the ticket status is **derived only** and this endpoint is not used to set it directly. **Decision for Stage 4:** ticket status is derived from items; this endpoint exists only for `preparing` (accept) as an explicit kitchen acknowledgement. Record the final choice in Stage 4. |
| `GET /kitchen/stations` | JWT (kitchen/owner) | Stations for the restaurant (id, name, active ticket counts). |

The KDS screen polls nothing in normal operation — it subscribes to the
WebSocket kitchen channel and re-fetches `GET /kitchen/tickets` on connect,
reconnect, and on each `ticket.*` event.

## 7. Bills, splits, payments, refunds, receipts

### 7.1 Bills

| Method & path | Auth | Notes |
|---|---|---|
| `POST /bills` | Guest (own session) or JWT (waiter/owner) | Body `{ "table_session_id": "…" }` (staff) — guest session is derived from the token. Generates a bill from the session's unpaid orders in one transaction. One open bill per session (second call returns the existing open bill, `200`, not a duplicate — this is a status guard, not idempotency-key replay). Returns the bill with items + totals. |
| `GET /bills/:id` | JWT or guest (own session) | Bill + items + splits + payments + receipt link. |
| `GET /bills` | JWT (waiter/owner) | Filters: `status`, `table_id`, `from`, `to`, `session_id`. |
| `GET /guest/bill` | `X-Table-Token` | The session's open bill (or the most recent paid bill for the receipt view). |
| `PATCH /bills/:id/request-payment` | Guest (own session) or JWT | `issued → payment_requested`. Guest tapping "Pay by Cash" calls this. Creates no payment. Publishes `bill.payment_requested` + a staff notification. |
| `POST /bills/:id/splits` | Guest (own session) or JWT | `{ "mode": "equal", "parts": 2 }` or `{ "mode": "custom", "amounts_paise": [50000, 50000] }`. Validated: equal — parts ≥ 2; custom — amounts sum exactly to `total_paise`, each > 0. Replaces existing unpaid splits (in a transaction). Returns the splits. |
| `POST /bills/:id/void` | JWT (owner) | `{ "reason": "…" }`. `issued/payment_requested → voided`. Audited. A voided bill's orders become billable again (Stage 6 detail). |

### 7.2 Payments — cash confirmation (staff only)

```
POST /api/v1/bills/:id/payments
Authorization: Bearer <staff jwt>   (waiter or owner only)
{
  "amount_paise": 50000,
  "bill_split_id": "…",        // optional; null = whole bill
  "change_due_paise": 0,       // optional, per D9
  "note": "…"                  // optional
}
```

**Server behaviour (one transaction, row lock on the bill/split):**

1. Load the bill (and split) `FOR UPDATE`. Reject if the bill is not
   `issued`/`payment_requested` or the split is already `paid` → `409`.
2. Validate `amount_paise` > 0 and ≤ amount due on the bill/split.
   Any other amount → `422`.
3. Create the `payments` row (`method=cash`, `collected_by=current user`,
   `receipt_number` generated).
4. Update `amount_paid_paise`; if the bill/split is fully paid, set status
   `paid` + `paid_at`; if all splits are paid, the bill is `paid`.
5. If all bills for the session are paid, the session may be closed by staff
   (not automatically — Stage 6 detail).
6. Write the audit log in the same transaction.
7. Commit, then publish `payment.confirmed` + `bill.paid`.

Response: `201` with the payment + the updated bill + receipt data.
A second confirmation for the same split fails at step 1 (`409`) and returns
the existing payment — this is the double-confirmation guard.

**There is no guest endpoint that creates a payment.** Any request to this
path with a guest token returns `403`.

### 7.3 Refunds & receipts

| Method & path | Auth | Notes |
|---|---|---|
| `POST /bills/:id/refunds` | JWT (owner only) | `{ "payment_id": "…", "amount_paise": …, "reason": "…" }`. Validated against the payment minus prior refunds. Creates the refund, updates payment + bill status (`refunded` / `partially_refunded`), audited. |
| `GET /bills/:id/receipt` | JWT or guest (own session) | Receipt data (JSON). Printable view is a frontend rendering of this data. Receipts are read-only; there is no "edit receipt" endpoint. |
| `GET /payments` | JWT (owner; waiter sees own) | Cash report source. Filters: `from`, `to`, `collected_by`. |
| `GET /admin/cash-report?date=YYYY-MM-DD` | JWT (owner) | Daily cash: collected, per collector, outstanding bills, refunds. Computed from `payments`/`bills`/`refunds` on read. |

## 8. Menu & admin

| Method & path | Auth | Notes |
|---|---|---|
| `GET /menu/categories` | JWT or guest | Categories for the restaurant. |
| `POST /menu/categories` | JWT (owner) | Create. |
| `PATCH /menu/categories/:id` | JWT (owner) | Rename / reorder / activate. |
| `GET /menu/items` | JWT or guest | Filters: `category_id`, `is_available`, `search`. |
| `POST /menu/items` | JWT (owner) | Create with `base_price_paise`, `station_id`, `is_veg`, image. |
| `PATCH /menu/items/:id` | JWT (owner) | Edit. **Price changes are audited** (`menu.price_changed`, old → new) and never affect existing orders (snapshots). |
| `PATCH /menu/items/:id/availability` | JWT (owner) | `{ "is_available": false }`. The fast toggle the owner uses during service. |
| `POST /menu/items/:id/variants` | JWT (owner) | Add a variant. |
| `PATCH /menu/items/:id/variants/:variantId` | JWT (owner) | Edit a variant. |
| `POST /menu/items/:id/add-ons` | JWT (owner) | Link an add-on with a per-item price. |
| `DELETE /menu/items/:id/add-ons/:addOnId` | JWT (owner) | Unlink. (Physical delete of the link row; the add-on catalogue row remains.) |
| `GET /admin/overview` | JWT (owner) | Live aggregates: occupancy, active orders, kitchen queue depth, open help calls, bills awaiting cash, today's sales + cash. |
| `GET /admin/analytics/sales?from=&to=&group_by=day\|category\|item` | JWT (owner) | Read-only aggregates. |
| `GET /admin/orders` | JWT (owner) | Order history (same as `GET /orders` with owner scope + more filters). |
| `POST /admin/staff` | JWT (owner) | Create a staff user (role `waiter`/`kitchen`/`admin`). |
| `GET /admin/staff` | JWT (owner) | Staff list with assignment + active status. |
| `PATCH /admin/staff/:id` | JWT (owner) | Edit role / deactivate. Role change is audited. |
| `POST /admin/staff/:id/assignments` | JWT (owner) | Assign tables to a waiter. |
| `POST /admin/tables` | JWT (owner) | Create a table. |
| `POST /admin/tables/:id/qr-rotate` | JWT (owner) | Rotate the QR token (old token revoked immediately, new raw token returned **once** for printing). Audited. |
| `GET /admin/settings` | JWT (owner) | Restaurant settings. |
| `PATCH /admin/settings` | JWT (owner) | Update settings (tax mode, SLA minutes, … — per D1). |
| `POST /guest/reviews` | `X-Table-Token` | `{ "rating": 1–5, "comment": "…" }`. Only after the session's bill is paid, one per session. |
| `GET /admin/reviews` | JWT (owner) | Filters: `status`, `rating`. Moderation per D6. |

**Menu image upload:** `POST /menu/items/:id/image` (multipart, owner only).
Validated type/size; stored via the abstracted storage interface; returns the
`image_url`. No image URL supplied by the client is trusted without validation.

## 9. WebSocket API

### 9.1 Connect

```
GET /ws/restaurant?token=<access-jwt>          # staff / kitchen / owner
GET /ws/table-session?token=<table-token>       # guest (token as query param here
                                                # because browsers cannot set
                                                # headers on a WS handshake;
                                                # the token is single-use-scoped
                                                # to this handshake and is not logged)
```

Authentication at connect time; failure closes the socket with code `4401`.
The token is validated and then discarded from logs. (Alternative: a short-lived
WS ticket issued via REST — Stage 8 may adopt this if the query-param token
is judged too exposed in logs; record the decision then.)

### 9.2 Channels & authorisation

| Channel | Subscriber | Authorisation check |
|---|---|---|
| `restaurant:{restaurant_id}` | Staff, kitchen, owner | JWT restaurant matches |
| `table-session:{session_id}` | Guest of that session; staff viewing that table | Token session matches, or JWT restaurant matches + table in scope |
| `kitchen:{restaurant_id}:{station_id}` | KDS | JWT role kitchen/owner + station belongs to restaurant |

A client subscribes by sending `{ "action": "subscribe", "channel": "…" }`
after connect. Subscribing to a channel it is not authorised for returns an
error event and does not subscribe it.

### 9.3 Event catalogue

All events use the envelope in `architecture.md` § 6.1
(`type`, `restaurant_id`, `table_id`, `data`, `version`, `occurred_at`).
`data` carries identifiers + a summary only.

| Event | Published when | Channels |
|---|---|---|
| `order.created` | Order committed | `restaurant:*`, `kitchen:*` (per station tickets), `table-session:*` |
| `order.status_changed` | Order status changes | `restaurant:*`, `table-session:*` |
| `order.item_ready` | An item becomes `ready` | `restaurant:*`, `table-session:*` |
| `order.item_status_changed` | Any item status change | `restaurant:*`, `kitchen:*` |
| `ticket.created` | Ticket created (with the order) | `kitchen:{station}` |
| `ticket.status_changed` | Ticket status derived-change | `kitchen:{station}`, `restaurant:*` |
| `table.status_changed` | Table status changes | `restaurant:*` |
| `help_call.opened` | Guest requests help | `restaurant:*`, `table-session:*` |
| `help_call.acknowledged` | Waiter accepts | `restaurant:*`, `table-session:*` |
| `help_call.resolved` | Waiter resolves | `restaurant:*`, `table-session:*` |
| `bill.issued` | Bill generated | `restaurant:*`, `table-session:*` |
| `bill.payment_requested` | Guest selects Pay by Cash | `restaurant:*`, `table-session:*` |
| `bill.paid` | Bill fully paid | `restaurant:*`, `table-session:*` |
| `payment.confirmed` | Staff confirms cash | `restaurant:*`, `table-session:*` |
| `menu.availability_changed` | Owner toggles availability | `restaurant:*`, `table-session:*` (guest menu updates) |
| `session.closed` | Session closes | `restaurant:*`, `table-session:*` |

### 9.4 Client contract

- On connect: subscribe, then **re-fetch current state via REST**.
- On event: invalidate the matching TanStack Query keys (or apply the
  summary if it is safe), then re-fetch. Never treat the event as the
  authoritative state.
- On disconnect: exponential backoff reconnect (1s, 2s, 4s, … max 30s),
  then re-fetch on reconnect.
- On `version` gap within a channel: re-fetch that channel's state.

## 10. Idempotency (§ 7 of the brief — the contract)

> **Stage 3 status (23 Nov 2026): implemented as specified, verified
> live and in tests.** `POST /orders` only. The idempotency record is
> stored **after** the order transaction commits; a replay returns
> the stored response verbatim with the original status and header
> `Idempotency-Replayed: true`. Rate limits (slowapi, per IP):
> `GET /guest/t/:tableToken` 120/minute, `POST /orders` 60/minute —
> generous caps to blunt brute force / abuse, not to police guests.

- **Where:** `POST /orders` only in v1. The client generates a UUID
  `Idempotency-Key` per placement attempt and reuses it on retry.
- **Storage:** `idempotency_keys` (see `database-design.md` § 3.26), keyed by
  `(restaurant_id, endpoint, key)`, with a hash of the canonical request body
  and the stored response.
- **Replay, same key + same body:** return the stored response verbatim with
  the original status code and a header `Idempotency-Replayed: true`. No new
  order, no new ticket.
- **Same key + different body:** `422` (`idempotency_key_reused`).
- **Missing key on `POST /orders`:** `422` (`idempotency_key_required`) — the
  frontend always sends one; a missing key is a client bug, not a fallback.
- **Expiry:** 24 hours. After expiry, the same key is treated as new.
- **Why not for payments:** payment confirmation is guarded by the bill/split
  row lock + status check (`409` on a second confirmation, returning the
  existing payment). A replayed payment request with the same key but a
  different amount must not silently return the first payment — the status
  guard is the correct control, and the `409` response names the existing
  payment id.

## 11. Errors

One envelope:

```json
{
  "error": {
    "code": "validation_failed",
    "message": "Quantity must be between 1 and 20.",
    "details": { "field": "items[0].quantity" }
  }
}
```

| HTTP | Code | Meaning |
|---|---|---|
| 400 | `bad_request` | Malformed JSON / request |
| 401 | `unauthenticated` / `invalid_credentials` / `invalid_table_token` | No/invalid auth |
| 403 | `forbidden` | Authenticated but not allowed (wrong role, wrong restaurant, wrong table) |
| 404 | `not_found` | Resource does not exist (or exists in another restaurant — same response, no leakage) |
| 409 | `conflict` | Illegal status transition, duplicate open bill/call/session, second payment confirmation |
| 410 | `session_expired` | Guest session expired |
| 422 | `validation_failed` / `idempotency_key_required` / `idempotency_key_reused` / `invalid_variant` / `split_sum_mismatch` / `amount_exceeds_due` | Validation failed (details name the field) |
| 429 | `rate_limited` | Too many requests |
| 500 | `internal_error` | Generic; details logged server-side only |

**404 vs 403 rule:** a resource in another restaurant returns `404`, not
`403`, so existence is not leaked across tenants. A resource in the caller's
restaurant that their role cannot access returns `403`.

## 12. OpenAPI & types

- FastAPI generates OpenAPI from the Pydantic schemas automatically.
  `GET /docs` (Swagger) and `/openapi.json` are enabled in development and
  staging; in production they are behind owner auth or disabled (Stage 10
  decision — default: disabled in production).
- Frontend types are generated from `/openapi.json` (`openapi-typescript`)
  in Stage 1. If generation is not wired up yet, hand-written types must
  match the Pydantic schemas field-for-field and are marked `// TODO: generate
  from OpenAPI` — they are not a permanent alternative.
- Every endpoint in this document must appear in OpenAPI with its auth,
  request, and response schemas. An endpoint without a schema is not done.

## 13. What is *not* in this API plan

- No payment-gateway endpoints (cash only).
- No public booking/reservation endpoints.
- No delivery/takeaway endpoints.
- No loyalty/coupon endpoints.
- No admin endpoint that returns another restaurant's data.
- No endpoint that accepts a price/total from the client and uses it.
