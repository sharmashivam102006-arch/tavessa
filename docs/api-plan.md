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
| `PATCH /orders/:id/status` | JWT (kitchen/owner; waiter for `served` only) | Body `{ "status": "accepted" \| "preparing" \| "ready" \| "served" \| "completed" \| "cancelled", "reason": "…" }`. Transition validated against the machine (`database-design.md` § 6); illegal → `409`. Sets the matching `*_at` timestamp. **Not in Stage 3; not in Stage 4 either** — Stage 4 built the kitchen transition as `PATCH /kitchen/orders/:id/status` instead (see § 6: order-level, preparing/ready only, dev-key gated). **Stage 5 built the serve transition as `PATCH /staff/orders/:id/serve` instead** (see § 5: ready→served only, dev-key gated). The staff-wide endpoint lands with staff auth. |
| `POST /orders/:id/cancel` | JWT or guest (own session, before `accepted` — per D8) | Body `{ "reason": "…" }`. Cancels the order and its non-served items; recomputes totals; if a bill already exists, the bill must be regenerated (Stage 6 rule). **Stage 3: guest cancel implemented — only while `placed` (D8 still open), else `409`; cancels its items too, same transaction.** |
| `PATCH /orders/:orderId/items/:itemId/status` | JWT (kitchen/owner; waiter for `served` only) | **Not built — superseded by the Stage 4 order-level decision** (api-plan.md § 6, deviation 1): there is no per-item status endpoint; items move with their order. |

**Status-transition rule:** the backend is the only authority. A request to
move `queued → served` directly is rejected (`409`) — the item must pass
through `ready`. The frontend offers only the transitions legal from the
current state (it can read them from the order's `available_actions` field in
the response, computed server-side).

## 5. Tables, floor & help calls

> **Stage 5 status (23 Nov 2026): staff half implemented and
> verified — as built below, which OVERRIDES the blueprint's
> JWT/assignment design for this stage. Recorded deviations:**
> 1. **Auth is DEV-ONLY in Stage 5** (same pattern as Stage 4):
>    staff REST endpoints require header `X-Staff-Dev-Key` equal
>    to the backend `STAFF_DEV_KEY` (missing → 401, wrong → 403);
>    the WebSocket takes the same key as query param `?dev_key=…`
>    (never logged). In production the staff routes return **503
>    `staff_auth_not_configured`** (WS: close 4403) until staff
>    auth lands. A guest table token does NOT grant staff access;
>    the kitchen dev key does NOT grant staff access (and vice
>    versa). This is development access only — never present it
>    as production auth.
> 2. **Serve is its own endpoint** — `PATCH
>    /staff/orders/:id/serve` (empty body), ready→served only.
>    Anything else → `409` with `current_status` +
>    `requested_status` in `error.details`, state unchanged.
>    Serving sets `served_at`, `version += 1`, and moves
>    non-cancelled items ready→served in ONE transaction under a
>    row lock. **Serving does NOT close the session and does NOT
>    change the table status** — the session stays `active` and
>    the table stays `occupied` until Stage 6 (billing/session
>    close).
> 3. **Help-call resolve is acknowledged→resolved ONLY.** There is
>    no `PATCH /help-calls/:id/accept` in Stage 5 — the endpoint is
>    `PATCH /staff/help-calls/:id/acknowledge` (open→acknowledged,
>    sets `acknowledged_at`) and `PATCH
>    /staff/help-calls/:id/resolve` (acknowledged→resolved, sets
>    `resolved_at`); resolving an open call → `409`. Acknowledge
>    does NOT assign a waiter — no `acknowledged_by`/`resolved_by`
>    columns exist (no users system yet).
> 4. **No bill requests, no session close, no table-status
>    transitions, no staff assignments in Stage 5** — `PATCH
>    /tables/:id/status`, `POST /tables/:id/close-session` and
>    assignment (`assigned_to=me`) are NOT built; they belong to
>    Stage 6 (billing/table lifecycle) and Stage 7 (owner/admin).
>    The floor is read-only in Stage 5: a simple grid in table
>    number order (no `position_x/y` coordinates seeded).
> 5. **Statuses on the wire stay LOWERCASE.**
>
> **`restaurant_id` rule (same as kitchen):** required on staff
> REST endpoints EXCEPT when omitted and exactly one restaurant
> exists — then that restaurant is used. Omitted with 0 or 2+
> restaurants → 422 requiring `restaurant_id`. Unknown
> restaurant → 404.

| Method & path | Auth | Notes |
|---|---|---|
| `GET /staff/tables?restaurant_id=` | Staff dev key | **Stage 5: implemented.** Floor grid: all tables for the restaurant, number order. Each row: table number + status (from `dining_tables.status`), active session (`sessionId`/`sessionOpenedAt` or null), `activeOrderCount`, `readyOrderCount`, `latestOrderStatus` (most recent active order), `hasOpenHelpCall`. All derived live from DB state. |
| `GET /staff/tables/:id?restaurant_id=` | Staff dev key | **Stage 5: implemented.** Table + active session + that session's orders (full snapshots) + open/acknowledged help calls. Unknown id or wrong restaurant → 404. |
| `GET /staff/orders?restaurant_id=&status=&page=&per_page=` | Staff dev key | **Stage 5: implemented.** Active orders (placed/accepted/preparing/ready), oldest first (`placed_at` asc). `status` is a single active status; unknown value → 422. Each order carries `tableNumber`, server-computed `elapsedSeconds`, full item snapshots. Served orders are NOT in this list (they leave the kitchen queue too). |
| `PATCH /staff/orders/:id/serve?restaurant_id=` | Staff dev key (rate-limited, 60/min) | **Stage 5: implemented.** Empty body. Only ready→served (see deviation 2 above). Broadcasts `order.status_changed` after commit. |
| `GET /staff/help-calls?restaurant_id=&status=` | Staff dev key | **Stage 5: implemented.** Unresolved (open+acknowledged) by default, oldest first; `status` filters one status (unknown → 422). Each call carries `tableNumber` + type + note + status + created/acknowledged/resolved timestamps. |
| `PATCH /staff/help-calls/:id/acknowledge?restaurant_id=` | Staff dev key | **Stage 5: implemented.** open→acknowledged only, else 409. Broadcasts `help_call.acknowledged` after commit. |
| `PATCH /staff/help-calls/:id/resolve?restaurant_id=` | Staff dev key | **Stage 5: implemented.** acknowledged→resolved only, else 409 (see deviation 3). Broadcasts `help_call.resolved` after commit. |
| `GET /staff/restaurants` | Staff dev key | **Stage 5: implemented.** Dev-only picker (id + name); the frontend defaults to the only restaurant when there is one. |
| `GET /ws/staff?restaurant_id=&dev_key=` | Staff dev key (query) | **Stage 5: implemented.** Staff WebSocket — see § 9. Bad/missing key → close 4401, unknown restaurant → 4404, production → close 4403. |
| `GET /tables` | JWT (waiter/owner) | Floor map: all tables for the restaurant with status, current session id, open order count, open help call flag. Sorted by zone, then number. **Not built — superseded in Stage 5 by `GET /staff/tables` (dev-key gated); the JWT version lands with staff auth.** |
| `GET /tables/:id` | JWT (waiter/owner) | Table + active session + its orders + its open bill. **Not built — superseded in Stage 5 by `GET /staff/tables/:id` (dev-key gated; no bill until Stage 6).** |
| `PATCH /tables/:id/status` | JWT (waiter/owner) | `{ "status": "cleaning" \| "available" \| "out_of_service" }`. `occupied` is system-set, not manually settable (except by owner override — audited, Stage 5). **Not built — Stage 6.** |
| `POST /tables/:id/close-session` | JWT (waiter/owner) | Close the active session (only when no unpaid bill — else `409` with the bill id). Sets table `cleaning`. **Not built — Stage 6.** |
| `POST /guest/help-calls` | `X-Table-Token` | **Stage 3: implemented.** Body `{ "type": "waiter" \| "water" \| "plates" \| "order_help", "note": "…" }` — type is case-insensitive, `plates_cutlery` is an alias for `plates`. One open/acknowledged call per session (second call → `409` with the existing call id in `error.details`). Status starts `open`; nothing claims a waiter has accepted it. **Stage 5: now also broadcasts `help_call.opened` after the creation transaction commits.** |
| `GET /help-calls` | JWT (waiter/owner) | Filters: `status`, `table_id`, `assigned_to=me`. **Not built — superseded in Stage 5 by `GET /staff/help-calls` (dev-key gated; no assignment filter yet).** |
| `GET /help-calls/:id` | JWT | One call. **Not built.** |
| `PATCH /help-calls/:id/accept` | JWT (waiter/owner) | Assigns to the caller, `open → acknowledged`. Publishes `help_call.acknowledged`. **Not built — superseded in Stage 5 by `PATCH /staff/help-calls/:id/acknowledge` (dev-key gated, no assignment).** |
| `PATCH /help-calls/:id/resolve` | JWT (waiter/owner) | `acknowledged → resolved`. Publishes `help_call.resolved`. **Not built — superseded in Stage 5 by `PATCH /staff/help-calls/:id/resolve` (dev-key gated).** |
| `PATCH /help-calls/:id/cancel` | JWT or guest (own session) | `open/acknowledged → cancelled`. **Not built.** |

## 6. Kitchen (KDS)

> **Stage 4 status (23 Nov 2026): implemented and verified — as
> built below, which OVERRIDES the blueprint's ticket/station
> design. Recorded deviations:**
> 1. **Workflow is ORDER-LEVEL, not per-item.** There is no
>    `PATCH /orders/:orderId/items/:itemId/status` and no
>    `PATCH /kitchen/tickets/:id/status`. `PATCH
>    /kitchen/orders/:id/status` moves the order AND its
>    non-cancelled items together in one transaction
>    (placed→preparing: items queued→preparing, `accepted_at` set
>    if null; preparing→ready: items →ready, `ready_at` set).
> 2. **NO `kitchen_tickets` table, NO stations.** A "ticket" IS an
>    order + its item snapshots. Station grouping/filtering is
>    deferred — menu items already carry a `station` TEXT field
>    for later use.
> 3. **WebSockets arrive NOW for the kitchen** (not Stage 8):
>    `GET /ws/kitchen`, events `order.created` and
>    `order.status_changed` (see § 9). Guest tracking still
>    refetches via REST.
> 4. **Statuses on the wire stay LOWERCASE.** Column mapping:
>    New = placed (+accepted if ever present), Preparing =
>    preparing, Ready = ready. Terminal states
>    (served/completed/cancelled) are NOT in the active queue.
>
> **Auth is DEV-ONLY in Stage 4:** kitchen REST endpoints require
> header `X-Kitchen-Dev-Key` equal to the backend `KITCHEN_DEV_KEY`
> (missing → 401, wrong → 403); the WebSocket takes the same key as
> query param `?dev_key=…` (never logged). In production the
> kitchen routes return **503 `kitchen_auth_not_configured`** (WS:
> close 4403) until staff auth lands. The KDS is
> development-access only until then. Guest token security is
> untouched.
>
> **`restaurant_id` rule:** required on kitchen REST endpoints
> EXCEPT when omitted and exactly one restaurant exists — then
> that restaurant is used. Omitted with 0 or 2+ restaurants → 422
> requiring `restaurant_id`. Unknown restaurant → 404.

| Method & path | Auth | Notes |
|---|---|---|
| `GET /kitchen/orders?restaurant_id=&status=&page=&per_page=` | Dev key | Active queue, oldest first (`placed_at` asc). `status` uses column names: `queued` → placed/accepted, `preparing` → preparing, `ready` → ready; unknown value → 422. No filter returns all non-terminal orders (placed/accepted/preparing/ready). Each order carries `tableNumber`, server-computed `elapsedSeconds`, and full item snapshots. |
| `GET /kitchen/orders/:id?restaurant_id=` | Dev key | One order. Unknown id or wrong restaurant → 404. |
| `PATCH /kitchen/orders/:id/status?restaurant_id=` | Dev key (rate-limited, 60/min) | Body `{ "status": "preparing" \| "ready" }` — any other value → 422. Allowed: placed/accepted → preparing, preparing → ready; anything else → 409 `conflict` with `current_status` + `requested_status` in `error.details`, state unchanged. Row-locks the order (`SELECT … FOR UPDATE`) so concurrent PATCHes serialise; order + items + `version` += 1 in ONE transaction. Broadcasts `order.status_changed` after commit. |
| `GET /kitchen/restaurants` | Dev key | Dev-only picker (id + name) so the KDS can pick the restaurant without hardcoding one; the frontend defaults to the only restaurant when there is one. |
| `GET /ws/kitchen?restaurant_id=&dev_key=` | Dev key (query) | Kitchen WebSocket — see § 9. Bad/missing key → close 4401, unknown restaurant → 4404. |

The KDS screen subscribes to the WebSocket kitchen channel and
re-fetches `GET /kitchen/orders` on connect, reconnect, and on each
`order.*` event. Elapsed time is `mm:ss` from `placedAt`; there is
no SLA/breach flag — no threshold is configured (D7 open).

## 7. Bills, splits, payments, receipts — as built in Stage 6 (23 Nov 2026)

Cash only. There is no guest endpoint that creates a payment, no UPI /
card / gateway / wallet path, no refunds, no bill void, no daily cash
report and no owner bills/payments history in this stage — all of those
need owner auth (Stage 7). The brief's event names are used, not the
older names in the original § 7 draft: `bill.created`, `bill.requested`,
`payment.recorded`, `bill.paid` (`bill.payment_requested` /
`payment.confirmed` are NOT used). There is no `audit_logs` table: the
audit trail is the append-only `payments` table.

### 7.1 Bills

| Method & path | Auth | Notes |
|---|---|---|
| `POST /api/v1/guest/bill` | `X-Table-Token` | Create-or-retrieve for the active session. No bill → generate (status `issued`, broadcast `bill.created` after commit); open `issued` bill → regenerate from current non-cancelled orders (decision 8) and return 200; open `payment_requested` bill → return as-is (frozen); paid bill + new billable items → NEW bill with only unreferenced items; no billable items and no bill → 409 `nothing_to_bill`. Returns bill + items + splits + payments + totals + outstanding. |
| `GET /api/v1/guest/bill` | `X-Table-Token` | The session's open bill, else the most recent paid bill (receipt view); none → 404 `bill_not_found`. Another session's bill → 404, not 403. |
| `PATCH /api/v1/guest/bill/request-payment` | `X-Table-Token` | Empty body. `issued → payment_requested` (unpaid splits → `payment_requested` too); creates NO payment row; already requested → 200 idempotent; paid → 409. Broadcasts `bill.requested` after commit (only on the actual transition). |
| `POST /api/v1/guest/bill/splits` | `X-Table-Token` | `{ "mode": "equal", "parts": N }` or `{ "mode": "custom", "amountsPaise": [...] }`. Server-validated: equal parts ≥ 2 (remainder 1p to earliest parts); custom sums exactly to total, each > 0, else 422. Replaces existing UNPAID splits in one transaction; refused once any split is paid or `payment_requested` → 409. Only while the bill is `issued`/`payment_requested`. |
| `GET /api/v1/guest/bill/receipt` | `X-Table-Token` | Only after the bill is paid, else 409 `receipt_not_available`. |
| `GET /api/v1/staff/bills?restaurant_id=&status=&table_id=&page=&per_page=` | `X-Staff-Dev-Key` (dev only; production → 503) | List, `payment_requested` first, then `issued_at` desc. Row: table number, bill number, status, total, amount paid, outstanding, `payment_requested_at`, order count. Unknown status → 422; unknown/wrong-restaurant table → 404. Returns paid bills too — filter with `status=`; the staff queue drops paid rows itself. |
| `GET /api/v1/staff/bills/:id?restaurant_id=` | `X-Staff-Dev-Key` | Full bill: items, splits, payments (history), totals, outstanding, table/session info. Unknown id or wrong restaurant → 404. |
| `GET /api/v1/staff/bills/:id/payments?restaurant_id=` | `X-Staff-Dev-Key` | Payment history for the bill, newest first. |
| `GET /api/v1/staff/bills/:id/receipt?restaurant_id=` | `X-Staff-Dev-Key` | Receipt data, only after paid, else 409. |
| `GET /api/v1/staff/tables/:id` | `X-Staff-Dev-Key` | Table detail now includes `open_bill` (bill number, status, total, outstanding) when one exists. |

Bill numbers are `B-YYYY-MM-DD-NNN`, sequential per restaurant per day
under the restaurant row lock (same pattern as `ZAIKA-0001` order
numbers). Calculation is server-side only, integer paise, from
`order_items` snapshots: line = unit × quantity; subtotal = Σ lines;
tax / service charge / discount = 0 (D1 undecided — the bill and receipt
show tax as explicitly "not configured", never "0% GST"); total =
subtotal; outstanding = total − amount paid. Client-supplied price/total
fields → 422. A partially-paid `issued` bill does not regenerate; a
no-change regen does not bump `version`.

### 7.2 Payments — cash confirmation (staff only)

```
POST /api/v1/staff/bills/:id/payments?restaurant_id=…
X-Staff-Dev-Key: <dev key>
Idempotency-Key: <required, else 422>
{ "amountPaise": 50000, "billSplitId": "…", "note": "…" }
```

`method` is NOT accepted from the client (cash is the only method — a
body `method` other than cash → 422; no method field → cash). Splits
exist → `billSplitId` required, else 422; no splits → `billSplitId`
must be omitted, else 422.

**Server behaviour (ONE transaction, Stage 3.2 claim pattern):**

1. Load the bill (+ split) FOR UPDATE — the bill row lock is taken
   BEFORE the idempotency claim is inserted (a claim INSERT takes a
   `FOR KEY SHARE` lock on the bill via the `bill_id` FK, which
   deadlocked against `FOR UPDATE` in the Postgres concurrency test).
   Reject if the bill is not `issued`/`payment_requested` or the split
   is already `paid` → `409` with the existing payment id + receipt
   number in details.
2. Idempotency claim, scope `(restaurant_id, bill_id, endpoint,
   key)` — same key + same body → replay `201` with the original
   payment (`Idempotency-Replayed: true`, no WS broadcast); same key +
   different body → `422`; a failed payment rolls the claim back, so a
   corrected retry with the same key succeeds.
3. Validate `amount_paise` > 0 and ≤ amount due on the target (whole
   bill outstanding, or split outstanding), else `422`.
4. Create the `payments` row (`method=cash`, `collected_by=NULL`,
   `change_due_paise=0`, `receipt_number` generated
   `R-YYYY-MM-DD-NNN`, sequential per restaurant per day).
5. Update `amount_paid_paise`; if the target is fully paid → split
   `paid` + `paid_at` (or bill `paid` + `paid_at` when the whole bill
   or all splits are paid).
6. Commit, then broadcast `payment.recorded` and (if fully paid)
   `bill.paid` after commit. No event on replay or on a failed payment.

Response: `201` with the payment + the updated bill + receipt data.
**A guest token on this path returns `401`** (missing staff key — the
dev gate returns 401 for a missing key; a kitchen key sent *as* the
staff key → 403). A second confirmation for the same split with a NEW
key → `409` (status guard). Session close after all bills are paid is
NOT in this stage (deferred in Stage 5, still deferred).

### 7.3 Refunds & receipts — deferred / as built

Refunds (`POST /bills/:id/refunds`), bill void (`POST /bills/:id/void`),
the daily cash report (`GET /admin/cash-report`) and the owner
bills/payments history are DEFERRED to Stage 7 (owner auth + D3).
`GET /payments` (cash report source) does not exist yet. The `refunds`
table in `database-design.md` § 3.20 is a blueprint row, not a built
table.

| Method & path | Auth | Notes |
|---|---|---|
| `GET /api/v1/guest/bill/receipt` | `X-Table-Token` | Receipt data (JSON), only after paid (409 before). |
| `GET /api/v1/staff/bills/:id/receipt?restaurant_id=` | `X-Staff-Dev-Key` | Same receipt data for staff. |

Receipt data: restaurant + branch name, bill number, receipt number,
table number, order numbers, itemised lines (from `bill_items`
snapshots), subtotal, tax status (`"Tax: not configured — no tax rate
has been set for this restaurant"`), total payable, amount received (Σ
confirmed payments), outstanding balance, payment method `"Cash"`,
payment timestamp(s), receipt timestamp (= `paid_at`). No PDF in this
stage — the receipt is a structured API response + a rendered view in
both UIs. A receipt is never issued as paid before the backend confirms
a successful cash payment (enforced by the 409 + tests).


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
GET /ws/kitchen?restaurant_id=<uuid>&dev_key=<key>   # kitchen — Stage 4, LIVE
                                                # (dev-only key; never logged)
GET /ws/staff?restaurant_id=<uuid>&dev_key=<key>     # staff — Stage 5, LIVE
                                                # (dev-only key; never logged)
```

> **Stage 5 status (23 Nov 2026): `/ws/kitchen` AND `/ws/staff`
> exist.** The staff/guest endpoints above still land in Stage 8.
> Both live channels reuse the SAME in-process `ConnectionManager`
> (`backend/app/realtime/manager.py`) — one manager, two channels
> (`kitchen:{restaurant_id}` and `staff:{restaurant_id}`); a client
> on one channel never receives the other's events. Each socket
> validates the dev key + restaurant BEFORE accept (bad/missing
> key → close 4401, unknown restaurant → 4404, production → close
> 4403 `staff_auth_not_configured` / `kitchen_auth_not_configured`).
> **Single-process only:** no Redis broker — a second `api`
> replica would not receive events published here (architecture.md
> § 6.3). Events published so far: `order.created` (guest order
> placed, broadcast after the placement transaction commits;
> replays are NOT broadcast — staff on the same restaurant channel
> receives it), `order.status_changed` (kitchen transition AND
> staff serve, broadcast after the transition commits — the staff
> client receives kitchen transitions too), `help_call.opened`
> (guest help call created, broadcast after commit),
> `help_call.acknowledged` and `help_call.resolved` (staff
> actions, broadcast after commit). A failed/rolled-back request
> broadcasts nothing. The envelope is § 9.3; `version` is a
> per-restaurant in-memory counter.

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
| `kitchen:{restaurant_id}` | KDS | **Stage 4, LIVE:** dev key matches + restaurant exists |
| `staff:{restaurant_id}` | Staff board | **Stage 5, LIVE:** dev key matches + restaurant exists |

A client subscribes by sending `{ "action": "subscribe", "channel": "…" }`
after connect. Subscribing to a channel it is not authorised for returns an
error event and does not subscribe it.

### 9.3 Event catalogue

All events use the envelope in `architecture.md` § 6.1
(`type`, `restaurant_id`, `table_id`, `data`, `version`, `occurred_at`).
`data` carries identifiers + a summary only.

| Event | Published when | Channels |
|---|---|---|
| `order.created` | Order committed | `restaurant:*`, `kitchen:*` (per station tickets), `table-session:*` — **live on `kitchen:{id}` + `staff:{id}` since Stages 4/5** |
| `order.status_changed` | Order status changes | `restaurant:*`, `table-session:*` — **live on `kitchen:{id}` + `staff:{id}` since Stages 4/5 (kitchen transitions AND staff serve)** |
| `order.item_ready` | An item becomes `ready` | `restaurant:*`, `table-session:*` |
| `order.item_status_changed` | Any item status change | `restaurant:*`, `kitchen:*` |
| `ticket.created` | Ticket created (with the order) | `kitchen:{station}` |
| `ticket.status_changed` | Ticket status derived-change | `kitchen:{station}`, `restaurant:*` |
| `table.status_changed` | Table status changes | `restaurant:*` |
| `help_call.opened` | Guest requests help | `restaurant:*`, `table-session:*` — **live on `staff:{id}` since Stage 5** |
| `help_call.acknowledged` | Waiter accepts | `restaurant:*`, `table-session:*` — **live on `staff:{id}` since Stage 5** |
| `help_call.resolved` | Waiter resolves | `restaurant:*`, `table-session:*` — **live on `staff:{id}` since Stage 5** |
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

> **Stage 3.2 status (23 Nov 2026): single transaction.** The
> claim, the order and the stored response are created in ONE
> transaction (claim INSERT + validation + order creation +
> response fill, one commit). There is no separately committed
> claim for a process crash to orphan: if the process dies
> mid-placement, PostgreSQL aborts the transaction when the
> connection drops and nothing persists — no claim, no partial
> order. Scope is `(restaurant_id, table_session_id, endpoint,
> key)`: a key is per-session and can never leak across sessions.
> A replay returns the stored response verbatim with the original
> status and header `Idempotency-Replayed: true`. Rate limits
> (slowapi, per IP): `GET /guest/t/:tableToken` 120/minute,
> `POST /orders` 60/minute — generous caps to blunt brute force /
> abuse, not to police guests.

- **Where:** `POST /orders` only in v1. The client generates a UUID
  `Idempotency-Key` per placement attempt and reuses it on retry.
- **Storage:** `idempotency_keys` (see `database-design.md` § 3.26), keyed by
  `(restaurant_id, table_session_id, endpoint, key)`, with a hash of the canonical request body
  and the stored response.
- **Single transaction:** the service inserts the claim row
  (`response_status` / `response_body` / `order_id` NULL) and
  FLUSHES it — no commit — then validates and prices the lines,
  creates the order (setting the claim's `order_id` in the same
  transaction), fills in the stored response, and commits once.
  Any failure (validation, inactive session, unexpected error)
  rolls the whole transaction back: no claim row survives a failed
  placement, so a corrected retry with the same key always
  proceeds, and a partial order is never exposed.
- **Concurrency (PostgreSQL unique-index speculative insertion):**
  a concurrent INSERT of the same scoped key BLOCKS until the
  owner's transaction commits or aborts. If the owner commits,
  the waiter gets a unique violation, reads the now-committed row
  and returns the stored response verbatim
  (`Idempotency-Replayed: true`). If the owner aborts — including
  a process crash, which PostgreSQL treats as an abort when the
  connection drops — the waiter's INSERT proceeds and it becomes
  the owner. No polling, no lease, no fencing. The wait is bounded
  by `SET LOCAL lock_timeout = '10s'` (PostgreSQL only, set at the
  start of the placement transaction); a timeout yields `409`
  `idempotency_in_progress` — retry with the same key. Verified by
  test: a 200 ms patched timeout returns the 409 promptly.
- **Legacy rows (Stage 3.1 compatibility, one-time path):** rows
  committed with `response_body` NULL by the old three-transaction
  design are handled in the conflict path only. If `order_id` is
  set, the response is rebuilt from the order and returned as a
  replay. If `order_id` is NULL and the row is older than 60 s, it
  is deleted and the claim is retried once (its owner can never
  come back). A newer pending row, or an expired row handled the
  same way, is deleted and retried; a fresh pending row yields
  `409` `idempotency_in_progress`. A different body always yields
  `422` `idempotency_key_reused`, including against legacy rows.
  In steady state (Stage 3.2) a committed row always has its
  response filled, so these branches are not expected to fire.
- **Failure cleanup:** there is none to do beyond the rollback —
  see "Single transaction" above. The old Stage 3.1 behaviour
  (delete the claim after a failed order creation) is subsumed by
  it.
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
| 409 | `idempotency_in_progress` | An order with this `Idempotency-Key` is already being placed (lock timeout while the owner transaction is still in flight, or a fresh pending pre-3.2 claim) — retry with the same key |
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
