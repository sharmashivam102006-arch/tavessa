# Tavessa — Restaurant Management Platform

> **"Every Table. Every Order. In Sync."**

Tavessa is a production-quality, full-stack restaurant management platform that
connects four interfaces into one central system:

1. **Guest** — QR-code table ordering, live order tracking, bill & cash payment request
2. **Waiter / Staff** — floor map, orders, help calls, bill requests, cash collection
3. **Kitchen Display System (KDS)** — real-time tickets by station, item-level status, timers
4. **Owner / Admin** — live occupancy, sales, cash, menu, staff, orders, analytics

The first demonstration restaurant is **Zaika**. The architecture is SaaS-ready:
every tenant-scoped record belongs to a restaurant, so a second restaurant can be
added without a schema redesign.

> **Status: Stage 5 complete (Waiter / Staff dashboard) — 23 Nov 2026.** Blueprint + project foundation
> are done and verified (see "Development setup" below). Stage 2 built the
> guest interface & digital menu for Zaika as a frontend against the API
> contract in `docs/api-plan.md`, using clearly-marked sample menu data.
> Stage 3 wired the guest backend behind it: PostgreSQL models + migration,
> Zaika seed (6 categories, 24 items), QR-token sessions, menu, orders
> (server-priced, idempotent), help calls — all verified live against a
> real PostgreSQL. The sample menu is now used only for the `/t/demo`
> preview; real table tokens use the real API. Stage 4 added the Kitchen
> Display System: order-level kitchen API, `order.created` /
> `order.status_changed` WebSocket events, and the `/kitchen` board
> (New / Preparing / Ready, ticking elapsed times). Stage 5 added the
> Waiter / Staff dashboard: floor grid, active orders, serving
> (ready → served), help calls (acknowledge → resolve), and the
> `/ws/staff` channel — all dev-key gated, no production login yet.
> The owner/admin dashboard is still a route shell, and there are
> no bills, payments, reviews, or staff login yet (Stages 6–8).
> Do not treat any endpoint, screen, or workflow in these docs as
> shipped until its stage in `docs/development-roadmap.md` is
> marked complete and verified.

## Development setup

Prerequisites: Python 3.12+, Node 24+, and PostgreSQL 16 (via Docker Compose
or a local install). Docker is optional — see "Database" below.

```bash
git clone <repo> tavessa && cd tavessa
cp .env.example .env          # placeholders only; set SECRET_KEY for real use
```

### Database

```bash
docker compose up -d db       # Postgres 16 on localhost:5432 (recommended)
# — or, without Docker, with a local PostgreSQL install: —
createdb tavessa              # then keep DATABASE_URL in .env as the default
```

### Backend

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
alembic upgrade head          # creates the Stage 3 guest tables
python -m app.seed.zaika      # idempotent Zaika seed: 1 restaurant, 10 tables,
                              # 6 categories, 24 items (safe to re-run)
python -m app.seed.demo_tokens --table 5   # dev-only demo QR token (refuses
                              # in production); prints http://localhost:5173/t/<token>
                              # ONCE — the raw token is stored only as a hash.
                              # Demo tokens expire in 30 days (provisional, D5).
uvicorn app.main:app --reload --port 8000
# Health: curl http://localhost:8000/api/v1/health
# Docs:   http://localhost:8000/docs (dev only)
```

#### Trying the guest endpoints (dev)

Generate a demo token first (see above — never commit a token), then:

```bash
TOKEN=<token printed by demo_tokens>   # e.g. for Table 5
# Open the session (creates/resumes it; returns the restaurant + table)
curl http://localhost:8000/api/v1/guest/t/$TOKEN
# {"session":{"id":"…","restaurantId":"…","tableId":"…","tableNumber":5,
#   "status":"active","openedAt":"…"},
#  "restaurant":{"id":"…","name":"Zaika","tagline":"…","cuisine":"…",
#   "address":"12, MG Road, Bengaluru","openingHours":"…",
#   "phone":"+91 98765 43210","rating":4.6},
#  "table":{"number":5},"menuUrl":"/t/<token>"}

# Menu (needs the token header)
curl -H "X-Table-Token: $TOKEN" http://localhost:8000/api/v1/guest/menu
# {"categories":[{"id":"…","name":"Starters","sortOrder":1,"isActive":true}, …],
#  "items":[{"id":"…","categoryId":"…","name":"Old Delhi Butter Chicken",
#   "pricePaise":34900,"isVeg":false,"isAvailable":true, …}]}

# Place an order — prices are recomputed server-side; never send prices.
# ITEM_ID is the menu item's UUID from the menu response.
curl -X POST http://localhost:8000/api/v1/orders \
  -H "X-Table-Token: $TOKEN" -H "Idempotency-Key: <uuid>" \
  -H "Content-Type: application/json" \
  -d '{"items":[{"menuItemId":"<ITEM_ID>","quantity":1}]}'
# 201 {"order":{"id":"…","orderNumber":"ZAIKA-0001","status":"placed",
#   "items":[…],"subtotalPaise":34900,"taxPaise":0,"totalPaise":34900, …}}
# Repeating the same request with the same Idempotency-Key returns the
# same order with header `Idempotency-Replayed: true` — no duplicate.

# Help call (one open call per session; second → 409)
curl -X POST http://localhost:8000/api/v1/guest/help-calls \
  -H "X-Table-Token: $TOKEN" -H "Content-Type: application/json" \
  -d '{"type":"water"}'
# 201 {"data":{"id":"…","type":"water","status":"open", …}}
```

Unknown token → `401 {"error":{"code":"invalid_table_token",…}}`;
revoked/expired token → `410 {"error":{"code":"session_expired",…}}`;
another session's order → `404` (not `403`).
Tests: `pytest -q` in `backend/` (SQLite in-memory; 87 passed / 1 skipped
after Stage 5) — plus a real-Postgres check via `alembic upgrade head`
against a local PostgreSQL before claiming the migration works.

### Waiter / Staff dashboard (Stage 5 — development access only)

> **The staff board is development-access only until staff auth lands.**
> Staff endpoints require a shared dev key, not a staff login. There is
> NO `/login` implementation, no users table, no JWT issuance in Stage 5.
> In production they return `503 {"error":{"code":
> "staff_auth_not_configured",…}}` and the WebSocket closes with
> code 4403. Do not expose the staff board publicly on the dev key.
> A guest table token does NOT grant staff access; the kitchen dev
> key does NOT grant staff access (and vice versa).

```bash
# .env (backend) and frontend/.env must carry the SAME key:
STAFF_DEV_KEY=dev-staff-only          # backend
VITE_STAFF_DEV_KEY=dev-staff-only     # frontend
```

```bash
# Floor grid (tables, number order, with live session/order/help-call flags)
curl -H "X-Staff-Dev-Key: dev-staff-only" \
  http://localhost:8000/api/v1/staff/tables
# Active orders, oldest first (ready orders are shown first in the UI)
curl -H "X-Staff-Dev-Key: dev-staff-only" \
  http://localhost:8000/api/v1/staff/orders
# Serve a ready order — ready -> served ONLY, anything else -> 409.
# Serving does NOT close the session or change the table status.
curl -X PATCH -H "X-Staff-Dev-Key: dev-staff-only" \
  http://localhost:8000/api/v1/staff/orders/<ORDER_ID>/serve
# Help calls: list unresolved, acknowledge (open -> acknowledged),
# resolve (acknowledged -> resolved). Resolving an open call -> 409.
curl -H "X-Staff-Dev-Key: dev-staff-only" \
  http://localhost:8000/api/v1/staff/help-calls
curl -X PATCH -H "X-Staff-Dev-Key: dev-staff-only" \
  http://localhost:8000/api/v1/staff/help-calls/<CALL_ID>/acknowledge
curl -X PATCH -H "X-Staff-Dev-Key: dev-staff-only" \
  http://localhost:8000/api/v1/staff/help-calls/<CALL_ID>/resolve
# restaurant_id is required unless exactly one restaurant exists.
```

Open the board at http://localhost:5173/staff (Vite proxies `/api`
and `/ws` to the backend). The board shows the floor grid (status
colours, READY / NEEDS HELP badges), table detail on click, active
orders with "Ready to serve" first, and the help-call queue. A new
guest order or help call appears without refresh via the WebSocket
(`order.created` / `order.status_changed` / `help_call.opened` /
`help_call.acknowledged` / `help_call.resolved`; REST re-fetch is
the source of truth, the socket only announces change). Serving
asks for inline confirmation first; the order moves only after the
server confirms. If the socket drops, the board keeps the last REST
snapshot with a reconnecting banner. Demo mode (clearly labelled
sample floor/orders/calls, actions disabled, no backend needed):
http://localhost:5173/staff?demo=1. Screenshots:
`docs/stage5-screenshots/`. NOT in Stage 5: bill requests, session
close, table-status transitions, staff assignments, payroll —
see `docs/development-roadmap.md` Stage 5 report.

### Kitchen Display System (Stage 4 — development access only)

> **The KDS is development-access only until staff auth lands.**
> Kitchen endpoints require a shared dev key, not a staff login.
> In production they return `503 {"error":{"code":
> "kitchen_auth_not_configured",…}}` and the WebSocket closes with
> code 4403. Do not expose the KDS publicly on the dev key.

```bash
# .env (backend) and frontend/.env must carry the SAME key:
KITCHEN_DEV_KEY=dev-kitchen-only          # backend
VITE_KITCHEN_DEV_KEY=dev-kitchen-only     # frontend
```

```bash
# Kitchen queue (active orders, oldest first; status=queued|preparing|ready)
curl -H "X-Kitchen-Dev-Key: dev-kitchen-only" \
  http://localhost:8000/api/v1/kitchen/orders
# Move an order one step: placed/accepted -> preparing -> ready
curl -X PATCH -H "X-Kitchen-Dev-Key: dev-kitchen-only" \
  -H "Content-Type: application/json" -d '{"status":"preparing"}' \
  http://localhost:8000/api/v1/kitchen/orders/<ORDER_ID>/status
# Any other transition -> 409; any other status value -> 422.
# restaurant_id is required unless exactly one restaurant exists.
```

Open the board at http://localhost:5173/kitchen (Vite proxies `/api`
and `/ws` to the backend — the `/ws` entry needs `ws: true` or the
socket never connects). The board shows New / Preparing / Ready
columns with ticking elapsed times; a new guest order appears
without refresh via the WebSocket (`order.created` /
`order.status_changed` events; REST re-fetch is the source of
truth, the socket only announces change). If the socket drops, the
board keeps the last REST snapshot with a reconnecting banner.
Demo mode (clearly labelled sample tickets, no backend needed):
http://localhost:5173/kitchen?demo=1. Screenshots:
`docs/stage4-screenshots/`.

### Frontend

```bash
cd frontend
npm install
npm run dev                   # http://localhost:5173 — /api proxied to :8000
npm run build                 # tsc + vite build
```

The home page shows the Tavessa brand and a backend connection status
(green = backend + database ok, amber = degraded/unreachable). No business
features exist yet.

## Cash only — v1 rule

The initial version supports **cash payments only**. There is no UPI, Razorpay,
Stripe, Paytm, payment gateway, or online payment processing in v1, and none
should be added without an explicit decision to change this rule.

A bill is **never** marked paid because a guest clicked a button. Payment is
recorded only when authorised staff confirm they have actually collected the
cash. See `docs/project-overview.md` § Billing and `docs/api-plan.md` § Billing.

## Stack (agreed — do not change silently)

| Layer | Choice |
|---|---|
| Frontend | React + TypeScript + Vite, Tailwind CSS, shadcn/ui, React Router, Zustand, TanStack Query, Lucide |
| Backend | Python, FastAPI, REST + native FastAPI WebSockets, Pydantic, SQLAlchemy 2.0 |
| Database | PostgreSQL, Alembic migrations |
| Realtime / cache | Redis **only when needed** (shared broadcast / cache / coordination) — see `docs/architecture.md` § 6 |
| Tooling | Docker + Docker Compose, Pytest, Playwright, Git + GitHub |

No microservices. No Kubernetes. No extra frameworks without a recorded
technical incompatibility and an explicit decision.

## Documentation

| Document | What it answers |
|---|---|
| `docs/project-overview.md` | What Tavessa is, who uses it, the end-to-end workflow, billing rules, non-goals |
| `docs/architecture.md` | Modular monolith, folder structure, frontend architecture, auth, realtime, security, risks |
| `docs/database-design.md` | Every table, columns, keys, constraints, indexes, statuses, tenant isolation, price snapshots |
| `docs/api-plan.md` | REST endpoints, WebSocket channels and events, idempotency, errors, pagination |
| `docs/development-roadmap.md` | Ordered 10-stage plan, per-stage acceptance criteria, testing and deployment checklists |

Read them in that order. `docs/architecture.md` § 11 is the single source of
truth for the folder structure; do not invent a different layout when Stage 1
starts.

## Proposed repository layout

```
tavessa/
├── README.md
├── docs/                          # this blueprint
│   ├── project-overview.md
│   ├── architecture.md
│   ├── database-design.md
│   ├── api-plan.md
│   └── development-roadmap.md
├── frontend/                      # Stage 1 — React + Vite app (all 4 interfaces)
│   └── src/
│       ├── app/                   # router, providers, layouts
│       ├── features/guest/        # /t/:tableToken
│       ├── features/staff/        # /staff
│       ├── features/kitchen/      # /kitchen
│       ├── features/admin/        # /admin
│       └── shared/                # api client, types, ui, utils
├── backend/                       # Stage 1 — FastAPI modular monolith
│   └── app/
│       ├── api/v1/                # route modules, one per domain
│       ├── core/                  # config, security, deps, errors
│       ├── models/                # SQLAlchemy 2.0 models
│       ├── schemas/               # Pydantic schemas
│       ├── services/              # business logic
│       ├── repositories/          # data access where useful
│       ├── realtime/              # WebSocket manager + event bus
│       └── db/                    # session, base, migrations (Alembic)
├── docker-compose.yml             # Stage 1 — api + web + postgres (+ redis later)
├── Dockerfile.backend
├── Dockerfile.frontend
└── .env.example                   # Stage 1 — every secret as an env var, no real values
```

Full annotated layout: `docs/architecture.md` § 11.

## How to start (when Stage 1 is approved)

1. Review this blueprint and record decisions on the open items in
   `docs/development-roadmap.md` § 8 (tax treatment, QR printing, KDS hardware,
   refund approver, session expiry, review moderation).
2. Approve Stage 1. Only then scaffold `frontend/` and `backend/` exactly as
   specified in `docs/architecture.md` § 11.
3. Work stage by stage. Do not skip stages or build all four interfaces at once.
   Each stage ends only when its acceptance criteria in
   `docs/development-roadmap.md` pass.

## Core reliability rules (non-negotiable)

- Save important changes to PostgreSQL **before** broadcasting any event.
  WebSocket messages are notifications, never the source of truth.
  Reconnecting clients always re-fetch latest state via REST.
- Never trust amounts from the frontend. Prices, taxes, discounts and totals
  are re-computed and validated on the backend from menu data and price
  snapshots stored in `order_items`.
- Prevent duplicate order submissions with an `Idempotency-Key` header and a
  stored idempotency record (see `docs/api-plan.md` § 7).
- Use database transactions for order placement, bill generation, payment
  confirmation and refunds. Validate restaurant and table access server-side
  on every request.
- Money is stored as integer paise (`BIGINT`), never as a float. All money
  arithmetic uses integers / `Decimal` on the backend.

## Demo data — Zaika

Zaika is the seed restaurant for local development and demonstration only.
Seed data (menu, tables, stations, one owner + staff accounts) is defined in
Stage 1 and must be clearly marked as demo data, with no real credentials.
Production onboarding of a second restaurant is a data operation, not a code
change — see `docs/database-design.md` § 9.

## Contributing / decisions

- Any change to the stack, the cash-only rule, the status machines, or the
  tenant-isolation strategy must be recorded as a decision in the relevant doc
  before it is implemented.
- Do not add tables, endpoints, or dependencies "to make the schema look
  large" or "just in case". Every addition needs a workflow that uses it.
