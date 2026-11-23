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

> **Status: Stage 2 complete (frontend) — 23 Nov 2026.** Blueprint + project foundation
> are done and verified (see "Development setup" below). Stage 2 built the
> guest interface & digital menu for Zaika as a frontend against the API
> contract in `docs/api-plan.md`, using clearly-marked sample menu data —
> no guest/menu/order/help/bill endpoints exist on the backend yet, so every
> write action honestly reports "not yet connected" and no order, help
> request or payment is faked. The backend half of Stage 2 (tables, menu,
> orders) is deferred to Stage 3, per the Stage 2 instruction. No business
> features exist yet beyond the guest UI — staff/kitchen/admin are route
> shells only. Do not treat any endpoint, screen, or workflow in these docs
> as shipped until its stage in `docs/development-roadmap.md` is marked
> complete and verified.

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
alembic upgrade head          # no business migrations yet in Stage 1
uvicorn app.main:app --reload --port 8000
# Health: curl http://localhost:8000/api/v1/health
# Docs:   http://localhost:8000/docs (dev only)
```

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
