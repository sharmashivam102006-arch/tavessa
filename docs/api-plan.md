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

## 2. Authentication — `POST /api/v1/auth/*` (as built in Stage 7, 24 Nov 2026)

| Method & path | Auth | Body / notes | Returns |
|---|---|---|---|
| `POST /api/v1/auth/login` | None (rate-limited 10/min/IP via slowapi) | `{ email, password }` | `{ accessToken, tokenType: "Bearer", user: { id, email, role, restaurantId, branchId, permissions, mustChangePassword, … } }` + HttpOnly refresh cookie (`Path=/api/v1/auth`, `SameSite=Lax`, `Secure` in production). Wrong credentials OR deactivated user → `401` generic `"Email or password is incorrect."` — never leaks which check failed. Success and failure both write audit rows (`login.success` / `login.failed`); failure rows contain no password or token. `last_login_at` recorded. |
| `POST /api/v1/auth/refresh` | Refresh cookie | — | New access token + **rotated** refresh cookie (old token revoked, same family). Reuse of a revoked refresh token revokes the **whole family** → `401`. |
| `POST /api/v1/auth/logout` | Refresh cookie | — | Revokes the presented refresh token; clears the cookie. |
| `POST /api/v1/auth/change-password` | JWT | `{ currentPassword, newPassword }` (new ≥ 8 chars) | Revokes **all** refresh sessions for the user, clears `must_change_password`; the user re-logs in. Audited (`user.password_changed`). |
| `GET /api/v1/auth/me` | JWT | — | Current user, role, permission flags, restaurant/branch. |

Access tokens are JWT (HS256, `SECRET_KEY` from settings), 15-minute
expiry, claims `sub` (user id), `restaurant_id`, `branch_id`, `role`,
`jti`. Passwords are Argon2id hashes via `core/security.py` — never
plaintext, never logged, never returned in any response. Every request
re-validates the user from the database (`get_current_user`), so
deactivation is effective immediately on **all** endpoints, not only at
token expiry (stronger than the brief's minimum). The first owner is
created only via the bootstrap CLI — there is **no** public
owner-registration endpoint and no hardcoded/default credentials:

```
python -m app.auth.bootstrap --restaurant-slug zaika --email owner@zaika.example
# prompts for the password TWICE via getpass (never in argv/logs);
# refuses if ANY owner already exists for the restaurant or the email is taken.
```

Login failures return `401` with the generic message above. A user
created by an owner/manager has `must_change_password=true` and must
change the password on next login (the login response carries the flag;
`/auth/change-password` clears it). Permissions live in
`backend/app/auth/permissions.py` (`PERMISSIONS` map +
`require_permission(capability)`); the role/permission matrix is
`architecture.md` § 8.3. Manager "Configurable" permissions are boolean
flags on the user record (`can_manage_staff`, `can_manage_settings`,
`can_refund`, `can_void`), default False.

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
>
>    **Stage 7 update (24 Nov 2026):** the 503 is gone. Production
>    staff routes now require a JWT and return **401
>    `unauthenticated`** for a dev key (or a missing/invalid token);
>    in dev, a Bearer JWT is accepted too and the dev key keeps
>    working. See § 2 and `architecture.md` § 8.
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
> **Stage 7 update (24 Nov 2026):** the 503 is gone — same change
> as staff above: production requires a JWT (401 for a dev key),
> dev accepts JWT or the dev key.
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

## 7. Bills, splits, payments, receipts — as built in Stage 6 (23 Nov 2026), extended in Stage 7 (24 Nov 2026)

Cash only. There is no guest endpoint that creates a payment, no UPI /
card / gateway / wallet path. Refunds, bill void, the daily cash report
and owner bills/payments history were deferred out of Stage 6 and **are
built in Stage 7** (§ 7.3, § 8) — they need owner auth, which now exists.
The brief's event names are used, not the older names in the original
§ 7 draft: `bill.created`, `bill.requested`, `payment.recorded`,
`bill.paid` (`bill.payment_requested` / `payment.confirmed` are NOT
used). The audit trail is the append-only `audit_logs` table (Stage 7);
`payment.recorded` is also written as an audit row in the same
transaction as the payment (a replay writes nothing new; a failed
payment rolls the audit row back too).

### 7.1 Bills

| Method & path | Auth | Notes |
|---|---|---|
| `POST /api/v1/guest/bill` | `X-Table-Token` | Create-or-retrieve for the active session. No bill → generate (status `issued`, broadcast `bill.created` after commit); open `issued` bill → regenerate from current non-cancelled orders (decision 8) and return 200; open `payment_requested` bill → return as-is (frozen); paid bill + new billable items → NEW bill with only unreferenced items; no billable items and no bill → 409 `nothing_to_bill`. Returns bill + items + splits + payments + totals + outstanding. |
| `GET /api/v1/guest/bill` | `X-Table-Token` | The session's open bill, else the most recent paid bill (receipt view); none → 404 `bill_not_found`. Another session's bill → 404, not 403. |
| `PATCH /api/v1/guest/bill/request-payment` | `X-Table-Token` | Empty body. `issued → payment_requested` (unpaid splits → `payment_requested` too); creates NO payment row; already requested → 200 idempotent; paid → 409. Broadcasts `bill.requested` after commit (only on the actual transition). |
| `POST /api/v1/guest/bill/splits` | `X-Table-Token` | `{ "mode": "equal", "parts": N }` or `{ "mode": "custom", "amountsPaise": [...] }`. Server-validated: equal parts ≥ 2 (remainder 1p to earliest parts); custom sums exactly to total, each > 0, else 422. Replaces existing UNPAID splits in one transaction; refused once any split is paid or `payment_requested` → 409. Only while the bill is `issued`/`payment_requested`. |
| `GET /api/v1/guest/bill/receipt` | `X-Table-Token` | Only after the bill is paid, else 409 `receipt_not_available`. |
| `GET /api/v1/staff/bills?restaurant_id=&status=&table_id=&page=&per_page=` | JWT (staff) in production; dev key or JWT in dev (production dev key → 401) | List, `payment_requested` first, then `issued_at` desc. Row: table number, bill number, status, total, amount paid, outstanding, `payment_requested_at`, order count. Unknown status → 422; unknown/wrong-restaurant table → 404. Returns paid bills too — filter with `status=`; the staff queue drops paid rows itself. |
| `GET /api/v1/staff/bills/:id?restaurant_id=` | JWT (staff) in production; dev key or JWT in dev | Full bill: items, splits, payments (history), totals, outstanding, table/session info. Unknown id or wrong restaurant → 404. |
| `GET /api/v1/staff/bills/:id/payments?restaurant_id=` | JWT (staff) in production; dev key or JWT in dev | Payment history for the bill, newest first. |
| `GET /api/v1/staff/bills/:id/receipt?restaurant_id=` | JWT (staff) in production; dev key or JWT in dev | Receipt data, only after paid, else 409. |
| `GET /api/v1/staff/tables/:id` | JWT (staff) in production; dev key or JWT in dev | Table detail now includes `open_bill` (bill number, status, total, outstanding) when one exists. |

Bill numbers are `B-YYYY-MM-DD-NNN`, sequential per restaurant per day
under the restaurant row lock (same pattern as `ZAIKA-0001` order
numbers). Calculation is server-side only, integer paise, from
`order_items` snapshots: line = unit × quantity; subtotal = Σ lines;
tax: while a bill is `issued` it recomputes with the tax config
effective NOW (§ 8.5); at the freeze point (→ `payment_requested`, or at
payment time if paid without a prior request) the tax policy + computed
tax are snapshotted onto the bill (`tax_rate_bps_snapshot`,
`tax_mode_snapshot`, `tax_label_snapshot`) and never change afterwards.
No config = "not configured", never "0% GST". Service charge / discount
remain 0. Total = subtotal + tax (exclusive) or subtotal (inclusive,
tax_paise = the tax component inside it); outstanding = total − amount
paid — except a voided bill, whose outstanding is 0 (written off). Client-supplied price/total
fields → 422. A partially-paid `issued` bill does not regenerate; a
no-change regen does not bump `version`.

### 7.2 Payments — cash confirmation (staff only)

```
POST /api/v1/staff/bills/:id/payments?restaurant_id=…
Authorization: Bearer <access-token>      # production (Stage 7); in dev,
                                          # X-Staff-Dev-Key: <dev key> also works
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
4. Create the `payments` row (`method=cash`,
   `collected_by` = the confirming user's id when called with a JWT
   (NULL only for dev-key calls in dev), `change_due_paise=0`,
   `receipt_number` generated `R-YYYY-MM-DD-NNN`, sequential per
   restaurant per day). The `payment.recorded` audit row is written in
   the same transaction.
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

### 7.3 Bill void, cash refunds, daily cash report — built in Stage 7 (24 Nov 2026)

| Method & path | Auth | Notes |
|---|---|---|
| `POST /api/v1/admin/bills/:id/void` | Owner, or manager with `can_void` | Body `{ "reason": "…" }` (required). Only from `issued`/`payment_requested` AND with zero confirmed payments — any confirmed payment → `409` "refund required first". Repeat void → `409` with the bill. Sets `status=voided` + `voided_at`/`void_reason`/`voided_by`; the bill is NOT deleted; its items become billable again (voided bills are excluded from the referenced-items computation and from the one-open-bill rule). Audited (`bill.voided`). A voided bill's outstanding is 0. |
| `POST /api/v1/admin/bills/:id/refunds` | Owner, or manager with `can_refund`; `Idempotency-Key` REQUIRED | Body `{ "paymentId", "amountPaise", "reason" }`. One transaction, bill + payment rows locked first. Amount > 0 and ≤ payment amount − prior refunds, else `422`. Creates the `refunds` row (reference `RFD-YYYY-MM-DD-NNN`, sequential per restaurant per day under the restaurant row lock); payment status → `refunded` (fully) or `partially_refunded`; bill status → `refunded` / `partially_refunded`. Successful payments are NEVER deleted/overwritten. Duplicate key + same body → replay (`Idempotency-Replayed: true`); different body → `422`. Concurrent refunds cannot exceed the refundable amount (tested on Postgres: 8-way concurrent same-key → 1 refund + 7 replays; 8-way different-key full-amount → exactly 1 succeeds). Audited (`refund.created`). |
| `GET /api/v1/admin/reports/daily-cash?date=&format=csv` | Owner/manager `view_financials` | Branch-timezone day bounds. Gross cash collected, cash refunds, net cash, outstanding bills (open bills only, as of now), bill count, payment count. `?format=csv` returns `{"data": {"csv": "…"}}` (JSON-wrapped, downloaded via Blob on the frontend). Gross − refunds = net; outstanding matches open bills (verified live). |
| `GET /api/v1/guest/bill/receipt` | `X-Table-Token` | Receipt data (JSON), only after paid (409 before). |
| `GET /api/v1/staff/bills/:id/receipt?restaurant_id=` | JWT (staff) in production; dev key or JWT in dev | Same receipt data for staff. |

Receipt data: restaurant + branch name, bill number, receipt number,
table number, order numbers, itemised lines (from `bill_items`
snapshots), subtotal, tax status (snapshot label when frozen, else
`"Tax: not configured — no tax rate has been set for this restaurant"`),
total payable, amount received (Σ confirmed payments), outstanding
balance, payment method `"Cash"`, payment timestamp(s), receipt
timestamp (= `paid_at`). No PDF in this stage — the receipt is a
structured API response + a rendered view in both UIs. A receipt is
never issued as paid before the backend confirms a successful cash
payment (enforced by the 409 + tests).


## 8. Admin — owner dashboard (as built in Stage 7, 24 Nov 2026)

All paths are under `/api/v1/admin` and require a JWT (`Authorization:
Bearer …`). Every endpoint validates the caller's role/permission
server-side (`require_permission`) and scopes to the caller's restaurant
(branch-scoped users are pinned to their branch — another branch's data
→ 403). Responses are `{"data": …}`; errors are
`{"error": {"code", "message", "details"}}`. There is **no** `?demo=1`
mode on `/admin` — admin data is real by definition. The draft table
that used to sit here (`/menu/categories`, `/admin/overview`,
`/admin/staff`, QR rotation, review moderation, image upload) was a
pre-Stage-7 sketch: QR rotation, review moderation (D6), menu image
upload and staff assignments are **deferred** (roadmap Stage 7
decisions); the endpoints below are what actually shipped.

### 8.1 Dashboard

| Method & path | Auth | Notes |
|---|---|---|
| `GET /api/v1/admin/dashboard/overview?restaurant_id=&branch_id=&preset=&from=&to=` | `view_dashboard` (owner/manager) | Presets `today` / `yesterday` / `last7` / `last30` / `custom` (custom needs `from`+`to`). Date boundaries are computed in the **branch timezone** (`zoneinfo`, branch `timezone`, default `Asia/Kolkata`). Returns `grossSalesPaise` (Σ totals of non-voided bills issued in the period), `cashCollectedPaise` (Σ confirmed payments collected in the period), `refundsPaise`, `netCashPaise` (collected − refunds), `outstandingPaise` (Σ outstanding of open bills, as of now — NOT period-bound), `averageOrderValuePaise` (gross / bill count), `ordersByStatus` (from the orders table), `recentPayments` (latest 10), `recentActivity` (latest audit rows), `bestSellers` (top 10 by quantity from `order_items` in the period). Unpaid bills are never labelled revenue collected: gross, cash, refunds, net and outstanding are separate fields. Aggregated in SQL — no N+1. |

### 8.2 History

| Method & path | Auth | Notes |
|---|---|---|
| `GET /api/v1/admin/orders?status=&table_id=&from=&to=&page=` | `view_dashboard` | Paginated order history with filters. |
| `GET /api/v1/admin/bills?status=&page=` | `view_financials` | Bill number, table, issue date, status, total, collected, outstanding (0 for voided bills), refund amount, net collected. |
| `GET /api/v1/admin/bills/:id` | `view_financials` | Full bill incl. items, splits, payments, tax snapshot, void info. |
| `GET /api/v1/admin/payments?page=` | `view_financials` | Receipt number, bill, amount, method, `collected_at`, actor name where available (`collected_by`). |
| `GET /api/v1/admin/refunds?page=` | `view_financials` | Reference, payment/receipt reference, amount, reason, actor, timestamp. |
| `GET /api/v1/admin/reports/daily-cash?date=&format=csv` | `view_financials` | See § 7.3. |

### 8.3 Menu management

| Method & path | Auth | Notes |
|---|---|---|
| `GET /api/v1/admin/menu/categories` | `manage_menu` | List with item counts. |
| `POST /api/v1/admin/menu/categories` | `manage_menu` | Create. Audited. |
| `PATCH /api/v1/admin/menu/categories/:id` | `manage_menu` | Rename / activate. Audited. |
| `POST /api/v1/admin/menu/categories/reorder` | `manage_menu` | Bulk reorder — the body is the full ordered list of category ids; validated as a whole, then applied. |
| `GET /api/v1/admin/menu/items?page=` | `manage_menu` | Paginated items with category name, station, variants, add-ons. |
| `POST /api/v1/admin/menu/items` | `manage_menu` | Create: name, description, `pricePaise` INT ≥ 0, `isVeg`, `isAvailable`, `isPopular`, `imageUrl`, `station`, `sortOrder`. Server-side validation; integer paise. |
| `PATCH /api/v1/admin/menu/items/:id` | `manage_menu` | Edit, incl. the availability toggle (`isAvailable` false removes the item from guest menus for NEW orders only; placed orders are unaffected). **Price changes are audited** (`menu.price_changed`, old → new) and never rewrite `order_items`/`bill_items` snapshots (tested: change a price, re-fetch an old bill, totals unchanged). |
| `POST /api/v1/admin/menu/items/:id/variants` | `manage_menu` | Add a variant. |
| `DELETE /api/v1/admin/menu/items/:id/variants/:variantId` | `manage_menu` | Delete a variant (204). Ownership validated — a variant id that does not belong to the item → 404. |
| `POST /api/v1/admin/menu/items/:id/add-ons` | `manage_menu` | Add an add-on. |
| `DELETE /api/v1/admin/menu/items/:id/add-ons/:addOnId` | `manage_menu` | Delete an add-on (204). Ownership validated as for variants. |

Menu image upload is **deferred** (roadmap Stage 7 decisions) — `imageUrl`
is a plain URL field, not an upload.

### 8.4 User management

| Method & path | Auth | Notes |
|---|---|---|
| `GET /api/v1/admin/users?page=` | `manage_staff` (owner, or manager with `can_manage_staff`) | Restaurant-scoped, paginated. Never returns `password_hash` or refresh tokens. |
| `POST /api/v1/admin/users` | `manage_staff` | Create: email, role, `branchId`, creator-entered initial password. New accounts have `must_change_password=true`. Role rule: only an owner can create `owner`/`manager` roles; a manager with the flag can create `waiter`/`kitchen` only. |
| `PATCH /api/v1/admin/users/:id` | `manage_staff` | Role / flags / branch. Never allow changing your own role to owner, or demoting the last active owner → 409. Role changes audited. |
| `POST /api/v1/admin/users/:id/deactivate` | `manage_staff` | Sets `is_active=false` + `deactivated_at`, revokes ALL refresh sessions immediately; deactivation is effective immediately everywhere (every request re-validates the user). Last-active-owner guard → 409. Audited. |
| `POST /api/v1/admin/users/:id/reset-password` | `manage_staff` | Owner/manager-with-flag sets a new password + `must_change_password=true` + revokes all sessions. Audited. |

### 8.5 Restaurant / branch settings + tax

| Method & path | Auth | Notes |
|---|---|---|
| `GET /api/v1/admin/settings/restaurant` | `manage_settings` | Restaurant settings. |
| `PATCH /api/v1/admin/settings/restaurant` | `manage_settings` (owner, or manager with `can_manage_settings`) | Display name, contact details, address, currency — validated as ISO-4217 `[A-Z]{3}`, default INR. No legal details are invented. Audited. |
| `GET /api/v1/admin/settings/branches` | `manage_settings` | Branch list. |
| `PATCH /api/v1/admin/settings/branches/:id` | `manage_settings` | Name, timezone (validated against `zoneinfo`), contact fields, address. Audited. |
| `GET /api/v1/admin/settings/tables` | `manage_settings` | Table list. |
| `POST /api/v1/admin/settings/tables` | `manage_settings` | Add a table: number unique per branch (duplicate → 409), status `available`. **No delete in this stage** (recorded). Audited. |
| `GET /api/v1/admin/settings/tax` | `configure_tax` (owner only) | Current config + full history (newest first). Unconfigured state is shown explicitly. |
| `POST /api/v1/admin/settings/tax` | `configure_tax` (owner only) | New config `{ enabled, rateBps (0–10000, required when enabled), mode: inclusive\|exclusive, label (e.g. "GST"), effectiveFrom }`. `effectiveFrom` must be now-or-future; no two configs may share the same `effectiveFrom` (409). The active config at any instant is the one with the greatest `effectiveFrom` ≤ now — inherently unambiguous. History is rows, never updates. Audited (`tax.config_created`). **Never invent a rate:** no config = "unconfigured"; `enabled=true` with rate 0 is an explicit owner choice, distinct from unconfigured. No tax compliance is claimed anywhere. |

### 8.6 Bills — void and refunds

See § 7.3 (`POST /api/v1/admin/bills/:id/void`,
`POST /api/v1/admin/bills/:id/refunds`).

### 8.7 Audit log

| Method & path | Auth | Notes |
|---|---|---|
| `GET /api/v1/admin/audit-logs?action=&entity_type=&from=&to=&page=` | `view_dashboard` | Append-only, paginated, filterable. Responses never contain secrets — audit writes pass only safe details (the writer drops any key containing `password`/`token`/`secret`/`hash`/`otp`/`cookie`/`dev_key`/`authorization` and truncates values to 500 chars). **No endpoint updates or deletes audit rows** (`PATCH`/`DELETE /admin/audit-logs/:id` → 404/405 — tested). |

Events written (in the same transaction as the action where possible):
`login.success` / `login.failed`, `user.created` / `user.role_changed` /
`user.deactivated` / `user.password_reset` / `user.password_changed`,
`menu.category_created` / `menu.category_updated` / `menu.item_created` /
`menu.price_changed` / `menu.variant_created` / `menu.add_on_created`,
`tax.config_created`, `bill.voided`, `refund.created`,
`settings.restaurant_updated` / `settings.branch_updated` /
`settings.table_created`, `payment.recorded`. Failed operations write
**no** success record (the audit row is written only on success, except
login failures which are recorded as failures).


## 9. WebSocket API

### 9.1 Connect

```
POST /api/v1/ws/tickets                     # authenticated (JWT) — Stage 7
                                            # body { "channel": "kitchen" | "staff" }
                                            # returns { "ticket", "expiresAt" } — ticket is a
                                            # random opaque string, SHA-256 hashed in
                                            # ws_tickets, single-use, 60 s expiry.
                                            # Kitchen: needs view_kitchen or update_kitchen;
                                            # staff: needs serve_orders or confirm_cash or
                                            # view_dashboard. Never logged.
GET /ws/kitchen?restaurant_id=<uuid>&ticket=<ticket>   # kitchen — Stage 4, LIVE
                                            # (ticket in production; ?dev_key= still works
                                            # in dev; never logged)
GET /ws/staff?restaurant_id=<uuid>&ticket=<ticket>     # staff — Stage 5, LIVE
GET /ws/restaurant?token=<access-jwt>          # staff / kitchen / owner — Stage 8
GET /ws/table-session?token=<table-token>       # guest — Stage 8
```

> **Stage 7 status (24 Nov 2026): `/ws/kitchen` AND `/ws/staff`
> authenticate by WS ticket in production.** The ticket is stored
> server-side (DB table `ws_tickets`, hashed, single-use, ~60 s
> expiry) — NOT a signed JWT in the URL, because single-use requires
> server state. The staff/guest endpoints above still land in Stage 8.
> Both live channels reuse the SAME in-process `ConnectionManager`
> (`backend/app/realtime/manager.py`) — one manager, two channels
> (`kitchen:{restaurant_id}` and `staff:{restaurant_id}`); a client
> on one channel never receives the other's events. Validation at
> connect: hash lookup, not expired, not used, user still active and
> still holding the channel permission, restaurant matches — then
> `used_at` is marked (single-use) and the socket is accepted.
> Failure codes: bad/expired/used ticket → close 4401; wrong-role
> ticket or dev key in production → close 4403; restaurant mismatch
> → close 4404; unknown restaurant → 4404. **Accept-then-close:**
> the server `accept()`s the socket first and then closes it with
> the code — uvicorn turns a close-before-accept into a bare HTTP
> 403, so the documented codes never reached real clients before
> this change (only the TestClient saw them). No data flows on a
> refused socket. **Single-process only:** no Redis broker — a
> second `api` replica would not receive events published here
> (architecture.md § 6.3). Events published so far: `order.created`
> (guest order placed, broadcast after the placement transaction
> commits; replays are NOT broadcast — staff on the same restaurant
> channel receives it), `order.status_changed` (kitchen transition
> AND staff serve, broadcast after the transition commits — the staff
> client receives kitchen transitions too), `help_call.opened`
> (guest help call created, broadcast after commit),
> `help_call.acknowledged` and `help_call.resolved` (staff
> actions, broadcast after commit), `bill.created` /
> `bill.requested` / `payment.recorded` / `bill.paid` (staff
> channel, Stage 6). A failed/rolled-back request broadcasts
> nothing. The envelope is § 9.3; `version` is a per-restaurant
> in-memory counter.

### 9.2 Channels & authorisation

| Channel | Subscriber | Authorisation check |
|---|---|---|
| `restaurant:{restaurant_id}` | Staff, kitchen, owner | JWT restaurant matches |
| `table-session:{session_id}` | Guest of that session; staff viewing that table | Token session matches, or JWT restaurant matches + table in scope |
| `kitchen:{restaurant_id}:{station_id}` | KDS | JWT role kitchen/owner + station belongs to restaurant |
| `kitchen:{restaurant_id}` | KDS | **Stage 4, LIVE; Stage 7 ticket auth:** valid single-use ticket (or dev key in dev) + restaurant exists |
| `staff:{restaurant_id}` | Staff board | **Stage 5, LIVE; Stage 7 ticket auth:** valid single-use ticket (or dev key in dev) + restaurant exists |

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
