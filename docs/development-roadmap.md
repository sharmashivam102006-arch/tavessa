# Tavessa — Development Roadmap

> Status: Blueprint v1 — 23 Nov 2026. No application code built yet.
> Companion docs: `project-overview.md`, `architecture.md`,
> `database-design.md`, `api-plan.md`.

## 0. How to use this roadmap

- Work **in order, one stage at a time**. Do not skip stages, do not build all
  four interfaces at once, and do not start a stage until the previous stage's
  acceptance criteria pass.
- Each stage ends with a **verification step** (tests + a live check), not with
  "the code is written". A stage is not complete because its endpoints exist.
- **Decisions needed** are listed per stage. Answer them before the stage's
  implementation, and record the answer in the relevant doc — do not carry an
  undecided tax/rounding/refund rule into the billing stage.
- Estimated effort is deliberately **not** given in days here: it depends on
  who is building and how much is done per session. The ordering and the
  acceptance criteria are the commitment; estimates are made per stage when
  work starts.
- At the end of every stage: run the full test suite, update this roadmap's
  status table (§ 7), and commit with a message naming the stage.

### Status table

| Stage | Name | Status |
|---|---|---|
| 0 | Blueprint (this document set) | **Done — 23 Nov 2026** |
| 1 | Project foundation & architecture | **Done — 23 Nov 2026, verified (see Stage 1 report below)** |
| 2 | Guest interface & QR ordering | **Done (frontend) — 23 Nov 2026, verified (see Stage 2 report below).** Guest UI built against the api-plan.md contract with labelled Zaika sample data; backend half of Stage 2 (tables/menu/orders endpoints) deferred to Stage 3 per the Stage 2 instruction. |
| 3 | Backend APIs & database integration | **Done (guest scope) — 23 Nov 2026, verified (see Stage 3 report below).** Guest backend only: models/migrations/seed, QR sessions, menu, orders, help calls. The roadmap's original Stage 3 scope (auth, staff/kitchen/admin endpoints, bills, reviews, audit) was narrowed by the Stage 3 instruction to the guest domain — the rest stays open and moves to Stages 4–7. |
| 3.1 | Concurrent idempotency hardening | **Done — 23 Nov 2026, verified.** Session-scoped atomic claim-before-create; genuine 8-thread concurrency test on real Postgres (old code created 8 orders, new code creates 1). See Stage 3.1 report below. |
| 4 | Kitchen Display System | Not started |
| 5 | Waiter / Staff dashboard | Not started |
| 6 | Billing & cash payments | Not started |
| 7 | Owner / Admin dashboard | Not started |
| 8 | Real-time integration & synchronisation | Not started |
| 9 | Testing, security & reliability | Not started |
| 10 | Deployment & documentation | Not started |

> Note on stage ordering: the brief lists Guest (Stage 2) before Backend APIs
> (Stage 3). In practice Stages 2 and 3 overlap — the guest UI needs the menu
> and order endpoints to do anything real. The intended reading is: **Stage 2
> builds the guest UI against the API contract defined in `api-plan.md`,
> using the Stage 1 skeleton; Stage 3 is where the backend is completed and
> hardened behind it.** If that overlap is uncomfortable, merge the backend
> work for menu + orders into Stage 2 and keep Stage 3 for the remaining
> domains (tables, help calls, staff, reviews). Record the choice when Stage 2
> starts; do not silently reorder the roadmap.

---

## Stage 1 — Project foundation and architecture

**Status: Done — 23 Nov 2026 (verified).** Backend: FastAPI + SQLAlchemy 2.0 +
Alembic + psycopg, Pydantic settings, JSON logging, error envelope (incl. a
404 envelope — fixed during verification), Argon2id hashing (decision
D-ARCH-6 settled: Argon2id, no bcrypt fallback), health endpoint. Frontend:
React 19 + TS + Vite 8, Tailwind v4, shadcn/ui (components.json + Button),
React Router (/, /t/:tableToken, /staff, /kitchen, /admin, /login), Zustand,
TanStack Query, Lucide, API client, `formatINR(paise)`, brand home page with
a live backend-status pill. Verified: `pytest` 5 passed / 1 skipped,
`npm run build` clean, backend health `{"status":"ok","database":"ok"}`
against a real PostgreSQL 16.2, `alembic upgrade head` exit 0, frontend dev
server serves the home page and proxies `/api/v1/health` to the backend.
Docker is not installed in this sandbox, so `docker compose` was not run
here — it is file-complete and must be run on a machine with Docker before
it is claimed working. Git initialised (local only; no remote yet).

**Goal:** a runnable skeleton that proves the stack, the layout, and the
deployment shape — with no business features yet.

**Work:**

- [ ] Initialise Git + GitHub repo from the layout in `architecture.md` § 11.
  `.gitignore`, `README.md` (already written — keep it), `.env.example`
  (every key, placeholder values only).
- [ ] `docker-compose.yml`: `api` + `web` + `db` (PostgreSQL 16). Health
  checks on `db`; `api` waits for `db`. No Redis service yet (see
  `architecture.md` § 6.3).
- [ ] Backend skeleton: FastAPI app factory, `core/config.py` (Pydantic
  settings), structured logging, error envelope, CORS from env, `/health`
  endpoint (`{ "status": "ok" }` + DB check), OpenAPI enabled in dev.
- [ ] Alembic initialised; first migration creates `restaurants` only (or an
  empty baseline — decide and record; the full schema lands in Stage 3).
- [ ] Frontend skeleton: Vite + React + TypeScript, Tailwind, shadcn/ui
  initialised, React Router with the four layouts (empty pages), TanStack
  Query provider, Zustand set up, `shared/api/client.ts` (base URL, error
  parsing), `formatINR(paise)` helper, OpenAPI type generation wired up
  (`openapi-typescript`).
- [ ] CI (`.github/workflows/ci.yml`): backend lint + pytest (empty suite
  passes), frontend `tsc` + build. Dependabot / `pip-audit` enabled.
- [ ] Record the password-hashing decision (Argon2id vs bcrypt-12 —
  `architecture.md` D-ARCH-6) and the dependency-management decision
  (`pyproject.toml` vs `requirements.txt`).

**Decisions needed:** hashing algorithm; `pyproject.toml` vs
`requirements.txt`; Zaika seed values (D10 — tables, stations, opening hours).

**Acceptance criteria:**

- [ ] `docker compose up` brings up all three services; `/health` returns ok.
- [ ] Frontend builds and serves; all four routes render their layout shells.
- [ ] CI passes on a clean checkout.
- [ ] No secret is committed; `.env.example` lists every required variable.

**Exit gate:** the skeleton runs end-to-end (browser → frontend → API → DB)
with a health check. Nothing else is claimed.

---

## Stage 2 — Guest interface and QR ordering

**Status: Done (frontend) — 23 Nov 2026 (verified).** Guest interface
built in `frontend/src/features/guest/` against the contracts in
`api-plan.md` (§§ 3, 4, 5, 7, 10): menu browsing (search, veg-only,
sticky category bar with scroll-spy), item detail sheet (variants,
add-ons, instructions, live price), per-table-token persisted cart
(Zustand + localStorage), order review with a generated idempotency
key, confirmation (renders only from a real 201 response), order
tracking timeline (Placed → Accepted → Cooking → Ready → Served, plus
Cancelled), help-call UI, bill view with equal/custom splits
(remainder rule) and cash-only Pay by Cash request flow. Shared API
types extended in `src/shared/api/types.ts`; client extended with
per-request headers + `createIdempotencyKey()`; no second client.
Zaika sample menu: 6 categories × 4 items (24 total, 2 sold out,
several popular), clearly marked as demo data. Honesty rules enforced:
the backend still exposes only `GET /api/v1/health`, so every write
(order/help/bill) attempts the real endpoint and, on 404, shows the
not-connected state naming the endpoint and the stage that adds it,
keeping the cart; tax shows no invented line (D1 undecided); totals
are labelled ESTIMATED; the table badge reads "Table — not verified
yet" until a real session response verifies it; no fake orders,
confirmations, help requests, payments or WebSocket progress.
Verified: `npm run build` clean, Vitest **32 passed** (pricing, split,
cart, session-error mapper), backend `pytest` 5 passed / 1 skipped
(backend untouched — `git status` shows frontend-only changes), Vite
dev server curl checks all 200 (`/`, `/t/demo`, `/t/demo/cart`,
`/t/demo/bill`, `/t/demo/orders`, `/t/demo/help`, `/t/demo/review`,
`/staff`), `/api/v1/health` proxied ok, guest endpoints confirmed
404 (session/menu), live browser run: added Half butter chicken +
extra gravy and paneer tikka (₹299 + ₹249 = ₹548 estimated, correct),
Place Order returned the honest not-connected state with the
idempotency key. 13 real screenshots in
`docs/stage2-screenshots/`. Known sandbox limits: Docker not
installed here (Compose still unverified); Unsplash food photos do
not load in the sandbox's headless Chromium (egress proxy breaks
non-loopback TLS for the browser — curl fetches them fine), so
screenshots show the designed FoodImage placeholder fallback; the
401 invalid-token / 410 expired branches are unit-tested
(mapSessionError) but not live-verifiable until the backend returns
those codes in Stage 3. The backend half of this stage (tables, menu,
orders endpoints + seed) is deferred to Stage 3, per the Stage 2
instruction — the roadmap's Stage 2 acceptance criteria that need
the backend (order placement, idempotency, 422s, tracking from DB)
are therefore still open and move to Stage 3.

**Goal:** a guest can scan a table QR code, browse Zaika's menu, build a cart,
and place an order that the backend validates and stores.

**Work:**

- [ ] Backend: `restaurants`, `dining_tables`, `table_tokens`, `table_sessions`,
  `menu_categories`, `menu_items`, `item_variants`, `add_ons`,
  `item_add_ons` models + migrations + seed (Zaika: tables, stations,
  menu — realistic demo data, marked as demo).
- [ ] Backend: QR token issue/rotate/revoke (`table_tokens`, hashed at rest);
  session open/resume (`GET /guest/t/:tableToken`, `GET /guest/session`);
  guest menu endpoints (`GET /guest/menu`).
- [ ] Backend: order placement (`POST /orders`) with full server-side
  validation, price recomputation, snapshots, one-transaction order +
  items + tickets, `Idempotency-Key` contract (`api-plan.md` § 7).
- [ ] Frontend (guest): menu browsing (categories, search, veg filter),
  item detail (variants, add-ons, instructions), cart (Zustand, per session),
  order placement with a generated idempotency key, order confirmation,
  live tracking view (`GET /guest/orders/:id`, polling in this stage —
  WebSocket arrives in Stage 8; the tracking view must already re-fetch).
- [ ] QR code generation for printing (admin-side seed script or a small
  admin action — the printed QR itself is an owner operation; the code that
  renders the QR from a token lives here).

**Decisions needed:** D4 (QR printing), D5 (session expiry), D8 (guest
cancellation window).

**Acceptance criteria:**

- [ ] Scanning a printed Zaika Table 5 QR opens the menu on a phone; a token
  for Table 5 cannot open Table 6's session (`403`/`404`).
- [ ] Placing an order with a tampered price in the request still charges the
  correct server-computed total (negative test).
- [ ] Double-tapping "Place order" (same idempotency key) creates exactly one
  order (negative test).
- [ ] An order for an unavailable item is rejected (`422`); a variant/add-on
  that does not belong to the item is rejected (`422`).
- [ ] The guest tracking view shows the order status from the DB after a
  manual refresh.

**Exit gate:** the golden path steps 1–5 (`project-overview.md` § 5) work
live on two devices (phone + laptop).

---

## Stage 3 — Backend APIs and database integration

**Status: Done (guest scope) — 23 Nov 2026 (verified).** Models +
migration + Zaika seed for the guest domain: restaurants, branches,
dining tables 1–10, QR tokens (SHA-256 hashed, never stored raw),
table sessions (one active per table), 6 categories + 24 menu items
with exact demo prices/variants/add-ons (31 variants, 22 add-ons,
2 sold out), orders + order items + per-item add-on snapshots,
help calls, idempotency keys. Guest endpoints live:
`GET /guest/t/:token` (401 unknown / 410 revoked-or-expired),
`GET /guest/session`, `GET /guest/menu` (`?categoryId=`,
`?search=`), `POST /orders` (server-recomputed prices, required
`Idempotency-Key`, replay returns the stored response with
`Idempotency-Replayed: true`, same key + different body → 422),
`GET /guest/orders` (paginated), `GET /guest/orders/:id`
(cross-table → 404), `POST /orders/:id/cancel` (only while
`placed`, else 409), `POST /guest/help-calls` (one open call per
session, second → 409; `plates_cutlery` alias accepted, always
returned as `plates`). Wire format is camelCase per the Stage 3
reconciliation (see `api-plan.md` § 1); statuses are lowercase
(`placed`/`accepted`/`preparing`/…; item `queued`/`preparing`/…);
tax stays 0 (D1 undecided); order numbers are `ZAIKA-0001` running
numbers; demo tokens expire in 30 days (provisional, D5 open).
Frontend: sample-menu/restaurant fallback now applies ONLY to the
`/t/demo` preview — real tokens use the real API and surface errors;
cart/review/layout resolve lines from the fetched menu (UUID ids);
OrderReviewPage clears the cart only after a confirmed 201 and
reuses the same idempotency key on retry. Verified: backend
`pytest` **37 passed / 1 skipped**, frontend Vitest **32 passed**,
`npm run build` clean, `alembic upgrade head` + downgrade/upgrade
cycle on a real PostgreSQL (pgserver), seed CLI run twice with
identical counts, live API check (health ok; session open; menu;
real order `ZAIKA-0001`, subtotal/total 59800 paise for 2× Half
butter chicken + gravy; replay returns the same order with the
replayed header; invalid token 401; revoked token 410), and a Vite
dev-server smoke (guest page 200, `/api/v1/health` proxied ok).
**Still open (needs later stages, named):** staff/JWT auth and all
staff/kitchen/admin endpoints, kitchen tickets + status transitions
(Stage 4), bills/payments/splits/reviews (Stage 6), WebSocket
(Stage 8), tax decision D1, session-expiry decision D5, guest
cancel window D8. BillPage still resolves cart lines from the
sample dataset — its Stage 6 wiring will switch it to the real
menu/bill.

**Goal:** the full backend domain surface from `api-plan.md`, with the whole
schema migrated and the golden path working end-to-end through REST.

**Work:**

- [ ] Remaining models + migrations: `users`, `staff_assignments`,
  `kitchen_stations`, `kitchen_tickets`, `orders` (complete),
  `order_items`, `bills`, `bill_items`, `bill_splits`, `payments`,
  `refunds`, `help_calls`, `reviews`, `notifications`, `audit_logs`,
  `idempotency_keys`, `branches`.
- [ ] Auth: login / refresh (rotating) / logout / `me`; RBAC dependencies;
  restaurant scoping on every query.
- [ ] Services + routes: tables/floor, help calls, kitchen ticket reads,
  order status transitions (item + order, with derivation), staff
  management (create/list/edit/deactivate), reviews.
- [ ] Audit logging wired into every sensitive operation (see
  `architecture.md` § 9).
- [ ] Backend test suite: pricing (incl. split remainder), status machines,
  idempotency, cross-tenant/cross-table denial, tampered-price rejection.
  Test Postgres in CI (not SQLite — the schema uses Postgres features).

**Decisions needed:** D1 (tax — must be decided before billing totals are
finalised; if undecided, implement `tax_mode='inclusive', tax=0` explicitly
and record it as provisional).

**Acceptance criteria:**

- [ ] Every endpoint in `api-plan.md` §§ 2–5, 8 exists in OpenAPI with its
  schemas; no endpoint is "planned but missing".
- [ ] The full golden-path sequence (order → accept → prepare → ready →
  serve) can be driven by API calls alone, with correct status derivation.
- [ ] Cross-tenant and cross-table negative tests pass.
- [ ] `pytest` passes against test Postgres; no test uses a mocked database
  for a transactional behaviour.

**Exit gate:** the backend is feature-complete for REST; the remaining stages
are interfaces, billing, realtime, and hardening — not new domains.

## Stage 3.1 — Concurrent idempotency hardening

**Status: Done — 23 Nov 2026 (verified).** Sequential duplicates
were already safe; concurrent duplicates were not — the claim was
stored after the order committed, so 8 simultaneous requests with
the same key created 8 orders (proven by the negative control
below). Fixed with session-scoped atomic claim-before-create
(`api-plan.md` § 10, `database-design.md` § 3.26): the claim row is
inserted first, the DB unique constraint on
`(restaurant_id, table_session_id, endpoint, key)` serialises
concurrent claimants, the order transaction sets the claim's
`order_id`, and the stored response is filled in afterwards. A
competing request polls the claim for up to 10 s and replays the
stored response (or the response rebuilt from the order); a still
-pending owner yields `409 idempotency_in_progress`. A failed order
creation deletes the claim, so a corrected retry can proceed.

**Verification:** new `tests/test_orders_concurrency.py` (real
PostgreSQL via pgserver, 8 threads, own engine + per-request
sessions): 8 simultaneous same-key requests → exactly 1 order, 1
order number, 1 order id in every response, 2 order-item rows, 1
owner + 7 replays; same key + different body → 422; same key in a
second session → a second, distinct order. Negative control: the
same test against the pre-3.1 code created **8 orders**. Migration
`3a1b7c9d4e2f` verified on real Postgres: backfill from orders,
orphan-row deletion, constraint swap, downgrade −1, re-upgrade.
Full suite: backend pytest 40 passed / 1 skipped, frontend Vitest
32 passed, `npm run build` clean. No API or frontend contract
changed.

---

## Stage 4 — Kitchen Display System

**Goal:** the kitchen sees new orders instantly, works them by station, and
updates item status — with timers and ready notifications.

**Work:**

- [ ] Backend: ticket derivation on order placement (one ticket per
  order × station); item-status endpoints; ticket-status derivation;
  SLA/breach flag (threshold from restaurant settings).
- [ ] Frontend (kitchen): ticket board (columns or filter by station:
  Main Kitchen / Tandoor / Beverages), large cards (order number, table,
  items, quantities, variants/add-ons, special instructions), elapsed-time
  badges, one large status button per item, ticket accept (`preparing`).
- [ ] Ready flow: item `ready` → order/ticket derivation → staff + guest
  notified (REST re-fetch in this stage; WebSocket push arrives in Stage 8).
- [ ] Decide and record: ticket status — derived-only, or an explicit
  `PATCH /kitchen/tickets/:id/status` for `preparing` (see `api-plan.md`
  § 6). Do not implement both.

**Decisions needed:** D7 (KDS hardware + offline fallback), ticket-status
decision above.

**Acceptance criteria:**

- [ ] An order placed from the guest phone appears on the KDS board without
  a manual refresh (polling is acceptable in this stage; Stage 8 replaces
  it with push — the acceptance is "appears", the mechanism is revisited).
- [ ] Items are grouped correctly by station; a Beverages-only order creates
  no Main Kitchen ticket.
- [ ] Marking an item `ready` updates the order status derivation and the
  guest tracking view on refresh.
- [ ] An illegal transition (`queued → served`) is rejected (`409`).
- [ ] The board is readable from 2 metres on the target KDS screen (manual
  check on the actual hardware, or the closest available screen).

**Exit gate:** golden-path steps 6–9 work live (guest phone + KDS screen in
the same room).

---

## Stage 5 — Waiter / Staff dashboard

**Goal:** waiters can run the floor from the staff interface — tables, orders,
help calls, serving, bill requests.

**Work:**

- [ ] Backend: floor-map endpoint (`GET /tables` with session/order/call
  flags), table status transitions, session close, staff assignments.
- [ ] Frontend (staff): login, floor map (zone-grouped, status colours),
  table detail (session, orders, bill), incoming-orders list, help-call
  list (accept/resolve), mark-served flow (only from `ready`), "my tables"
  view.
- [ ] Help-call flow end-to-end: guest request → staff list + floor flag →
  accept (assigned) → resolve → guest sees acknowledgement/resolution.

**Decisions needed:** floor-map coordinates (are `position_x/y` seeded
manually for Zaika, or is the map a simple grid in v1?); assignment model
(does a waiter see only assigned tables, or all tables with assigned ones
highlighted?).

**Acceptance criteria:**

- [ ] A guest help call appears on the staff floor map and list within
  seconds; accepting assigns it to that waiter and the guest sees the
  acknowledgement.
- [ ] Marking an item served before it is `ready` is rejected (`409`).
- [ ] The floor map correctly shows available / occupied / cleaning /
  out-of-service across a full visit (open → order → close → cleaning →
  available).
- [ ] A waiter cannot see or act on another restaurant's tables (negative test).

**Exit gate:** golden-path steps 10–11 work live.

---

## Stage 6 — Billing and cash payments

**Goal:** bills, splits, cash-payment requests, staff-confirmed collection,
receipts, refunds, and the daily cash report — with the cash-only rule
enforced by the API, not by the UI.

**Work:**

- [ ] Backend: bill generation (snapshot bill items, server totals), bill
  status machine, equal/custom splits (remainder rule, sum validation),
  `payment_requested`, payment confirmation (row lock + status guard +
  audit + receipt number), receipt data, refunds (owner only), daily cash
  report, bill void (owner, audited).
- [ ] Frontend (guest): bill view, split UI (equal/custom), "Pay by Cash"
  request, payment confirmation + receipt view (shown only after staff
  confirmation).
- [ ] Frontend (staff): bill-request list, cash-collection confirmation
  screen (amount validated against the bill/split, collector recorded),
  receipt view.
- [ ] Frontend (owner): bills/payments history, cash report.
- [ ] Rules to finalise in this stage (record in `project-overview.md` § 4
  if they change): bill-after-more-orders (regenerate vs second bill),
  overpayment/change handling (D9), refund limits (D3).

**Decisions needed:** D1 (tax — final), D2 (discounts), D3 (refund authority),
D9 (split remainder + change).

**Acceptance criteria:**

- [ ] A guest tapping "Pay by Cash" does **not** mark anything paid
  (negative test: bill stays `payment_requested`, no payment row exists).
- [ ] Staff confirmation creates exactly one payment, marks the bill/split
  paid, generates a receipt, and writes an audit row.
- [ ] A second confirmation for the same split fails (`409`) and returns the
  existing payment.
- [ ] Equal split of an odd-paise total sums exactly to the total; custom
  splits that do not sum are rejected (`422`).
- [ ] A bill's total does not change when a menu price changes after the
  order (snapshot test).
- [ ] The daily cash report reconciles: collected = sum of confirmed
  payments; outstanding = issued/requested bills not yet paid.
- [ ] A refund (owner only) adjusts payment + bill status and is audited; a
  waiter attempting a refund gets `403`.

**Exit gate:** golden-path steps 12–18 work live, including a real cash
handover at the table and a receipt the guest can see.

---

## Stage 7 — Owner / Admin dashboard

**Goal:** the owner can run the restaurant from the dashboard — live
operations, sales, cash, menu, staff, history, analytics, settings.

**Work:**

- [ ] Backend: overview aggregates, sales analytics (by day/category/item,
  busiest hours, AOV, top items), order/bill history with filters, staff
  CRUD + assignments, restaurant settings, QR rotation, review moderation
  (per D6).
- [ ] Frontend (admin): overview (occupancy, active orders, kitchen depth,
  open calls, bills awaiting cash, today's sales/cash), menu management
  (categories, items, variants, add-ons, availability toggle, image upload),
  staff management, order/bill/payment history, analytics views, settings.
- [ ] Menu price-change audit visible in the dashboard (or at least in the
  audit log view, if a dedicated view is deferred — record the choice).

**Decisions needed:** D6 (review moderation), D10 (seed settings finalised).

**Acceptance criteria:**

- [ ] The overview matches the live state (open the dashboard during a live
  order and watch occupancy, orders, and cash update on refresh).
- [ ] Toggling an item unavailable removes it from orderable items for new
  guest orders and does not affect orders already placed.
- [ ] A menu price change is audited and does not change any existing bill.
- [ ] Rotating a table's QR token immediately invalidates the old token.
- [ ] Analytics numbers reconcile with the underlying orders/bills (spot-check
  by hand for one day).

**Exit gate:** the owner can run a full service (menu change, staff check,
cash check) from the dashboard without touching the database.

---

## Stage 8 — Real-time integration and synchronisation

**Goal:** all four interfaces stay in sync over WebSockets, with the database
as the source of truth and clean reconnection behaviour.

**Work:**

- [ ] Backend: WebSocket endpoints (`/ws/restaurant`, `/ws/table-session`),
  auth at connect, channel subscription + authorisation, `ConnectionManager`
  (in-process), `EventBus` interface, event envelope with `version`,
  publish-after-commit wired into every service that mutates shared state
  (orders, tickets, tables, help calls, bills, payments, menu availability).
- [ ] Frontend: WebSocket hook + provider; on event, invalidate the matching
  TanStack Query keys; on reconnect, re-fetch; `version`-gap → re-fetch.
  Replace Stage 2/4 polling with push (polling remains as a fallback only
  if the socket is unavailable — record the choice).
- [ ] Decide and record: WS auth — query-param token (v1) vs short-lived WS
  ticket issued via REST (see `api-plan.md` § 9.1).
- [ ] Notifications: persistent `notifications` rows for missed events
  (help calls, payment requests, order ready) + staff inbox read state.

**Decisions needed:** WS auth mechanism; Redis trigger assessment (is a
second `api` replica planned? If not, Redis stays out).

**Acceptance criteria:**

- [ ] Order placed on the guest phone appears on the KDS and staff screens
  without refresh; item `ready` updates the guest tracking view within
  seconds; bill payment updates the owner dashboard.
- [ ] Killing and reopening a client (or dropping the network for 30s) brings
  it back to the correct current state via re-fetch — no stale "ready" or
  missing order.
- [ ] A guest socket cannot subscribe to `restaurant:{id}` (negative test).
- [ ] No business action is performed from a WebSocket message (code review
  + a test that sends a forged event and observes no state change).
- [ ] Two browser sessions (guest + kitchen) in the same room stay in sync
  for a full order lifecycle, verified live.

**Exit gate:** the 18-step golden path runs end-to-end with no manual
refresh anywhere.

---

## Stage 9 — Testing, security, and reliability

**Goal:** prove the system is correct and safe under the failure modes in
`architecture.md` § 12, and fix what the tests find.

**Work:**

- [ ] Full test pyramid (§ 10 of `architecture.md`): unit (pricing, splits,
  machines, idempotency), API/integration (golden path on test Postgres),
  frontend unit (Vitest), E2E (Playwright against Docker Compose — guest,
  waiter, KDS, owner flows).
- [ ] Security pass: re-run the § 9 checklist in `architecture.md`
  (hashing, JWT rotation/revocation, QR tokens, RBAC + tenant negative
  tests, input validation, CORS, rate limits, headers, audit coverage,
  secret scan, dependency scan). Consider PostgreSQL RLS as defence-in-depth
  — decide yes/no and record it.
- [ ] Reliability pass: duplicate-submission, concurrent status updates
  (two cooks, same item), double payment confirmation, KDS offline/reconnect,
  session expiry, bill-after-more-orders, cancelled-item-after-bill.
  Each failure mode gets a test or a documented manual check.
- [ ] Load sanity: a realistic service (e.g. 10 concurrent tables ordering)
  against the Compose stack; record what breaks first (it will be
  informative, not a pass/fail gate for v1, unless it breaks the golden path).
- [ ] Fix what is found. Do not defer a money-correctness bug to "later".

**Acceptance criteria:**

- [ ] `pytest` (backend) + Vitest + Playwright all pass in CI on a clean checkout.
- [ ] Every risk R1–R12 in `architecture.md` § 12 has a test, a manual check,
  or a recorded decision explaining why it does not apply.
- [ ] No endpoint accepts a client-supplied price (grep + test).
- [ ] No secret in git history (scan); no `TODO` claims a feature complete.

**Exit gate:** the team is willing to run a real service on this build.

---

## Stage 10 — Deployment and documentation

**Goal:** a deployable, operable system with documentation a new person can
follow — and a demo Zaika can actually run.

**Work:**

- [ ] Production Compose (or the chosen host): `api` + `web` + `db`, with
  backups for Postgres (documented restore procedure — a backup that has
  never been restored is not a backup), log retention, and health checks.
  Reverse proxy / TLS termination documented (host-dependent; record the
  choice — e.g. Caddy/Nginx/Cloudflare — when the host is chosen).
- [ ] Environment documentation: every variable, its source, its rotation
  procedure (especially `SECRET_KEY` and QR tokens). Production OpenAPI
  (`/docs`) disabled or owner-gated (record the choice).
- [ ] Seed + QR printing runbook for Zaika: how to create the restaurant,
  tables, menu, staff, print QR codes, rotate a token, close a session,
  reconcile the till at end of day.
- [ ] User guides (one page each): guest (a QR poster already explains it —
  keep it to the poster), waiter, kitchen, owner. Keep them short; the UI
  should need little explaining.
- [ ] Final README update: status, how to run locally, how to run the demo,
  known limitations (the open decisions that remain open, the Redis trigger,
  the RLS decision).
- [ ] Demo run: a full service on the deployed build (or the Compose stack
  on a laptop in the restaurant) — guest phone, KDS screen, staff tablet,
  owner dashboard — with a real cash payment and a reconciled till.

**Decisions needed:** hosting (VPS vs PaaS — e.g. Render/Railway/Fly vs a
VPS with Docker; the Compose file works on either, the choice is operational,
not architectural); domain + TLS; Postgres backup schedule.

**Acceptance criteria:**

- [ ] A clean machine can follow the README and have the system running
  without asking the author a question.
- [ ] A Postgres backup can be restored (tested, not assumed).
- [ ] The demo service completes the golden path on the deployed build.
- [ ] The docs name every known limitation honestly; nothing is claimed
  complete that is not verified.

**Exit gate:** Zaika can run a real service on Tavessa.

---

## 8. Unresolved decisions (master list — from `project-overview.md` § 7)

| # | Decision | Needed by |
|---|---|---|
| D1 | Tax: GST-inclusive vs exclusive, service charge, rounding | Stage 3 / 6 |
| D2 | Discounts / complimentary items | Stage 6 |
| D3 | Refund authority + limits | Stage 6 |
| D4 | QR printing (format, size, who prints) | Stage 2 |
| D5 | Table session expiry + close rules | Stage 2 |
| D6 | Review moderation (immediate vs held, editable?) | Stage 2 / 7 |
| D7 | KDS hardware + offline fallback | Stage 4 |
| D8 | Order cancellation window | Stage 3 |
| D9 | Split remainder + overpayment/change | Stage 6 |
| D10 | Zaika seed values (tables, stations, hours, settings) | Stage 1 |

Record each answer in the doc that owns it (tax in `project-overview.md` § 4
and `database-design.md` § 3.1; the rest where they are named above) and tick
it off here. An unanswered decision is not a blocker for the blueprint —
it is a blocker for the stage that depends on it.

## 9. What "done" means for the whole project

Tavessa v1 is done when:

1. The 18-step golden path runs live across all four interfaces with no
   manual refresh, on the deployed build.
2. A cash payment is confirmed only by staff after collection, with a receipt
   and a reconciled daily cash report.
3. The test suite (unit + API + E2E) passes in CI.
4. The security checklist in `architecture.md` § 9 is verified, not just read.
5. The README + runbooks let a new person run Zaika's service without help.

Anything beyond that — online payments, delivery, reservations, loyalty —
is a v2 decision, recorded separately, not smuggled into v1.
