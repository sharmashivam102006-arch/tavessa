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
| 3.2 | Idempotency crash-recovery hardening | **Done — 23 Nov 2026, verified.** Single-transaction claim+order+response; no separately committed claim for a crash to orphan. See Stage 3.2 report below. |
| 4 | Kitchen Display System | **Done — 23 Nov 2026, verified (see Stage 4 report below).** Order-level kitchen API + WebSocket + KDS board. |
| 5 | Waiter / Staff dashboard | **Done — 23 Nov 2026, verified (see Stage 5 report below).** Floor grid, active orders, serving (ready → served), help calls (acknowledge → resolve), staff WebSocket channel. |
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

## Stage 3.2 — Idempotency crash-recovery hardening

**Status: Done — 23 Nov 2026 (verified).** Root cause: Stage 3.1
used three separate transactions (claim commit → order commit →
response-fill commit), so a process crash after the first left a
committed pending claim with no order and no response — ordinary
exception handling cannot cover process termination. Fixed by
collapsing all three into ONE transaction (`api-plan.md` § 10,
`database-design.md` § 3.26): claim INSERT (flush, no commit) +
validation + order creation + response fill, one commit. Any
failure rolls everything back. Concurrency is PostgreSQL's
unique-index speculative insertion: a competing INSERT blocks
until the owner's transaction commits (unique violation → read the
committed row and replay) or aborts — a crash aborts it when the
connection drops, so the waiter proceeds as owner. No polling, no
lease, no fencing. The wait is bounded by `SET LOCAL lock_timeout
= '10s'` (PostgreSQL only), mapped to `409
idempotency_in_progress`. Pre-3.2 pending rows are handled in the
conflict path only (rebuild from `order_id`, or reclaim a stale
row older than 60 s). No schema migration was needed — the 3.1
constraint already covers the scope.

**Verification:** `tests/test_orders_concurrency.py` extended on
the same real-PostgreSQL pgserver fixture (9 tests): the 3 existing
concurrency tests pass unchanged (8 simultaneous same-key requests
→ exactly 1 order, 1 owner + 7 replays; different body → 422;
cross-session independence); new tests — failed placement leaves
no claim row and the same key succeeds with a corrected body;
abandoned claim without an order recovers as owner (1 order);
abandoned claim with an order replays it verbatim (0 new orders);
two competing recoveries of one stale claim create exactly 1
order; a held uncommitted claim transaction makes a competing POST
genuinely block for 2 s with no order, then complete as owner
after the holder rolls back (simulated crash); a 200 ms patched
lock timeout returns `409 idempotency_in_progress` promptly.
Full suite: backend pytest 46 passed / 1 skipped (3 consecutive
runs, 0 warnings), frontend Vitest 32 passed, `npm run build`
clean. No API or frontend contract changed.

---

## Stage 4 — Kitchen Display System

**Status: Done — 23 Nov 2026 (verified).** Order-level kitchen API
(`GET /kitchen/orders`, `GET /kitchen/orders/:id`,
`PATCH /kitchen/orders/:id/status`, `GET /kitchen/restaurants`) +
WebSocket `GET /ws/kitchen` + full-width KDS board at `/kitchen`
(New / Preparing / Ready, ticking elapsed times, one large action
button per ticket). Dev-only access: `KITCHEN_DEV_KEY` /
`VITE_KITCHEN_DEV_KEY` (production returns 503 until staff auth
lands). Verified: backend pytest 67 passed / 1 skipped (20 new
kitchen SQLite tests + 1 Postgres concurrency test: 8 simultaneous
PATCHes → exactly 1 success, 7× 409), frontend Vitest 47 passed
(15 new kitchen tests), `npm run build` clean, live Postgres
verification 15/15 (guest order → kitchen list → preparing →
ready, WS `order.created` / `order.status_changed` delivery, no
event after failed PATCH or replay, invalid transitions 409), 3
real screenshots in `docs/stage4-screenshots/`. **No migration**
— the order model already carried statuses, timestamps, version
and snapshots (`alembic upgrade head` re-run on real Postgres:
only the Stage 3/3.1 migrations apply). One real bug found and
fixed during verification: the Vite dev proxy's `/ws` string
shorthand does not forward WebSocket upgrades, so the KDS socket
sat on "Connecting…" in dev — the proxy entry is now
`{ target, ws: true }` and the socket reaches "Live" (verified
through the proxy: `order.created` received).

**Stage 4 recorded deviations (the Stage 4 brief OVERRIDES the
roadmap/blueprint):**
1. **Workflow is ORDER-LEVEL, not per-item.** No
   `PATCH /orders/:orderId/items/:itemId/status`. When an order
   transitions, its non-cancelled items move with it in the SAME
   transaction (placed→preparing: items queued→preparing, set
   `accepted_at` if null; preparing→ready: items →ready, set
   `ready_at`).
2. **NO `kitchen_tickets` table, NO stations.** A "ticket" IS an
   order + its item snapshots. Station grouping/filtering is
   deferred — menu items already carry a `station` TEXT field for
   later use.
3. **WebSockets arrive NOW for the kitchen (not Stage 8).** Guest
   tracking still refetches via REST.
4. **Statuses on the wire stay LOWERCASE.** Column mapping:
   New = placed (+accepted if ever present), Preparing =
   preparing, Ready = ready. Terminal states
   (served/completed/cancelled) are NOT in the active queue.

**Still open (named):** D7 (KDS hardware + offline fallback) —
no SLA/breach flag on the board because no threshold is
configured; staff/JWT auth (the dev key is the interim gate);
per-station filtering; `served`/`completed` transitions (Stage 5);
guest tracking over WebSocket (Stage 8); the in-process
ConnectionManager is single-process only (a second `api` replica
would not receive events — the Redis trigger in
`architecture.md` § 6.3).

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

**Status: Done — 23 Nov 2026 (verified).** Staff API
(`GET /staff/restaurants`, `GET /staff/tables`,
`GET /staff/tables/:id`, `GET /staff/orders`,
`PATCH /staff/orders/:id/serve`, `GET /staff/help-calls`,
`PATCH /staff/help-calls/:id/acknowledge`,
`PATCH /staff/help-calls/:id/resolve`) + WebSocket
`GET /ws/staff` + full-width staff board at `/staff` (floor grid,
table detail, "Ready to serve" first, help-call queue). Dev-only
access: `STAFF_DEV_KEY` / `VITE_STAFF_DEV_KEY` (production
returns 503 until staff auth lands). Verified: backend pytest 87
passed / 1 skipped (19 new staff SQLite tests + 1 Postgres
concurrency test: 8 simultaneous PATCH serve → exactly 1×200,
7×409), frontend Vitest 70 passed (23 new staff tests),
`npm run build` clean, live Postgres verification 29/29 (guest
order → kitchen ready → staff READY + floor flag → serve 200 with
`served_at` → kitchen queue no longer shows it; help-call flow
end-to-end with `help_call.opened` / `acknowledged` / `resolved`
WS events; invalid serve 409; cross-restaurant 404), 3 real
screenshots in `docs/stage5-screenshots/`. **No migration** — the
order model already carried `served_at`/`version` and the help
call already carried its lifecycle + timestamps.

**Stage 5 recorded deviations (the Stage 5 brief OVERRIDES the
roadmap/blueprint):**
1. **NO production JWT auth / login system.** The roadmap lists a
   "login" UI and Stage 3's deferred auth scope, but the brief § 6
   explicitly excludes production auth from this stage. Access is a
   DEVELOPMENT-ONLY staff key (`STAFF_DEV_KEY`, header
   `X-Staff-Dev-Key`, WS query `?dev_key=…`), exactly like Stage 4's
   kitchen key. No `/login` implementation, no `users` table, no JWT
   issuance. A guest table token does NOT grant staff access
   (tested); the kitchen key does NOT grant staff access (tested),
   and vice versa. The key is documented as dev-only in README +
   api-plan.md; it must never be presented as production auth.
2. **NO bill requests, NO session close, NO table-status
   transitions, NO staff assignments / "my tables" view, NO
   payroll/scheduling.** The brief assigns only: floor overview
   (read), active orders, serving, help calls. Deferred: bill
   requests + session close → Stage 6; table-status transitions
   (cleaning/out-of-service) → Stage 6 (table lifecycle); staff
   assignments / "my tables" → Stage 7 (owner/admin) or a later
   staff stage — decision still open; payroll/scheduling → out of
   v1 scope. Serving does NOT close the session and does NOT change
   the table status (recorded explicitly in api-plan.md § 4.2 and
   database-design.md § 6).
3. **Help-call resolve is acknowledged→resolved ONLY**
   (api-plan.md § 5). A waiter must acknowledge first; open→resolved
   is a 409, and the frontend enforces the two-step flow (an open
   call shows only "Acknowledge"). There is no assigned waiter: no
   `acknowledged_by`/`resolved_by` columns exist and none were
   added — there is no users system to reference yet.
4. **Statuses stay LOWERCASE on the wire.** Served transition:
   ready→served only. Serving sets `served_at`, `version += 1`,
   and moves non-cancelled items ready→served in ONE transaction
   under a row lock.
5. **Floor-map coordinates: simple grid in v1.** Tables 1–10 in
   number order; no `position_x/y` seeding. The "Decisions needed"
   question below is answered: grid now, coordinates later if a
   real floor plan is needed.

**Still open (named):** D1 (tax — still 0, undecided); D5 (session
expiry — demo-token CLI expiry is provisional at 30 days); D8
(guest cancellation window — guest cancel still only while
placed); staff/JWT auth (the dev key is the interim gate); the
in-process ConnectionManager is single-process only (the staff
channel reuses the SAME manager as the kitchen — a second `api`
replica would not receive events; see architecture.md § 6.3);
guest tracking over WebSocket (Stage 8).

**Goal:** waiters can run the floor from the staff interface — tables, orders,
help calls, serving, bill requests.

**Work (as built 23 Nov 2026 — see the report above for what the
brief deferred):**

- [x] Backend: floor-map endpoint (`GET /staff/tables` with
  session/order/call flags), table detail (`GET /staff/tables/:id`),
  active orders (`GET /staff/orders`), serve (`PATCH
  /staff/orders/:id/serve`), help calls (`GET /staff/help-calls`,
  acknowledge, resolve). Table status transitions, session close
  and staff assignments are NOT built — deferred (see report).
- [x] Frontend (staff): full-width board at `/staff` outside
  AppLayout (own `StaffLayout`), floor grid (simple grid in number
  order, status colours), table detail (session, orders, help
  calls), active-orders list (Ready to serve first), help-call
  queue (acknowledge → resolve), mark-served flow (ready only,
  two-step inline confirmation). NO login UI (brief § 6), NO "my
  tables" view.
- [x] Help-call flow end-to-end: guest request → staff list +
  floor flag → acknowledge → resolve → WS events at each step.
  Accept does NOT assign a waiter (no users system yet); the guest
  sees acknowledgement/resolution on refresh (guest WS tracking is
  Stage 8).

**Decisions needed:** floor-map coordinates — ANSWERED: simple
grid in v1 (tables 1–10 in number order), no `position_x/y`
seeding; assignment model — still open (does a waiter see only
assigned tables, or all tables with assigned ones highlighted?).

**Acceptance criteria (as verified 23 Nov 2026):**

- [x] A guest help call appears on the staff floor map and list
  within seconds (WS `help_call.opened` → REST re-fetch); the
  two-step acknowledge → resolve flow works, with a 409 for
  resolve-from-open. Accept does NOT assign a waiter (deferred —
  no users system); the guest sees the acknowledgement/resolution
  on refresh, not live (guest WS tracking is Stage 8).
- [x] Marking an order served before it is `ready` is rejected
  (`409`, state unchanged — tested for placed, preparing and
  served→served).
- [ ] The floor map correctly shows available / occupied / cleaning
  / out-of-service across a full visit (open → order → close →
  cleaning → available). NOT VERIFIED end-to-end: the floor shows
  available/occupied live, and cleaning/out-of-service badges
  render, but table-status transitions and session close are not
  built yet (Stage 6) — a full visit cannot be driven today.
- [x] A waiter cannot see or act on another restaurant's tables
  (negative test: cross-restaurant serve/table-detail → 404;
  two-restaurant isolation on tables/orders/calls).

**Exit gate:** golden-path steps 10–11 work live.

---

## Stage 6 — Billing and cash payments ✅ COMPLETE (23 Nov 2026)

**Goal (as built):** bills, splits, cash-payment requests, staff-confirmed
collection and receipts — with the cash-only rule enforced by the API, not
by the UI. Refunds, bill void, the daily cash report and owner
bills/payments history are DEFERRED (see decisions below): they need owner
auth, which does not exist yet. No `audit_logs` table: the payments table
itself is the audit trail (append-only rows, never updated/deleted).

**Built:**

- Backend: `bills` / `bill_items` / `bill_splits` / `payments` models +
  one Alembic migration (`a0f6368eca02`, revises `3a1b7c9d4e2f`);
  `idempotency_keys.table_session_id` made nullable + `bill_id` /
  `payment_id` columns for payment claims (partial UQ per bill scope).
  Bill numbers `B-YYYY-MM-DD-NNN`, receipt numbers `R-YYYY-MM-DD-NNN`,
  sequential per restaurant per day under the restaurant row lock.
  Bill calculation server-side only, integer paise, from order-item
  snapshots (never current menu price/name); client price fields → 422.
  Cash payment in ONE transaction reusing the Stage 3.2 claim pattern,
  scope `(restaurant_id, bill_id, endpoint, key)`; the bill row is locked
  BEFORE the claim insert (a claim INSERT takes a `FOR KEY SHARE` lock
  on the bill via the `bill_id` FK, which deadlocked against `FOR UPDATE`
  — found by the Postgres concurrency test, fixed by lock ordering).
- Frontend (guest): `BillPage` rewritten onto the live API (no sample-data
  fallback): itemised bill, split UI (equal/custom, server authoritative),
  Pay by Cash → `payment_requested`, receipt view after paid, help link.
  Refetches on focus + after actions + 10s interval while unpaid.
- Frontend (staff): Billing section in `StaffPage` — pending queue
  (`payment_requested` first), bill detail + payment history, two-step
  cash collection (amount prefilled with outstanding, validated ≤ due,
  confirm panel shows the exact amount), receipt after confirmation,
  open-bill summary in table detail. Demo mode `?demo=1` with separate
  sample bills. The queue drops paid bills itself (the list endpoint
  also returns paid bills for a future history view).

**Recorded scope decisions (brief governs over the blueprint):**

1. DEFERRED: refunds (need D3 owner authority), bill void (owner only),
   daily cash report, owner bills/payments history UI, `audit_logs` table.
2. SPLITS ARE IN: equal (parts ≥ 2, remainder 1p to earliest parts) and
   custom (amounts sum exactly to total, each > 0, else 422). No splits =
   one whole-bill target; with splits = paid split-by-split.
3. EVENT NAMES use the brief's list: `bill.created`, `bill.requested`,
   `payment.recorded`, `bill.paid`. The blueprint's `bill.payment_requested`
   / `payment.confirmed` are NOT used.
4. NO audit_logs table — the audit trail is the append-only `payments`
   table (timestamps, amounts, receipt numbers, idempotency keys).
5. `collected_by` is NULLABLE and always NULL (no users system; the brief
   forbids inventing staff identity). Same for any actor field.
6. OVERPAYMENT / CHANGE (D9): no change handling in v1. Amount must be
   > 0 and ≤ outstanding on its target, else 422. `change_due_paise` is
   always 0. D9 still open for change handling.
7. TAX (D1) UNDECIDED, not invented. Bills carry subtotal, tax = 0,
   service charge = 0, discount = 0, total = subtotal. Bill and receipt
   show tax as explicitly "not configured" (never "0% GST", never
   "tax-inclusive", never a GSTIN). Later: a restaurant-level tax config
   (rate, inclusive/exclusive, effective date) snapshotted onto the bill
   at issue time, with a migration. The 0 must not be read as a 0% rule.
8. BILL REGENERATION: while `issued`, lines are recomputed from the
   session's current non-cancelled orders on every fetch/request (one
   transaction); a partially-paid `issued` bill does NOT regenerate
   (money already collected); a no-change regen does NOT bump `version`.
   Once `payment_requested` the bill is FROZEN; a `paid` bill is frozen
   forever. New orders after a paid bill go on a NEW bill containing only
   items not referenced by any existing `bill_item` for the session. A
   guest cancellation after billing is reflected by the lazy regen.
9. GUEST LIVE UPDATES via REST refetch. Bill/payment WS events go to the
   STAFF channel only; the guest WS channel is Stage 8.

**Deviations found while building (recorded, not silent):**

- A guest token on `POST /staff/bills/:id/payments` returns **401**
  (missing staff key), not the 403 the brief stated: the Stage 4/5 dev
  gate returns 401 for a missing key; a kitchen key sent *as* the staff
  key → 403. Tested as such.
- Receipt-number uniqueness is per restaurant `(restaurant_id,
  receipt_number)`, not global — the `R-YYYY-MM-DD-NNN` sequence
  restarts per restaurant per day.
- `payment_requested_at` is an explicit column on `bills` (not derived
  from `updated_at`).
- The staff bills list returns paid bills too (filterable by `status`);
  the frontend queue drops them. The queue's "Issued" label previously
  mislabelled a paid bill — caught by the live screenshot, fixed by the
  queue-side filter + a test.
- Full-suite fixes (test infrastructure, no behaviour change): the
  slowapi limiter is disabled for the SQLite suite in `tests/conftest.py`
  (the whole suite shares one TestClient IP and the 60/min order cap was
  exhausted by legitimate test traffic — 429 cascade; no test asserts
  rate-limit behaviour); the billing isolation test deletes its second
  restaurant in a `finally` (same pattern as kitchen/staff); the kitchen
  list helper fetches `per_page=100` (the shared DB now holds >20 active
  orders, so a just-placed order fell off page 1).

**Decisions still open:** D1 (tax — undecided), D2 (discounts), D3 (refund
authority — now needed by Stage 7, not Stage 6), D9 (change handling;
split remainder decided: remainder 1p to earliest parts).

**Verification (23 Nov 2026):**

- Backend: `tests/test_billing.py` 36 passed (SQLite) +
  `tests/test_billing_concurrency.py` 3 passed (real Postgres, 8-way:
  same key → 1 payment; overpaying keys → total never exceeded; 8 × 1/8
  → all 201, bill paid). Full backend suite: **126 passed, 1 skipped**.
  Frontend: **84 passed** (was 70), `npm run build` clean.
- Migration verified on real PostgreSQL (pgserver): upgrade to head,
  downgrade −1 (billing tables gone, Zaika data preserved), re-upgrade.
- Live Postgres verification: 34/34 checks — seed Zaika, Table 5 order
  (Butter Chicken + 2× Butter Naan, ₹447.00) → kitchen ready → staff
  serve → guest bill (totals from snapshots) → menu price +₹100 change
  leaves the bill unchanged → Pay by Cash creates no payment row →
  staff lists pending bill → staff confirms cash (receipt
  `R-2026-11-23-001`, `collected_by` null, change 0) → WS
  `payment.recorded` + `bill.paid` received → second confirmation 409 →
  guest + staff receipts 200. Screenshots in
  `docs/stage6-screenshots/` (guest bill live, guest receipt live, staff
  queue, staff cash-confirmation, staff demo, guest bill mobile).

**Acceptance criteria:**

- [x] A guest tapping "Pay by Cash" does **not** mark anything paid
  (negative test: bill stays `payment_requested`, no payment row exists).
- [x] Staff confirmation creates exactly one payment, marks the bill/split
  paid, generates a receipt. (Audit row: deferred per decision 4 — the
  payments row IS the audit record.)
- [x] A second confirmation for the same split fails (`409`) and returns the
  existing payment.
- [x] Equal split of an odd-paise total sums exactly to the total; custom
  splits that do not sum are rejected (`422`).
- [x] A bill's total does not change when a menu price changes after the
  order (snapshot test).
- [ ] The daily cash report reconciles — DEFERRED (needs owner auth).
- [ ] A refund (owner only) adjusts payment + bill status and is audited —
  DEFERRED (needs D3 owner authority).

**Exit gate:** golden-path steps 12–18 work live — verified 23 Nov 2026
(see the live check above; the receipt the guest sees is the rendered
receipt view, and the cash handover itself is a human step at the table).

---

## Stage 7 — Owner / Admin dashboard ✅ COMPLETE (24 Nov 2026)

**Goal:** the owner can run the restaurant from the dashboard — live
operations, sales, cash, menu, staff, history, settings, tax, voids,
refunds, audit log — with password authentication for staff and owner.

**What was built (commit: see report below):**

- **Auth** (`backend/app/auth/`): `users`, `refresh_sessions`,
  `ws_tickets`, `tax_configurations`, `refunds`, `audit_logs` tables in
  one migration (`b7c2e9a1d4f5`, upgrade + downgrade + re-upgrade verified
  on real PostgreSQL with live Stage 6 data preserved). JWT access tokens
  (15 min, HS256) + rotating opaque refresh tokens (7 days, SHA-256 hash
  stored, reuse revokes the whole family), HttpOnly refresh cookie,
  `POST /api/v1/auth/login|refresh|logout|change-password`,
  `GET /api/v1/auth/me`, login rate-limited 10/min/IP, generic 401s,
  Argon2id passwords, bootstrap CLI `python -m app.auth.bootstrap`
  (getpass, refuses if an owner exists; no public owner registration).
  Permissions in `app/auth/permissions.py` with `require_permission()`;
  every request does a DB lookup, so deactivation is effective
  immediately everywhere.
- **Admin API** (`/api/v1/admin`): dashboard overview (branch-timezone
  presets, gross/cash/refunds/net/outstanding as separate fields),
  orders/bills/payments/refunds history, daily cash report (JSON + CSV),
  menu CRUD + reorder + price-change audit, users CRUD + deactivate +
  reset-password (last-active-owner guard), restaurant/branch/table
  settings, tax config + history, bill void, cash refunds (idempotent),
  read-only audit logs. All `{"data": …}` wrapped.
- **Dev-key retirement:** production requires JWT everywhere (staff,
  kitchen, admin, WS tickets) and returns 401 for dev keys; dev keeps
  both. Frontend sends dev keys only under `import.meta.env.DEV`.
- **WS tickets:** `POST /api/v1/ws/tickets` issues a server-side,
  hashed, single-use ticket (60 s); `/ws/kitchen` and `/ws/staff`
  accept `?ticket=` (or `?dev_key=` in dev). Bad ticket → 4401,
  wrong role / production dev key → 4403, restaurant mismatch → 4404.
  The socket is accepted then closed with the code (uvicorn turns
  close-before-accept into a bare HTTP 403, so the codes never reached
  real clients before this change).
- **Tax (D1 decided — see below):** owner-only config, explicit rate
  (0–10000 bps, inclusive/exclusive, label, effective_from now-or-future,
  no two configs share an effective_from, active = greatest
  effective_from ≤ now). Unconfigured is a distinct state from 0%.
  Bills recompute with the current config while `issued`; at the freeze
  point (transition to `payment_requested`, and also at payment time if
  paid without a prior request) the tax policy + computed tax are
  snapshotted onto the bill and never change afterwards.
- **Void + refunds:** void only from `issued`/`payment_requested`
  with zero confirmed payments (else 409 "refund required first");
  repeat void → 409; voided items become billable again. Refunds
  require `Idempotency-Key`, one transaction under bill+payment row
  lock, amount ≤ payment − prior refunds, original payment never
  deleted/overwritten, bill/payment status recomputed.
- **Frontend:** `/login` (in-memory token only, role redirect,
  must-change-password flow), `/admin` with AdminLayout (Overview,
  Orders, Menu, Staff, Tables, Bills, Cash reports, Refunds, Audit log,
  Settings), kitchen/staff pages use JWT tickets in production builds.
  Screenshots: `docs/stage7-screenshots/` (9, real data).
- **Tests:** backend 164 passed / 1 skipped (was 126; +36 admin +2
  Postgres concurrency); frontend 100 passed / 12 files; live
  verification 37/37 (bootstrap → login → role gates → order → bill →
  cash confirm as waiter → void → partial refund → cash report
  reconciles → audit log → WS ticket + reuse 4401) and migration
  verification 14/14. Scripts: `backend/scripts/verify_stage7_live.py`,
  `verify_stage7_migration.py`, `screenshots_stage7.py`.

**Decisions recorded (brief governs over this roadmap):**

1. **Deferred out of Stage 7** (roadmap listed them here; the brief
   does not assign them): QR token rotation, review moderation (D6),
   menu image upload, staff assignments, busiest-hours / by-category
   analytics depth. Analytics in this stage = the brief §4 dashboard
   metrics only.
2. **Manager "Configurable" permissions** are explicit boolean flags
   on the user record (`can_manage_staff`, `can_manage_settings`,
   `can_refund`, `can_void`), default False, toggleable by the owner.
   Owner always has all permissions. Manager cannot configure tax.
   Waiter `view_kitchen` = No in v1 (brief said "Optional").
3. **Bill freeze point** for the tax snapshot is the Stage 6 freeze
   point: the transition to `payment_requested` (also at payment time
   when a bill is paid without a prior request).
4. **WS tickets are stored server-side** (DB table, hashed, single-use,
   ~60s expiry) — NOT signed JWTs in the URL, because single-use
   requires server state.
5. **Production dev-key behaviour changed** from the Stage 4/5 spec
   (503 `*_auth_not_configured`) to 401 `unauthenticated`, per the
   Stage 7 brief §B; three existing tests updated accordingly.
6. **A voided bill shows outstanding ₹0** — it is written off; its
   total no longer counts as owed (fixed in `billing_service._bill_out`
   and the admin bills list; regression test added).

**Acceptance criteria:**

- [x] The overview matches the live state (verified live: gross ₹725.00
  = paid ₹387.00 + re-billed ₹338.00; cash ₹387.00; refunds ₹10.00;
  net ₹377.00; outstanding ₹338.00 — reconciled by hand).
- [x] Toggling an item unavailable removes it from orderable items for
  new guest orders and does not affect orders already placed.
- [x] A menu price change is audited (`menu.price_changed`) and does
  not change any existing bill.
- [ ] Rotating a table's QR token immediately invalidates the old token
  — **DEFERRED** (see decision 1).
- [x] Dashboard / cash-report numbers reconcile with the underlying
  orders/bills (spot-checked live for one day; see report above).

**Exit gate:** the owner can run a full service (menu change, staff
check, cash check) from the dashboard without touching the database.
✅ — verified live against real PostgreSQL.

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
| D1 | Tax: GST-inclusive vs exclusive, service charge, rounding | DECIDED in Stage 7 (24 Nov 2026): the owner configures a tax rate (0–10000 bps, inclusive or exclusive, label, effective_from now-or-future; active = greatest effective_from ≤ now). No config = "unconfigured" (distinct from an explicit 0% rate). Bills recompute with the current config while `issued`; at the freeze point (→ `payment_requested`, or at payment time) the tax policy + computed tax are snapshotted onto the bill and never change. Exclusive: total = subtotal + tax. Inclusive: total = subtotal, tax_paise = the tax component inside it. Service charge and discounts remain 0 / not configured. Tavessa claims no tax compliance — the UI says so. |
| D2 | Discounts / complimentary items | Stage 6 |
| D3 | Refund authority + limits | DECIDED in Stage 7 (24 Nov 2026): refunds are owner-only, or manager with `can_refund`. Cash refunds only, against a confirmed payment, amount ≤ payment − prior refunds, `Idempotency-Key` required, one transaction under bill+payment row lock. Bill void is owner-only, or manager with `can_void`, only for unpaid bills (any confirmed payment → 409 "refund required first"). |
| D4 | QR printing (format, size, who prints) | Stage 2 |
| D5 | Table session expiry + close rules | Stage 2 |
| D6 | Review moderation (immediate vs held, editable?) | Stage 2 / 7 |
| D7 | KDS hardware + offline fallback | Stage 4 |
| D8 | Order cancellation window | Stage 3 |
| D9 | Split remainder + overpayment/change | Split remainder DECIDED in Stage 6 (1p to earliest parts); change handling STILL OPEN (no change in v1) |
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
