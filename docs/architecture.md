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
/admin                   Owner — login required, role owner|admin
  /admin                  (overview)
  /admin/menu
  /admin/tables
  /admin/staff
  /admin/orders
  /admin/bills
  /admin/analytics
  /admin/settings
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

At that point, add Redis as a **pub/sub broker behind the same `EventBus`
interface** (`publish(channel, event)` / `subscribe(channel)`), with the
in-process manager as the local fan-out. The service code does not change;
only the broker implementation does. Docker Compose gains a `redis` service
then, not before. Do not add Redis in Stage 1 "because realtime usually needs
it" — it does not, at one replica.

### 6.4 Reconnection and ordering

- Clients reconnect with exponential backoff and, on reconnect, re-fetch their
  current state via REST before rendering live updates again.
- Events carry a per-restaurant monotonically increasing `version` (from a
  `restaurant_events` sequence or the DB — implementation detail in Stage 8).
  A client that sees a gap re-fetches. Events are not guaranteed ordered
  across channels; they are ordered within a channel by `version`.
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

### 8.1 Staff / owner (password users)

- Passwords hashed with **Argon2id** (preferred) or bcrypt cost 12 — one
  algorithm, configured in `core/security.py`, never MD5/SHA-1/plaintext.
  (Argon2id is the recommendation; if the chosen password library in Stage 1
  makes Argon2id awkward, bcrypt-12 is the accepted fallback — record the
  choice in Stage 1, do not silently pick.)
- Login: `POST /auth/login` → short-lived **access token (JWT, 15 min)** +
  **refresh token (rotating, HttpOnly cookie, 7 days)**. Access tokens are
  sent as `Authorization: Bearer …`; the frontend keeps the access token in
  memory (not `localStorage`).
- JWT claims: `sub` (user id), `restaurant_id`, `role`, `exp`, `jti`.
  Every request re-validates the user is still active and still belongs to
  that restaurant (a deactivated staff member's token stops working on the
  next request after a short cache, not only at expiry — Stage 1 detail).
- Refresh-token rotation: each refresh issues a new refresh token and revokes
  the old one; reuse of a revoked token revokes the whole token family
  (theft detection). This is implemented in Stage 1, not "later".
- RBAC is enforced by dependencies (`require_role("owner")`,
  `require_role("waiter", "owner")`, …) on every route, plus a restaurant
  scope check (`current_user.restaurant_id == resource.restaurant_id`).
  The frontend hiding a button is never the security control.

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

### 8.3 Authorisation matrix (summary — enforced per endpoint in `api-plan.md`)

| Resource | Guest (own session) | Waiter | Kitchen | Owner |
|---|---|---|---|---|
| Menu (read) | Yes | Yes | Yes | Yes |
| Menu (write) | No | No | No | Yes |
| Place order | Yes (own table) | No* | No | No* |
| Order status (read) | Own session only | Restaurant | Restaurant | Restaurant |
| Item status (write) | No | Serve only | Prepare/ready | Yes |
| Help calls | Create own | Accept/resolve | Read | Read |
| Bill (read) | Own session | Restaurant | No | Restaurant |
| Bill (create/request) | Yes (own session) | Yes | No | Yes |
| Payment confirm | No | Yes | No | Yes |
| Refund | No | No | No | Yes |
| Staff manage | No | No | No | Yes |
| Reviews | Create own (after paid) | No | No | Moderate/read |

\* Waiters/owners placing an order on a guest's behalf is a possible future
addition, not a v1 capability — do not build it speculatively.

## 9. Security — checklist (enforced in Stage 9, designed now)

- [ ] Password hashing (Argon2id / bcrypt-12), no plaintext secrets anywhere.
- [ ] JWT: short access, rotating refresh, revocation on logout / role change /
      deactivation, `jti` tracking.
- [ ] QR tokens: random, hashed at rest, revocable, scoped, expirable.
- [ ] RBAC on every route; tenant scoping on every query; tests that prove a
      cross-tenant and cross-table read fails (see § 10).
- [ ] Server-side validation of all input (Pydantic), including: item/variant/
      add-on existence, availability, quantities (≥ 1, ≤ a sane max), price
      recomputation, bill-split sums, payment amount ≤ amount due.
- [ ] SQL injection: prevented by SQLAlchemy parameterisation; no raw SQL with
      string interpolation. Any raw SQL is reviewed.
- [ ] CORS: explicit allowlist from env (`CORS_ORIGINS`), credentials only for
      listed origins, no `*` with credentials.
- [ ] Rate limiting: login and order-placement endpoints rate-limited
      (SlowAPI or equivalent, in-process in v1). QR-token brute-force is
      rate-limited per IP.
- [ ] Security headers: `X-Content-Type-Options`, `X-Frame-Options`,
      `Content-Security-Policy` (appropriate for the frontend), HSTS in
      production (via reverse proxy / middleware).
- [ ] Audit logging for: login failures, payment confirmation, refunds, bill
      voids, menu price changes, staff role changes, QR rotation, session
      closure by staff.
- [ ] No secrets in git, logs, or the frontend bundle. `.env.example` contains
      placeholders only.
- [ ] Dependencies pinned and scanned (`pip-audit` / Dependabot or equivalent)
      in CI from Stage 1.
- [ ] Uploaded food images: validated type/size, stored outside the web root
      with a served URL (local `uploads/` in dev; object storage is a later
      deployment decision, not a v1 code change — the storage interface is
      abstracted).

## 10. Testing strategy

| Level | Tool | What it proves |
|---|---|---|
| Unit | Pytest | Pricing (incl. splits/remainder), status-machine transitions, idempotency, token hashing, permission helpers. Money tests use integer paise and boundary cases (₹0, ₹1, odd-paise splits). |
| API / integration | Pytest + FastAPI `TestClient` + test Postgres | Full golden path (§ 5 of `project-overview.md`) against a real DB: order → kitchen → ready → bill → cash → receipt. Cross-tenant and cross-table access denied. Duplicate order with same idempotency key returns one order. |
| Frontend unit | Vitest + Testing Library | Cart maths (display only), status badges, split calculator, guards. |
| E2E | Playwright | Guest scan → order → track; waiter floor → help call → cash confirm; KDS ticket flow; owner dashboard loads with seeded data. Run against Docker Compose. |
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
│   │   │   ├── manager.py       # ConnectionManager (in-process v1)
│   │   │   ├── bus.py           # EventBus interface (+ Redis adapter later)
│   │   │   └── events.py        # event names + envelope builder
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
| R9 | Single-instance realtime breaks when scaled | Events lost across replicas | `EventBus` interface from Stage 8, Redis adapter behind it; no service code changes when the broker changes. |
| R10 | QR code photographed / shared | Strangers order on Table 5's tab | Revocable tokens, session expiry (D5), rotation by owner; no payment can be completed by a guest anyway, so the blast radius is a bogus order — which staff can cancel and which is auditable. |
| R11 | Scope creep — online payments, delivery, reservations | v1 never ships | Cash-only rule + non-goals list in `project-overview.md`; any addition needs a recorded decision and a roadmap change. |
| R12 | Menu price change rewrites history | Old bills change value | Price snapshots in `order_items`/`bill_items`; price changes only affect new orders. |

## 13. Decisions recorded in this document

- **D-ARCH-1:** Modular monolith, not microservices. (Rationale § 1.)
- **D-ARCH-2:** One frontend app with four feature areas, not four apps and
  not an Nx/Turborepo monorepo. (Rationale § 4.1.)
- **D-ARCH-3:** Native FastAPI WebSockets, DB-first broadcast, event envelope
  with summary + `version`. (Rationale § 6.)
- **D-ARCH-4:** Redis deferred; in-process broadcast in v1 behind an
  `EventBus` interface. Triggers for adding Redis are listed in § 6.3.
- **D-ARCH-5:** No background worker in v1; no Celery/RQ. (Rationale § 7.)
- **D-ARCH-6:** Password hashing Argon2id preferred, bcrypt-12 fallback —
  final choice recorded in Stage 1.
- **D-ARCH-7:** QR tokens are opaque random database tokens, hashed at rest,
  revocable — not JWTs.
- **D-ARCH-8:** Money as integer paise (`BIGINT`) everywhere.
- **D-ARCH-9:** UUID primary keys for business tables.
- **D-ARCH-10:** No schema change without an Alembic migration.
