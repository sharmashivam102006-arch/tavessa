# Tavessa — Architecture

> Status: Blueprint v1 — 23 Nov 2026. No application code built yet.
> Companion docs: `project-overview.md`, `database-design.md`, `api-plan.md`,
> `development-roadmap.md`.

## 1. Architectural style — modular monolith

Tavessa is a **modular monolith**: one FastAPI application, one PostgreSQL
database, one frontend application, deployed as a small set of containers.
Modules are separated by domain with explicit boundaries (routes → services →
repositories → models), so a module can be extracted later if — and only if —
there is a proven scaling reason. There is no such reason in v1.

**Why not microservices:** four interfaces share one order, one table, and one
bill. Splitting them into services would turn a single database transaction
(order + tickets + table status) into a distributed transaction, add network
failure modes to the golden path (scan → order → kitchen), and multiply
operational cost for a single-restaurant demo. The brief explicitly forbids
microservices and Kubernetes; this choice follows it.

**Why not a single unstructured monolith:** without module boundaries, order,
billing, and kitchen logic will tangle in the first month. The module rules in
§ 3 are the guardrail: routes never touch the database directly, services never
build HTTP responses, and realtime code never writes business state.

## 2. System context

```
                ┌──────────────────┐
                │  Guest phone     │  /t/:tableToken
                │  (mobile web)    │
                └────────┬─────────┘
                         │ HTTPS + WSS
┌──────────────────┐     │
│  Waiter tablet   │─────┤
│  /staff          │     │
└──────────────────┘     │
                         ▼
┌──────────────────┐  ┌─────────────────────────────────┐
│  KDS screen      │─▶│  Tavessa backend (FastAPI)      │
│  /kitchen        │  │  REST + WebSockets, one process │
└──────────────────┘  │  Modular monolith               │
┌──────────────────┐  └──────┬──────────────┬───────────┘
│  Owner dashboard │──▶      │              │
│  /admin          │         ▼              ▼
└──────────────────┘  ┌──────────┐   ┌──────────┐
                      │ Postgres │   │  Redis*  │
                      │ (source  │   │ (*only   │
                      │  of      │   │  when    │
                      │  truth)  │   │  needed) │
                      └──────────┘   └──────────┘

* Redis is NOT in the v1 critical path. See § 6.
```

All four clients talk to the same backend. There is no guest-only backend, no
kitchen-only backend, and no direct client-to-client communication.

**Deployment shape (v1):** Docker Compose with three services — `api`
(FastAPI + Uvicorn), `web` (frontend static build served by Nginx or the Vite
preview server in dev), `db` (PostgreSQL 16). Redis is a fourth service only
when § 6 says it is needed. One `api` replica in v1; horizontal scaling is a
later decision, not a v1 requirement.

## 3. Backend — modular monolith, layer by layer

### 3.1 Module map

Every domain is a vertical slice through the same layers. A request flows
**routes → services → repositories → models**, and events flow
**services → event bus → WebSocket manager** only after the transaction commits.

| Layer | Responsibility | May import | Must never |
|---|---|---|---|
| `api/v1/` (routes) | Parse/validate HTTP input, call one service, return a response. Auth via dependencies. | services, schemas, core deps | Touch SQLAlchemy sessions directly, contain business rules |
| `services/` | Business logic + transactions: validate, compute totals, enforce status machines, write audit logs, publish events after commit. | repositories, models, realtime bus | Build HTTP responses, read request headers |
| `repositories/` | Data access: queries, filters, pagination, row locking. Thin; no business rules. | models, db session | Call services, publish events |
| `models/` | SQLAlchemy 2.0 models, relationships, constraints (see `database-design.md`). | — | Contain business logic |
| `schemas/` | Pydantic v2 request/response models. Money as `int` paise. | — | Contain DB logic |
| `core/` | Config, security, dependencies, errors, logging. | — | Domain logic |
| `realtime/` | WebSocket connection manager, channel subscriptions, event envelope, broker abstraction. | core | Write to the database, decide business state |
| `db/` | Engine, session factory, Base, Alembic env + migrations. | models | Business logic |

**One service per domain** (not one service per endpoint):

- `auth_service` — login, tokens, password hashing, QR token issue/rotate/revoke
- `restaurant_service` — restaurants, branches, settings
- `menu_service` — categories, items, variants, add-ons, availability
- `table_service` — tables, floor map, sessions, QR tokens
- `order_service` — order placement, validation, pricing, status transitions
- `kitchen_service` — tickets, station queues, item status, timers
- `billing_service` — bills, splits, payment confirmation, receipts, refunds
- `help_service` — help calls
- `staff_service` — staff, assignments, roles
- `review_service` — reviews
- `analytics_service` — read-only aggregates for the owner dashboard
- `audit_service` — audit log writes (called by other services, never bypassed)

Cross-domain rules live in the owning service and are called explicitly — e.g.
`order_service.place_order()` calls `kitchen_service.create_tickets()` inside
the same transaction; it does not duplicate ticket logic.

### 3.2 Request lifecycle (the rule that prevents most bugs)

```
HTTP request
  → route: authenticate + authorise (dependency) + validate Pydantic schema
  → service: load rows (with row locks where needed), validate business rules,
             compute money server-side, mutate in ONE transaction, commit
  → service: write audit log in the same transaction (for sensitive ops)
  → AFTER commit: publish event to the realtime bus
  → realtime: broadcast to subscribed channels
  → route: return response (the committed state)
```

**Save to PostgreSQL before broadcasting.** A WebSocket message is a hint that
something changed; the database is the truth. A client that receives an event
re-fetches (or applies the event only if it carries a monotonically increasing
version it can reconcile). On reconnect, every client re-fetches its current
state via REST. No client ever reconstructs state from a replayed event log.

### 3.3 Configuration

- All secrets and environment-specific values come from environment variables
  (`.env` locally, never committed; `.env.example` documents every key).
- `core/config.py` is a single Pydantic `BaseSettings` class; no module reads
  `os.environ` directly.
- Required in v1: `DATABASE_URL`, `SECRET_KEY` (JWT signing), `QR_TOKEN_SECRET`
  (or reuse `SECRET_KEY` with a distinct purpose — decided in Stage 1; do not
  store raw QR tokens, see § 8), `CORS_ORIGINS`, `ENVIRONMENT`.
- No secret is ever logged, returned in an API response, or baked into the
  frontend bundle. `NEXT_PUBLIC_*`-style public values are limited to the API
  base URL and WebSocket URL.

### 3.4 Errors and logging

- One error envelope for all REST errors: `{ "error": { "code": "...",
  "message": "...", "details": {...} } }` with a stable machine-readable
  `code` (`not_found`, `validation_failed`, `forbidden`, `conflict`,
  `idempotency_replayed`, …). See `api-plan.md` § 8.
- Structured JSON logging (request id, restaurant id, user id, route, latency).
  Never log passwords, tokens, or full payment payloads.
- Unhandled exceptions return `500` with a generic message and a logged
  traceback; they never leak SQL or stack traces to the client.

## 4. Frontend — one application, four layouts

### 4.1 Decision: one frontend application (not a monorepo, not four apps)

**Chosen: a single Vite + React + TypeScript application in `frontend/`,
with four feature areas under one router.**

This is the simpler maintainable approach, and it is what the brief asks for
("one maintainable frontend application with shared components, API client,
types, utilities, and distinct role-based layouts").

| Option | Verdict |
|---|---|
| Four separate frontend apps | Rejected. Four builds, four deploys, four copies of the API client, types, design system, and auth — for interfaces that share the same order, bill, and table types. Divergence is guaranteed within weeks. |
| Monorepo with Nx / Turborepo + shared packages | Rejected for v1. The tooling (workspace orchestration, package versioning, build graph) costs more than it saves at this size. Revisit only if a genuinely separate deployable (e.g. a native app) appears. |
| **One app, one repo, feature folders** | **Chosen.** One build, one deploy, one shared layer. Role separation is by route + layout + feature folder, not by repository. The git repo itself is the monorepo in the loose sense (frontend + backend together); no extra monorepo tooling is needed. |

The shared layer is the contract: if a type changes, TypeScript breaks the
build in all four interfaces at once — which is exactly what you want when
they share one backend.

### 4.2 Routing

```
/t/:tableToken            Guest — public (token-scoped), mobile-first
  /t/:tableToken/menu     (menu is the default view; cart / track / bill are
  /t/:tableToken/track     nested routes or panels under the same token scope)
/t/:tableToken/bill
/staff                    Waiter — login required, role waiter|owner
  /staff/tables
  /staff/orders
  /staff/calls
  /staff/bills
/kitchen                  KDS — login required, role kitchen|owner
  /kitchen                (station filter is a query/state, not a route per station)
/admin                   Owner — login required, role owner|manager
  /admin                  (overview) — built in Stage 7, with AdminLayout
  /admin/menu             (categories, items, variants, add-ons)
  /admin/tables           (list + add; no delete in Stage 7)
  /admin/staff            (users: create/roles/flags/deactivate/reset-password)
  /admin/orders           (history + filters)
  /admin/bills            (history + void)
  /admin/cash             (daily cash report + CSV)
  /admin/refunds          (list + create with confirmation)
  /admin/audit-logs       (read-only list + filters)
  /admin/settings         (restaurant/branch/tax config + history)
                          — no /admin/analytics in Stage 7 (deferred: busiest-hours /
                          by-category depth); no ?demo=1 mode on /admin.
/login                    Staff/owner login (one page, role decides landing route)
/                         Redirect: staff → /staff, kitchen → /kitchen,
                          owner → /admin. No public landing page in v1.
```

Route guards are **both** client-side (redirect to `/login`) **and**
server-side (every API call is authorised; a guest token cannot call staff
endpoints even if the UI is bypassed).

### 4.3 State management

| State | Tool | Rule |
|---|---|---|
| Server state (menu, orders, tables, bills, tickets) | **TanStack Query** | Single source of truth per query key (`["orders", restaurantId]`, `["table-session", token]`…). Mutations invalidate the affected keys. Never duplicate server data into Zustand. |
| Client UI state (cart, selected table, station filter, sidebar) | **Zustand** | Small, focused stores per feature (`useCartStore`, `useKitchenFilterStore`). Cart is per table session and cleared on session close. |
| Realtime | WebSocket hook + TanStack Query invalidation | An incoming event invalidates (or surgically updates) the matching query keys, then the query re-fetches. Events do not write business state directly into the cache except for optimistic, clearly-marked UI states that are reconciled on re-fetch. |
| Forms | React Hook Form + Zod (validated against the same rules as the backend schemas where practical) | Frontend validation is UX only; the backend re-validates everything. |
| Design system | shadcn/ui + Tailwind CSS | One theme, four layouts. No per-interface theme forks. |

### 4.4 Layouts — optimised per user, not per template

- **Guest layout:** mobile-first, bottom cart bar, large food cards, sticky
  category nav, one primary action per screen. Works on a phone held in one hand.
- **Staff layout:** tablet/phone, floor-map grid first, lists second, big status
  buttons. Designed for walking, glancing, tapping — not reading.
- **Kitchen layout:** desktop/wall screen, large cards, high contrast, station
  columns, elapsed-time badges. Minimal clicks: the primary action is one large
  status button per item. No small text, no hover-dependent controls.
- **Admin layout:** desktop, sidebar navigation, tables + charts, dense but
  organised. Analytics are read-only aggregates, not live-editable data.

Shared components (`shared/components/ui/` — shadcn/ui primitives,
`shared/components/` — `OrderCard`, `TableCard`, `StatusBadge`, `Money`,
`EmptyState`, …) are used across interfaces where the concept is the same;
per-interface components live in the feature folder. If a component is used
by only one interface, it lives in that interface's folder — do not promote
it to `shared/` "just in case".

### 4.5 API client and types

- One `shared/api/client.ts` (fetch wrapper): base URL from env, attaches auth
  (JWT for staff, QR token header for guest), parses the error envelope,
  attaches `Idempotency-Key` for order placement, retries only safe GETs.
- Types are generated from the backend OpenAPI schema where possible
  (`openapi-typescript` in Stage 1); hand-written types are a stopgap and must
  be marked as such. The backend's Pydantic schemas are the source of truth
  for shapes; the frontend never invents a field the backend does not return.
- Money is displayed from integer paise via a single `formatINR(paise)` helper.
  No component formats money inline.

## 5. Database — summary (full design in `database-design.md`)

PostgreSQL 16, SQLAlchemy 2.0 (typed, `Mapped[]`), Alembic for every schema
change. No schema change without a migration; no migration without a
downgrade path for development (production downgrades are reviewed, not
automatic).

Key decisions, stated here so the rest of the blueprint is readable:

- **UUID primary keys** (`uuid7` / `gen_random_uuid()`) for all business tables
  — safe to generate client-side for idempotency, no sequential-ID leakage.
- **Tenant isolation:** `restaurant_id` on every tenant-scoped table, with a
  composite index `(restaurant_id, …)` and a foreign key to `restaurants`.
  Application code always scopes queries by the caller's restaurant. Row-Level
  Security is a Stage 9 hardening option, not the v1 mechanism — app-level
  scoping is the mechanism, and it is tested (see § 10).
- **Money:** `BIGINT` paise everywhere. No `FLOAT`/`NUMERIC` for money in
  application arithmetic (a `NUMERIC(12,2)` display column is not a substitute).
- **Price snapshots:** `order_items` and `bill_items` store the price and
  names as they were at order/bill time.
- **Status fields are separate per entity:** table status, order status,
  order-item status, ticket status, bill status, payment status, help-call
  status are independent columns with independent state machines. No single
  "status" column is shared across entities.
- **Audit:** `audit_logs` is append-only; sensitive operations (payment
  confirmation, refund, role change, menu price change, QR rotation) write a
  row in the same transaction as the change.

## 6. Realtime — WebSockets, and when Redis is needed

### 6.1 Transport

Native FastAPI WebSockets (no Socket.IO, no separate realtime service).
One endpoint per scope — see `api-plan.md` § 6 — with authentication at
connect time (JWT for staff, QR token for guest). Unauthenticated sockets are
closed immediately.

> **Stage 4 status (23 Nov 2026):** the first WebSocket endpoint is
> live — `GET /ws/kitchen` (dev-key auth, see `api-plan.md` § 9.1).
> It is backed by the in-process `ConnectionManager`
> (`backend/app/realtime/manager.py`): `dict[restaurant_id,
> set[WebSocket]]`, best-effort broadcast per socket (a dead socket
> is removed, never raises to the caller), per-restaurant
> monotonically increasing `version` held in memory. Broadcast
> happens at ROUTER level only AFTER the service transaction has
> committed (`place_order` broadcasts `order.created` only when
> `replayed=False`; the kitchen PATCH broadcasts
> `order.status_changed` only after the transition commits) — the
> services stay broadcast-free so tests can call them without a
> socket and no event can be published from inside a transaction.
> The frontend (`useKitchenSocket`) invalidates the
> `kitchen-orders` TanStack Query key on (re)connect and on every
> event and re-fetches via REST; WS data is never applied as state
> directly. Reconnect is exponential backoff 1s/2s/4s… max 30s.
>
> **Stage 5 status (23 Nov 2026):** a second endpoint reuses the
> SAME manager — `GET /ws/staff` (staff dev-key auth, see
> `api-plan.md` § 9.1). The manager is keyed by channel
> (`kitchen:{restaurant_id}` / `staff:{restaurant_id}`), not by
> role: a kitchen socket and a staff socket for the same
> restaurant are separate channel memberships, and a broadcast to
> one never reaches the other. Staff broadcasts, all at router
> level after commit: `order.status_changed` on serve (the staff
> client ALSO receives kitchen transitions, because the kitchen
> router broadcasts to the staff channel too — both channels get
> `order.created` on guest placement); `help_call.opened` from
> the guest help-call router after creation commits;
> `help_call.acknowledged` / `help_call.resolved` from the staff
> routers after commit. The frontend (`useStaffSocket`) mirrors
> `useKitchenSocket`: it invalidates the `staff-tables`,
> `staff-orders`, `staff-help-calls` and `staff-table-detail`
> TanStack Query keys on (re)connect and on every event.
>
> **Stage 6 status (23 Nov 2026):** bill/payment events ride the
> SAME staff channel — `bill.created` (guest router, only when a NEW
> bill is generated), `bill.requested` (guest router, only on the
> actual `issued → payment_requested` transition), `payment.recorded`
> and `bill.paid` (staff payment router, after the payment commits;
> nothing is broadcast on an idempotency replay — a replay is not a
> new payment). No event is broadcast on a failed/rolled-back
> operation. `useStaffSocket` now also invalidates the `staff-bills`
> and `staff-bill` query keys. The guest bill page does NOT use a
> socket: it refetches via REST (on focus, after its own actions, and
> on a 10s interval while unpaid) — the guest WS channel is Stage 8.
> The single-process limitation is unchanged (see § 6.3): events
> published in one process reach only sockets connected to that
> process.
>
> **Stage 7 status (24 Nov 2026):** both live channels now
> authenticate by **WS ticket** in production. A client first calls
> `POST /api/v1/ws/tickets` (JWT) for its channel, then connects with
> `?ticket=…`. Tickets are stored server-side in `ws_tickets`
> (SHA-256 hash, single-use, 60 s expiry) — NOT signed JWTs in the
> URL, because single-use requires server state. Validation: hash
> lookup, not expired, not used, user still active and still holding
> the channel permission, restaurant matches; then `used_at` is
> marked. In dev, `?dev_key=` still works and a JWT is accepted on
> the REST endpoints too. Close codes: bad/expired/used ticket →
> 4401; wrong-role ticket or dev key in production → 4403;
> restaurant mismatch → 4404. The socket is **accepted then
> closed** with the code (uvicorn turns close-before-accept into a
> bare HTTP 403). Production REST dev-key behaviour also changed:
> dev keys are never accepted and return 401 (was 503
> `*_auth_not_configured` in Stages 4/5). The frontend fetches a
> fresh ticket on every (re)connect (tickets are single-use) and
> sends dev keys only when `import.meta.env.DEV` is true, so a
> production build never includes them.
>
> **Stage 8 status (25 Nov 2026):** the third endpoint is live —
> `GET /ws/guest` (guest ticket auth, see `api-plan.md` § 9.1). The
> SAME `ConnectionManager` was extended, not replaced:
> `connect_guest` / `disconnect_guest` / `broadcast_guest` keyed by
> `table_session_id`, alongside the restaurant-keyed staff/kitchen
> sets. A guest socket receives ONLY its own session's events;
> staff/kitchen sockets never receive guest-scoped events and vice
> versa — isolation is structural, not a filter. The guest ticket is
> issued by `POST /api/v1/guest/ws/tickets` (X-Table-Token): same
> opaque / SHA-256 / single-use / 60 s shape as staff/kitchen, but
> `user_id` is NULL and the ticket is bound to the exact restaurant,
> branch, table and active session (`ws_tickets.table_session_id`,
> migration `c8d3e5f6a7b8` — `user_id` nullable, channel CHECK
> widened to `kitchen|staff|guest`). Guest events (10 types):
> `order.created`, `order.status_changed`, `order.cancelled` (NEW —
> guest cancel did not broadcast before Stage 8),
> `help_call.opened` / `.acknowledged` / `.resolved`,
> `bill.created`, `bill.updated` (NEW — splits changed or status
> changed; NOT on every bill-regeneration fetch),
> `payment.recorded`, `bill.paid`. All are broadcast at router
> level AFTER commit, never on rollback, never on idempotent replay.
> The frontend (`useGuestSocket`, owned once by `GuestLayout` via
> `GuestSocketProvider`) fetches a fresh ticket per (re)connect,
> backs off exponentially WITH jitter (1 s → 30 s), invalidates the
> guest TanStack Query keys on (re)connect and on every event, and
> refetches via REST; while the socket is down it falls back to a
> slow 30 s background refetch + refetch on window focus, and after
> 3 consecutive session failures (401/410 ticket fetch, or WS close
> 4401/4404) it stops and shows "Session expired — reopen via QR".
> The old 20 s / 10 s REST polling intervals on the guest order and
> bill pages were removed — push is primary, polling is fallback
> only (the roadmap's "replace polling with push, polling remains
> as fallback" choice, recorded). `version` for the guest channel
> is a per-SESSION in-memory counter; the hook ignores events with
> `version` ≤ the highest seen. The single-process limitation is
> unchanged (see § 6.3).

**Event envelope (all events):**

```json
{
  "type": "order.created",
  "restaurant_id": "<uuid>",
  "table_id": "<uuid>",
  "data": { "order_id": "<uuid>", "order_number": "A-104", "...": "summary only" },
  "version": 42,
  "occurred_at": "2026-11-23T15:00:00Z"
}
```

Events carry identifiers and a summary, not the full entity. Receivers
re-fetch the entity via REST if they need the full detail. This keeps events
small, avoids leaking fields a channel should not see, and makes the DB the
authority.

### 6.2 Channels

| Channel | Who subscribes | Events |
|---|---|---|
| `restaurant:{id}` | Staff, kitchen, owner of that restaurant | `order.*`, `ticket.*`, `table.*`, `help_call.*`, `bill.*`, `payment.*` |
| `table-session:{session_id}` | The guest session (+ staff viewing that table) | `order.status_changed`, `order.item_ready`, `bill.*`, `help_call.*` for that session |
| `kitchen:{restaurant_id}:{station}` | KDS filtered view (subscription filter, not a separate truth) | `ticket.*` for that station |
| `kitchen:{restaurant_id}` | KDS | **Stage 4, LIVE:** `order.created`, `order.status_changed` |
| `staff:{restaurant_id}` | Staff board | **Stage 5, LIVE:** `order.created`, `order.status_changed` (incl. kitchen transitions), `help_call.opened`, `help_call.acknowledged`, `help_call.resolved`. **Stage 6, LIVE:** `bill.created`, `bill.requested`, `payment.recorded`, `bill.paid`. |
| `guest:{table_session_id}` | The guest of that session ONLY | **Stage 8, LIVE:** `order.created`, `order.status_changed`, `order.cancelled`, `help_call.opened`, `help_call.acknowledged`, `help_call.resolved`, `bill.created`, `bill.updated`, `payment.recorded`, `bill.paid`. Session-scoped; no other session ever receives these events. |

Authorisation is checked at subscription time, per channel, against the
caller's restaurant and (for guests) table session. A guest socket cannot
subscribe to `restaurant:{id}`.

### 6.3 Redis — deferred by design

**v1 (single `api` replica): in-process broadcast.** The `ConnectionManager`
holds connections in memory and broadcasts within the process. This is
sufficient, simpler, and has no extra infrastructure to operate.

**When Redis becomes needed — any one of these triggers:**

1. A second `api` replica is deployed (events published in one process must
   reach sockets connected to another), or
2. A background worker is introduced that must publish events, or
3. Caching of a hot read is measured to be necessary (not assumed).

At that point, add Redis as a **pub/sub broker behind the same
`ConnectionManager` API** (`broadcast` / `broadcast_guest` /
`connect` / `disconnect`), with the in-process manager as the local
fan-out. The service code does not change; only the broker
implementation does. Docker Compose gains a `redis` service then,
not before. Do not add Redis in Stage 1 "because realtime usually needs
it" — it does not, at one replica.

> **Stage 8 decision (25 Nov 2026): no `EventBus` interface.** The
> roadmap's Stage 8 work list called for an `EventBus` interface with
> Redis behind it. The brief overrules that: the existing
> `ConnectionManager` already IS the bus — adding an abstraction
> over one implementation buys nothing at one replica. If a second
> replica ever forces Redis (§ 6.3 above), the broker is added
> behind the manager's existing API, not behind a new interface.

### 6.4 Reconnection and ordering

- Clients reconnect with exponential backoff and, on reconnect, re-fetch their
  current state via REST before rendering live updates again.
- Events carry a per-restaurant monotonically increasing `version` (in-memory
  counter in the `ConnectionManager`; the guest channel uses a per-SESSION
  counter instead — each session's events are ordered within that session).
  A client that sees a gap re-fetches. Events are not guaranteed ordered
  across channels; they are ordered within a channel by `version`. The
  guest hook additionally ignores events with `version` ≤ the highest seen
  (stale/out-of-order protection, not an event-replay system).
- No client action (e.g. "mark paid") is ever performed from a WebSocket
  message alone. Actions are REST calls; WebSocket only announces that an
  action happened.

## 7. Background work

There is no background worker in v1. The only time-based behaviours are:

- Table-session expiry (a periodic check, implemented as a lightweight
  FastAPI startup task / `asyncio` loop in Stage 2, or a DB-driven check on
  read — decided in Stage 2; not a Celery/Redis queue).
- Daily cash-report aggregation (computed on read from `payments`/`bills`,
  not pre-computed — until measurement shows otherwise).

If a worker is ever needed (e.g. nightly reports, token cleanup at scale),
it is a separate process in the same monolith codebase sharing the same
services — not a new service with its own API. Do not introduce Celery/RQ in
v1.

## 8. Authentication and authorisation

### 8.1 Staff / owner (password users) — as built in Stage 7 (24 Nov 2026)

- Passwords hashed with **Argon2id** via `core/security.py`
  (`hash_password` / `verify_password`) — the Stage 1 choice, confirmed
  in Stage 7. Never MD5/SHA-1/plaintext, never logged, never returned.
- Login: `POST /api/v1/auth/login` → short-lived **access token (JWT,
  15 min, HS256)** + **refresh token (rotating, opaque, HttpOnly
  cookie, 7 days)**. Access tokens are sent as `Authorization:
  Bearer …`; the frontend keeps the access token in memory (Zustand
  store, not `localStorage` — a full page reload logs the user out,
  by design). Refresh tokens are stored as SHA-256 hashes only.
- JWT claims: `sub` (user id), `restaurant_id`, `branch_id`, `role`,
  `exp`, `jti`. **Every request re-validates the user from the
  database** (`get_current_user` does a DB lookup + `is_active`
  check), so deactivation is effective immediately on all endpoints,
  not only at token expiry.
- Refresh-token rotation: each refresh issues a new refresh token and
  revokes the old one; reuse of a revoked token revokes the whole
  token family (theft detection). Logout revokes the presented token;
  change-password revokes ALL of the user's refresh sessions.
- Login is rate-limited (10/min/IP, slowapi) and returns a generic 401
  for wrong credentials and for deactivated accounts alike. The first
  owner is created only via the bootstrap CLI (`python -m
  app.auth.bootstrap`, password via getpass twice); there is no public
  owner-registration endpoint and no hardcoded/default credentials.
- RBAC is enforced by dependencies (`require_permission(capability)`,
  `require_role(...)`) on every route — the capability map lives in
  `backend/app/auth/permissions.py` — plus a restaurant scope check
  and a branch pin for branch-scoped users. The frontend hiding a
  button is never the security control.
- Manager "Configurable" permissions are boolean flags on the user
  record (`can_manage_staff`, `can_manage_settings`, `can_refund`,
  `can_void`), default False, toggleable by the owner.

### 8.2 Guest (QR token — no password)

- Each table has a **QR token**: a cryptographically random 32-byte value
  (URL-safe base64), stored **hashed** (SHA-256) in `table_tokens`, never in
  plaintext after creation. The QR code encodes
  `https://<host>/t/<raw-token>`.
- A token maps to exactly one `(restaurant_id, table_id)`. It is **revocable**
  (owner rotates it; the old token stops working immediately) and can carry an
  expiry. Rotation is an admin action, audited.
- Scanning creates/resumes a `table_sessions` row. The guest's API calls carry
  the raw token (header `X-Table-Token` or the path token, per `api-plan.md`);
  the backend hashes it, looks up the token, and scopes every query to that
  session's restaurant + table. A guest token for Table 5 cannot read Table 6,
  cannot call staff endpoints, and cannot see other restaurants.
- Tokens are **not** JWTs and are not signed with the JWT secret — they are
  opaque, random, revocable database tokens. If a QR code is photographed and
  shared, rotation is the remedy; the session-expiry decision (D5 in
  `project-overview.md`) bounds the exposure window.

### 8.3 Authorisation matrix (as built in Stage 7 — enforced per endpoint in `api-plan.md` § 2/§ 8)

Capabilities are the keys of `PERMISSIONS` in
`backend/app/auth/permissions.py`. Owner has all of them. Manager has
the base set plus whichever of the four flags the owner granted.
Waiter `view_kitchen` is No in v1 (the brief marked it "Optional").

| Capability | Owner | Manager | Waiter | Kitchen |
|---|---|---|---|---|
| `view_dashboard` | Yes | Yes | No | No |
| `manage_settings` | Yes | Flag `can_manage_settings` | No | No |
| `manage_menu` | Yes | Yes | No | No |
| `manage_staff` | Yes | Flag `can_manage_staff` | No | No |
| `view_financials` | Yes | Yes | No | No |
| `confirm_cash` | Yes | Yes | Yes | No |
| `serve_orders` | Yes | Yes | Yes | No |
| `update_kitchen` | Yes | Yes | No | Yes |
| `view_kitchen` | Yes | Yes | No | Yes |
| `configure_tax` | Yes | **No** | No | No |
| `issue_refunds` | Yes | Flag `can_refund` | No | No |
| `void_bills` | Yes | Flag `can_void` | No | No |

Guest (own session) is unchanged from the blueprint: menu read, place
order, own bill read/create/request, help-call create — via the QR
table token, never a password. Waiters/owners placing an order on a
guest's behalf is a possible future addition, not a v1 capability — do
not build it speculatively. Reviews are not built (moderation D6
deferred).

## 9. Security — checklist (verified in Stage 9, 25 Nov 2026)

Every item below was re-run in Stage 9 with evidence — a test file, a
grep, or a cited check. "Verified" means the evidence exists in the
repo; see the Stage 9 report in `development-roadmap.md` for the full
per-item list.

- [x] Password hashing (Argon2id, no bcrypt fallback — `core/security.py`;
  `tests/test_security.py`). No plaintext secrets anywhere in code.
- [x] JWT: 15-min access, rotating refresh, revocation on logout /
  change-password / deactivation, `jti` tracking; reuse of a revoked
  refresh token revokes the whole family (`tests/test_jwt_security.py`).
  Pinned limit: an already-issued access token from a revoked family
  stays valid until its 15-min expiry (no server revocation list — by
  design, recorded in the test).
- [x] QR tokens: random, SHA-256 hashed at rest, revocable, scoped,
  expirable (`tests/test_qr_tokens.py`; rotation/revoke/expired paths
  tested). QR rotation itself is still a deferred owner action (R10).
- [x] RBAC on every route; tenant scoping on every query; negative tests
  proving cross-tenant and cross-table reads fail
  (`tests/test_rbac_matrix.py` — role × endpoint matrix, 401/403/404 as
  designed per `api-plan.md` § 2).
- [x] Server-side validation of all input (Pydantic), including:
  item/variant/add-on existence, availability, quantities (≥ 1, ≤ a
  sane max), price recomputation, bill-split sums, payment amount ≤
  amount due (`tests/test_input_validation.py`; rejected requests leave
  the DB unchanged). No endpoint accepts a client-supplied price
  (`tests/test_no_client_price.py`; admin-set menu prices are the only
  legitimate exception).
- [x] SQL injection: prevented by SQLAlchemy parameterisation; no raw SQL
  with string interpolation in `backend/app` (grep, Stage 9).
- [x] CORS: explicit allowlist from env (`CORS_ORIGINS`), credentials
  only for listed origins, no `*` with credentials
  (`tests/test_security_headers_cors.py`). Stage 9 fix: Starlette's
  `CORSMiddleware` sent `Access-Control-Allow-Credentials: true` even
  for unlisted origins — the headers middleware in `app/main.py` now
  strips it unless an ACAO header was actually set.
- [x] Rate limiting: login, order placement, session-open, kitchen
  PATCH, staff serve/payment/help (SlowAPI, in-process; limits verified
  with the limiter enabled — `tests/test_rate_limits.py`,
  `tests/test_rate_limits_security.py`). Admin endpoints have NO
  limiter (recorded, not changed — admin actions require JWT + role;
  adding limits is a Stage 10 decision if abuse is observed).
- [x] Security headers: `X-Content-Type-Options: nosniff`,
  `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer` on all
  responses; `Cache-Control: no-store` on `/api/*` only (middleware in
  `app/main.py`, `tests/test_security_headers_cors.py`). No CSP — the
  Vite frontend serves inline assets a CSP would break, and a CSP for
  the JSON-only API buys nothing (recorded in code + test). No HSTS —
  no TLS termination in dev/sandbox; a Stage 10 deployment decision
  (reverse proxy).
- [x] Audit logging for: login success/failure, payment confirmation,
  refunds, bill voids, menu price changes, staff role changes, QR
  rotation, session closure (`tests/test_audit_coverage.py`). Failed
  operations write no success row. Pinned: unknown-email login writes
  no audit row (no tenant to scope it to).
- [x] No secrets in git, logs, or the frontend bundle. `.env.example`
  contains placeholders only. Stage 9 secret scan (gitleaks 8.28.0 +
  trufflehog + `git log -p` greps over all 13 commits): no private keys,
  no real SECRET_KEY values, no Render/LiveKit tokens, `.env` never
  tracked. **Open item for Shivam:** 4 demo passwords
  (`OwnerPass123`/`WaiterPass123`/`KitchenPass123`) in the Stage 7/8
  verification scripts (`backend/scripts/verify_stage7_live.py`,
  `screenshots_stage7.py`, `verify_stage8_e2e.py`) — throwaway local
  demo accounts against ephemeral localhost databases; the seed creates
  no users with them and no deployment uses them. Left in place
  deliberately (no history rewrite on a public repo without his call);
  see the Stage 9 report for the decision options.
- [x] Dependencies pinned and scanned (`pip-audit` / `npm audit`) in CI
  from Stage 9 (`.github/workflows/ci.yml`). See "Dependency scan"
  below.
- [ ] Uploaded food images: validated type/size, stored outside the web
  root with a served URL — **not applicable in v1**: menu image upload
  is deferred (no upload endpoint exists, so there is nothing to
  validate; recorded, not claimed).

### Dependency scan (Stage 9, 25 Nov 2026)

- `pip-audit -r backend/requirements.txt`: **3 findings in 2 packages,
  both accepted with reasons, both with no fix released**:
  - `python-jose 3.5.0`, PYSEC-2026-4298 (incomplete fix for
    CVE-2024-33663 — HMAC init accepts unarmoured DER public keys):
    not exploitable here — `tokens.py` decodes with
    `algorithms=[settings.jwt_algorithm]` (HS256 only) and the key is
    the server-side `SECRET_KEY`, never attacker-controlled.
  - `ecdsa 0.19.2`, PYSEC-2026-1325 (Minerva timing attack on P-256
    signing): the app never signs with ECDSA (HS256 only; `ecdsa` is
    only a transitive dependency of `python-jose`); upstream has no
    planned fix.
  - CI runs `pip-audit` with `--ignore` for exactly these two IDs and
    fails on any new finding.
- `npm audit --audit-level=high` (frontend): **0 vulnerabilities**.

### RLS decision (Stage 9, 25 Nov 2026) — NO for v1

PostgreSQL Row-Level Security as defence-in-depth for tenant isolation
was evaluated and **declined for v1**. Evidence: app-level scoping is
the mechanism (§ 5) and is now covered by negative tests on every
surface (RBAC matrix, cross-restaurant, cross-table); the app uses one
shared engine/pool user from a single `DATABASE_URL` with no
per-request DB role and no `SET ROLE`/`SET LOCAL app.*` anywhere
(`app/db/session.py`); an RLS migration would have to touch 17
`restaurant_id` columns across 14 tenant tables plus 6 child tables
that carry no `restaurant_id` at all (`BillItem`, `BillSplit`,
`OrderItem`, `OrderItemAddon`, `ItemVariant`, `ItemAddon` — scoped via
parents, so policies would need joins or new columns), plus the seed,
the bootstrap CLI, the 5 Alembic migrations, and the pgserver test
fixtures. RLS would add a second enforcement layer that must be kept
in sync with the app layer, for no additional protection while there
is exactly one DB user and one request path. **Revisit if:** a second
DB user/role is introduced, multi-tenancy moves beyond
one-restaurant-per-user, or an app-level scoping escape is ever found
in production. If adopted later: one Alembic migration enabling RLS +
`FORCE RLS` per tenant table, `current_setting('app.restaurant_id')`
set per session in `get_db` from the authenticated user/guest context,
`restaurant_id` added to the 6 child tables, and a superuser/bypass
role for seed, migrations, and fixtures.

## 10. Testing strategy

> **Stage 9 status (25 Nov 2026):** the pyramid below is built and
> green — backend pytest 377 passed / 1 skipped, frontend Vitest 128
> passed, E2E `verify_stage9_e2e.py` 81/81 checks, load sanity 50/50
> placements with 0 errors. CI (`.github/workflows/ci.yml`) runs all
> three jobs on every push/PR: backend (pytest + `pip-audit`), frontend
> (Vitest + `npm audit` + build), e2e (Playwright script against the
> local stack). GitHub Actions runs are NOT executed from the sandbox —
> they run on push after Shivam pushes; the YAML is validated by parse
> and the jobs mirror the locally-verified commands.

| Level | Tool | What it proves |
|---|---|---|
| Unit | Pytest | Pricing (incl. splits/remainder), status-machine transitions, idempotency, token hashing, permission helpers. Money tests use integer paise and boundary cases (₹0, ₹1, odd-paise splits). Stage 9: `tests/test_pricing_tax.py`, `test_splits_property.py` (200 seeded cases), `test_state_machines.py` (every legal + illegal transition). |
| API / integration | Pytest + FastAPI `TestClient` + test Postgres | Full golden path (§ 5 of `project-overview.md`) against a real DB: order → kitchen → ready → bill → cash → receipt. Cross-tenant and cross-table access denied. Duplicate order with same idempotency key returns one order. Stage 9: `tests/test_golden_path_pg.py`, `test_contracts.py` (wire shapes field-for-field), `test_rbac_matrix.py`, `test_jwt_security.py`, `test_qr_tokens.py`, `test_input_validation.py`, `test_audit_coverage.py`, `test_no_client_price.py`, `test_security_headers_cors.py`, `test_rate_limits_security.py`. Postgres tests run via pgserver (bundled binaries, `tests/pg_support.py`, data under `~/.pg-tmp`). |
| Frontend unit | Vitest + Testing Library | Cart maths (display only), status badges, split calculator, guards. Stage 9: `src/shared/utils/money.test.ts`, `features/guest/hooks/useGuestSocket.test.tsx` (reconnect/backoff/stop-after-3/no-false-paid), `features/staff/components/TableDetail.test.tsx`. |
| E2E | Playwright | Guest scan → order → track; waiter floor → help call → cash confirm; KDS ticket flow; owner dashboard loads with seeded data. Run against Docker Compose. Stage 9: `backend/scripts/verify_stage9_e2e.py` (81 checks) runs the same flows against the LOCAL stack (pgserver + uvicorn + Vite) — Compose was not available in the sandbox, so the Compose entrypoint (`backend/scripts/run_e2e_compose.sh`, `--external` mode) is written but UNPROVEN; it is a Stage 10 run, not a Stage 9 claim. |
| Manual / demo | Zaika seed data | Two-device check: guest phone + KDS screen in the same room, one order, watch it appear without refresh. |

**Definition of done for a stage** (applies to every stage in the roadmap):
tests for that stage pass, the stage's acceptance criteria are verified live
(not just "endpoint exists"), no new `TODO` claims a feature complete that is
not, and the docs are updated if a decision changed.

## 11. Project folder structure (final — Stage 1 must follow this)

```
tavessa/
├── README.md
├── docs/
│   ├── project-overview.md
│   ├── architecture.md          # this file
│   ├── database-design.md
│   ├── api-plan.md
│   └── development-roadmap.md
├── .env.example
├── .gitignore
├── docker-compose.yml
├── Dockerfile.backend
├── Dockerfile.frontend
├── backend/
│   ├── pyproject.toml           # or requirements.txt — decided in Stage 1, one only
│   ├── alembic.ini
│   ├── alembic/
│   │   ├── env.py
│   │   └── versions/
│   ├── app/
│   │   ├── main.py              # FastAPI app factory, middleware, routers, WS
│   │   ├── core/
│   │   │   ├── config.py
│   │   │   ├── security.py      # hashing, JWT, QR token hashing
│   │   │   ├── deps.py          # get_db, current_user, require_role, guest session
│   │   │   ├── errors.py
│   │   │   └── logging.py
│   │   ├── db/
│   │   │   ├── base.py          # Base, naming conventions
│   │   │   └── session.py       # engine, SessionLocal, get_db
│   │   ├── models/
│   │   │   ├── __init__.py
│   │   │   ├── restaurant.py    # Restaurant, Branch
│   │   │   ├── user.py          # User, Role (enum), StaffAssignment
│   │   │   ├── table.py         # DiningTable, TableToken, TableSession
│   │   │   ├── menu.py          # Category, MenuItem, Variant, AddOn, ItemAddOn
│   │   │   ├── order.py         # Order, OrderItem
│   │   │   ├── kitchen.py       # KitchenStation, KitchenTicket
│   │   │   ├── billing.py       # Bill, BillItem, BillSplit, Payment, Refund
│   │   │   ├── support.py       # HelpCall, Review, Notification
│   │   │   └── audit.py         # AuditLog, IdempotencyKey
│   │   ├── schemas/
│   │   │   ├── auth.py
│   │   │   ├── menu.py
│   │   │   ├── table.py
│   │   │   ├── order.py
│   │   │   ├── kitchen.py
│   │   │   ├── billing.py
│   │   │   ├── staff.py
│   │   │   ├── admin.py
│   │   │   └── common.py        # pagination, error envelope, money
│   │   ├── repositories/
│   │   │   ├── menu_repo.py
│   │   │   ├── order_repo.py
│   │   │   ├── billing_repo.py
│   │   │   └── table_repo.py    # only where a query is non-trivial; not one file per model
│   │   ├── services/
│   │   │   ├── auth_service.py
│   │   │   ├── menu_service.py
│   │   │   ├── table_service.py
│   │   │   ├── order_service.py
│   │   │   ├── kitchen_service.py
│   │   │   ├── billing_service.py
│   │   │   ├── help_service.py
│   │   │   ├── staff_service.py
│   │   │   ├── review_service.py
│   │   │   ├── analytics_service.py
│   │   │   └── audit_service.py
│   │   ├── api/
│   │   │   ├── deps.py          # (re-export from core/deps if preferred — one place only)
│   │   │   └── v1/
│   │   │       ├── __init__.py  # router aggregation
│   │   │       ├── auth.py
│   │   │       ├── restaurants.py
│   │   │       ├── menu.py
│   │   │       ├── tables.py
│   │   │       ├── orders.py
│   │   │       ├── kitchen.py
│   │   │       ├── bills.py
│   │   │       ├── help_calls.py
│   │   │       ├── staff.py
│   │   │       ├── reviews.py
│   │   │       ├── admin.py
│   │   │       └── websocket.py
│   │   ├── realtime/
│   │   │   ├── manager.py       # ConnectionManager (in-process v1) — the
│   │   │   │                    # bus. No EventBus interface (Stage 8
│   │   │   │                    # decision); a Redis broker, if ever needed,
│   │   │   │                    # goes behind this manager's API (§ 6.3).
│   │   └── seed/
│   │       └── zaika.py         # demo seed: Zaika, tables, stations, menu, users
│   └── tests/
│       ├── conftest.py
│       ├── test_pricing.py
│       ├── test_orders.py
│       ├── test_billing.py
│       ├── test_auth.py
│       └── test_realtime.py
├── frontend/
│   ├── package.json
│   ├── vite.config.ts
│   ├── tailwind.config.js
│   ├── tsconfig.json
│   ├── index.html
│   └── src/
│       ├── main.tsx
│       ├── app/
│       │   ├── router.tsx
│       │   ├── providers.tsx    # QueryClient, Zustand setup, WS provider
│       │   └── layouts/
│       │       ├── GuestLayout.tsx
│       │       ├── StaffLayout.tsx
│       │       ├── KitchenLayout.tsx
│       │       └── AdminLayout.tsx
│       ├── features/
│       │   ├── guest/           # menu, cart, track, bill, review
│       │   ├── staff/           # floor map, orders, calls, bills
│       │   ├── kitchen/         # ticket board, station filter
│       │   └── admin/           # overview, menu, staff, orders, bills, analytics, settings
│       ├── shared/
│       │   ├── api/
│       │   │   ├── client.ts
│       │   │   ├── types.ts     # generated from OpenAPI where possible
│       │   │   └── websocket.ts
│       │   ├── components/
│       │   │   ├── ui/          # shadcn/ui primitives
│       │   │   ├── OrderCard.tsx
│       │   │   ├── TableCard.tsx
│       │   │   ├── StatusBadge.tsx
│       │   │   └── Money.tsx
│       │   ├── hooks/
│       │   ├── stores/          # Zustand stores (cart, UI)
│       │   └── utils/
│       │       ├── money.ts     # formatINR(paise)
│       │       └── time.ts
│       └── tests/               # Vitest + Playwright (e2e/ in tests/e2e/)
└── .github/
    └── workflows/
        ├── ci.yml               # backend pytest + frontend build/test
        └── e2e.yml              # Playwright against docker-compose
```

**Rules for this layout:**

- Do not create a folder before its stage needs it. Stage 1 creates the
  skeleton (configs, `main.py`, `core/`, `db/`, one health endpoint, the Vite
  app shell, router, layouts). Feature folders gain files stage by stage.
- Do not add a `repositories/` file for a model whose queries are trivial —
  the service may use the session directly. Repositories exist where they
  earn their place (complex order/billing queries), not as ceremony.
- `seed/zaika.py` is demo data only, clearly labelled, and never runs in
  production by default.

## 12. Technical risks and mitigations

| # | Risk | Impact if unmitigated | Mitigation (designed now) |
|---|---|---|---|
| R1 | Duplicate orders from double-tap / retry / flaky network | Kitchen makes the same dish twice; guest is billed twice | `Idempotency-Key` + stored key record + unique constraint; retry returns the original order. Tested in Stage 3. |
| R2 | Frontend prices trusted by the backend | Guest edits a request and pays ₹1 for a ₹500 dish | Backend recomputes every price from menu + snapshots; frontend amounts are ignored. Tested with tampered payloads. |
| R3 | Bill marked paid without cash collected | Cash loss, unreconcilable till | Only staff `POST /payments` creates a paid state; guest has no endpoint that can do it; audit log + row-lock guard against double confirmation. |
| R4 | WebSocket treated as source of truth | Divergent screens after a dropped message; "my screen says ready, kitchen says preparing" | DB-first, broadcast-after-commit; reconnect re-fetch; event `version` gap → re-fetch. |
| R5 | Cross-table / cross-restaurant data leak via QR token or API | Guest sees another table's bill; staff sees another restaurant | Token scoping + restaurant scoping on every query; negative tests for both. |
| R6 | KDS screen offline / browser crash mid-service | Kitchen misses orders | KDS re-fetches the active queue on load/reconnect; orders are never "only in the socket". A printed-ticket fallback is a Stage 4 decision, not a v1 assumption. |
| R7 | Concurrent status updates (two cooks, same item) | Lost update, wrong status | Status transitions validated against the current DB state in a transaction; illegal transitions return `409`. Optimistic locking (`version` column) on orders/tickets where contention is expected. |
| R8 | Money rounding (splits, tax, change) | Paise lost/gained; bills that do not sum | Integer paise everywhere; split remainder rule documented; rounding rule decided in D1 before Stage 6; property-style tests for splits. |
| R9 | Single-instance realtime breaks when scaled | Events lost across replicas | **Corrected Stage 9:** the earlier text claimed an "`EventBus` interface from Stage 8" — that is FALSE. Stage 8 deliberately did NOT add an EventBus interface; the `ConnectionManager` already IS the bus, and the Redis trigger is a second `api` replica (§ 6.3). When Redis becomes needed, the broker is added behind the manager's existing `broadcast` / `broadcast_guest` / `connect` / `disconnect` API, not behind a new interface. Until then the single-process limitation is documented and no cross-process delivery is claimed. |
| R10 | QR code photographed / shared | Strangers order on Table 5's tab | Revocable tokens, session expiry (D5), rotation by owner; no payment can be completed by a guest anyway, so the blast radius is a bogus order — which staff can cancel and which is auditable. |
| R11 | Scope creep — online payments, delivery, reservations | v1 never ships | Cash-only rule + non-goals list in `project-overview.md`; any addition needs a recorded decision and a roadmap change. |
| R12 | Menu price change rewrites history | Old bills change value | Price snapshots in `order_items`/`bill_items`; price changes only affect new orders. |

### Stage 9 evidence per risk (25 Nov 2026)

Every row above now names its evidence — a test, a manual check, or a
recorded decision. No row is "mitigation designed, never verified".

- **R1 — duplicate orders:** test. Same-key replay, different-body 422,
  and 8-way concurrent same-key → exactly 1 order
  (`test_orders.py`, `test_orders_concurrency.py`); same for payments
  and refunds (`test_billing_concurrency.py`,
  `test_admin_concurrency.py`).
- **R2 — frontend prices trusted:** test. Guest order schemas carry no
  price/total/amount field (`extra='forbid'`); tampered payloads → 422
  and server totals recomputed from menu prices
  (`test_no_client_price.py`, `test_input_validation.py`).
- **R3 — bill paid without cash:** test. Only staff `POST /payments`
  creates a paid state; 8-way concurrent same-key → exactly one
  payment, different-key → never overpaid
  (`test_billing_concurrency.py`); double-confirmation race covered in
  `test_reliability_concurrency_pg.py`; audit row per payment
  (`test_audit_coverage.py`).
- **R4 — WebSocket as source of truth:** test. DB-first,
  broadcast-after-commit; reconnect re-fetches via REST; `version` gap
  → re-fetch; no event on rollback or idempotent replay
  (`test_guest_realtime.py`, `test_reliability_stage9.py` KDS/guest
  reconnect tests, E2E §6b).
- **R5 — cross-table / cross-restaurant leak:** test. Role × endpoint
  matrix, cross-restaurant 403/404 as designed, cross-table 404,
  guest token on staff/kitchen/admin → 401
  (`test_rbac_matrix.py`, `test_qr_tokens.py`).
- **R6 — KDS offline mid-service:** test. Socket killed mid-service,
  reconnect → queue re-fetched from REST, no order lost, none shown
  twice (`test_reliability_stage9.py`, E2E §6b).
- **R7 — concurrent status updates:** test. Two cooks / two staff
  serving / concurrent help-call acknowledge / concurrent void — exactly
  one wins, the rest 409, final state legal
  (`test_kitchen_concurrency.py`, `test_staff_concurrency.py`,
  `test_reliability_concurrency_pg.py`). Note: the "optimistic locking
  (`version` column)" in the original mitigation was never built —
  contention is serialised by row locks + status checks in a
  transaction instead; the `version` column is a bill/order counter,
  not an optimistic-lock token. Recorded here, not claimed.
- **R8 — money rounding:** test. Integer paise everywhere; property
  test over 200 seeded totals × parts (sums exact, remainder rule);
  tax inclusive/exclusive + freeze snapshot tests
  (`test_splits_property.py`, `test_pricing_tax.py`,
  `test_split_reconciliation.py`).
- **R9 — single-instance realtime:** recorded decision (see the R9 row
  above). No cross-process delivery is claimed; the Redis trigger
  (second `api` replica) is unchanged.
- **R10 — QR photographed/shared:** recorded decision + partial test.
  Revocable tokens, session expiry (D5, 30-day demo expiry), rotation
  by owner; token revoke/expire paths tested (`test_qr_tokens.py`).
  QR rotation itself is still deferred (not built in Stages 7/8/9) —
  the blast radius is a bogus order (no guest payment path exists),
  which staff can cancel and which is auditable.
- **R11 — scope creep:** recorded decision. Cash-only rule +
  non-goals in `project-overview.md`; no online payment, delivery, or
  reservation code exists in the repo (grep, Stage 9).
- **R12 — price change rewrites history:** test. Menu price change
  after ordering cannot change a bill; old bills keep snapshots
  (`test_pricing_tax.py`, `test_billing.py`).

### E2E against Docker Compose (unproven)

`backend/scripts/run_e2e_compose.sh` boots the Compose stack (services
`api`, `web`, `db`), waits for the API health endpoint, bootstraps the
owner account and seeds Zaika inside the `api` container, then runs
`backend/scripts/verify_stage9_e2e.py --external` with `E2E_API_BASE` /
`E2E_WEB_BASE` / `E2E_SEED_JSON` pointed at the Compose services. It
leaves the stack running for inspection; stop it with
`docker compose down`. This path was **not executed in the Stage 9
sandbox (no Docker there) and is unproven** — it assumes a Stage 10
`docker-compose.yml` (api on 8000, web as Vite dev on 5173 with a
proxy, db Postgres) and Playwright/Chromium on the host. The same
flows are verified against the local stack by `verify_stage9_e2e.py`
(81 checks, all passing).

## 13. Decisions recorded in this document

- **D-ARCH-1:** Modular monolith, not microservices. (Rationale § 1.)
- **D-ARCH-2:** One frontend app with four feature areas, not four apps and
  not an Nx/Turborepo monorepo. (Rationale § 4.1.)
- **D-ARCH-3:** Native FastAPI WebSockets, DB-first broadcast, event envelope
  with summary + `version`. (Rationale § 6.)
- **D-ARCH-4:** Redis deferred; in-process broadcast in v1 behind the
  `ConnectionManager`'s existing API. **Corrected Stage 9:** the
  original text said "behind an `EventBus` interface" — no such
  interface exists and Stage 8 deliberately did not add one. Triggers
  for adding Redis are listed in § 6.3.
- **D-ARCH-5:** No background worker in v1; no Celery/RQ. (Rationale § 7.)
- **D-ARCH-6:** Password hashing Argon2id preferred, bcrypt-12 fallback —
  final choice recorded in Stage 1.
- **D-ARCH-7:** QR tokens are opaque random database tokens, hashed at rest,
  revocable — not JWTs.
- **D-ARCH-8:** Money as integer paise (`BIGINT`) everywhere.
- **D-ARCH-9:** UUID primary keys for business tables.
- **D-ARCH-10:** No schema change without an Alembic migration.
- **D-ARCH-11 (Stage 9):** No PostgreSQL Row-Level Security for v1.
  App-level `restaurant_id` scoping is the tenancy mechanism (§ 5),
  covered by negative tests on every surface; one shared pool user
  means RLS would need per-session `SET LOCAL` plumbing, policies on
  17 columns across 14 tables plus 6 parent-scoped child tables, and
  seed/bootstrap/test changes — a second enforcement layer to keep in
  sync, for no added protection at one DB user. Revisit if a second DB
  role appears, multi-tenancy moves beyond one-restaurant-per-user, or
  an app-level scoping escape is found. (Evidence in § 9, "RLS
  decision".)
- **D-ARCH-12 (Stage 9):** Security headers are set by the app
  (`X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`,
  `Cache-Control: no-store` on `/api/*`); no CSP (Vite inline assets)
  and no HSTS in the app (no TLS termination in dev) — HSTS is a
  Stage 10 reverse-proxy decision.
- **D-ARCH-13 (Stage 9):** Frontend types stay hand-written in v1,
  pinned field-for-field by backend contract tests
  (`tests/test_contracts.py`); OpenAPI generation
  (`openapi-typescript`, checked-in file + CI drift check) is deferred
  to Stage 10.
