<p align="center">
  <h1 align="center">🍽️ Tavessa</h1>
  <p align="center">
    <strong>A full-stack restaurant management platform — QR ordering, kitchen display, staff dashboard, billing & admin panel.</strong>
  </p>
  <p align="center">
    <a href="#features">Features</a> •
    <a href="#tech-stack">Tech Stack</a> •
    <a href="#architecture">Architecture</a> •
    <a href="#quick-start">Quick Start</a> •
    <a href="#api-reference">API Reference</a> •
    <a href="#project-structure">Project Structure</a> •
    <a href="#contributing">Contributing</a>
  </p>
</p>

---

## Overview

**Tavessa** is an end-to-end, real-time restaurant management system built with **FastAPI** and **React**. Guests scan a QR code at their table, browse the menu, place orders, track progress live, request help, split bills and pay — all from their phone, no app install needed. Meanwhile, the kitchen sees orders on a live display board, waiters manage floor operations, and the owner has a full admin dashboard with financials, audit logs and staff management.

> **Cash-only in v1.** Online payments, delivery, and multi-tenancy are planned but not yet implemented.

---

## Features

### 🧑‍🍳 Guest Experience (QR → Order → Pay)
- **QR Code Table Sessions** — Scan to open a session; no login required
- **Digital Menu** — Searchable, filterable menu with categories, variants, add-ons, veg/non-veg tags and images
- **Cart & Ordering** — Add items with variants/add-ons/special instructions; server-side pricing (no price manipulation)
- **Live Order Tracking** — Real-time WebSocket updates: Placed → Accepted → Cooking → Ready → Served
- **Help Calls** — Request a waiter, water, plates, or order help with one tap
- **Bill & Receipt** — View itemized bill, split equally or custom, request cash payment, view receipt
- **Real-time Notifications** — Live updates for order status, help call acknowledgements, bill changes

### 🖥️ Kitchen Display System (KDS)
- **Three-column board** — New / Preparing / Ready
- **One-tap transitions** — Move orders through the kitchen workflow
- **Elapsed timers** — Server-computed time since placement
- **WebSocket-driven** — Instant updates, no polling

### 👨‍🍳 Staff / Waiter Dashboard
- **Floor Grid** — Live table status (available/occupied/cleaning) with order & help indicators
- **Order Management** — View all active orders, mark as served
- **Help Call Queue** — Acknowledge and resolve guest requests
- **Billing Queue** — See payment-requested bills, confirm cash collection
- **Bill Detail** — Full bill breakdown, split view, record payments with idempotent receipts

### 🏢 Admin / Owner Panel
- **Dashboard Overview** — Gross sales, cash collected, refunds, net cash, best sellers, recent activity
- **Order History** — Paginated, filterable order list with status breakdown
- **Bill Management** — View all bills, void bills with reason, process refunds
- **Menu Management** — CRUD categories, items, variants, add-ons; toggle availability
- **Staff Management** — Create/edit users (owner, manager, waiter, kitchen), set permissions
- **Table Management** — View/update table statuses
- **Tax Configuration** — Set tax rate, mode (inclusive/exclusive), label; snapshots on freeze
- **Cash Reports** — Daily cash reconciliation: collected, refunds, net, outstanding
- **Audit Logs** — Full audit trail of logins, password changes, voids, refunds, settings changes
- **Restaurant Settings** — Edit name, tagline, cuisine, address, contact info

### 🔐 Security & Auth
- **JWT Authentication** — Access + refresh token rotation with reuse detection
- **Role-Based Access Control** — Owner / Manager / Waiter / Kitchen with configurable permissions
- **Argon2id Password Hashing** — Industry-standard password storage
- **Rate Limiting** — Session opening and order placement throttled
- **Idempotent Order Placement** — Single-transaction crash-safe design with PostgreSQL speculative insertion

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| **Backend** | Python 3.12 · FastAPI · SQLAlchemy 2.0 · Alembic · Pydantic v2 |
| **Frontend** | React 19 · TypeScript 7 · Vite 8 · TailwindCSS 4 · React Router 8 |
| **State** | Zustand (client) · TanStack React Query (server state) |
| **Database** | PostgreSQL 16 (production) · SQLite (tests) |
| **Real-time** | WebSockets (FastAPI native) with single-use ticket authentication |
| **Auth** | JWT (python-jose) · Argon2id (argon2-cffi) · Refresh token rotation |
| **UI Components** | Radix UI · Lucide icons · shadcn/ui patterns |
| **Testing** | pytest + httpx (backend) · Vitest + Testing Library (frontend) |
| **CI/CD** | GitHub Actions · Docker Compose |
| **Linting** | oxlint (frontend) |

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                        Client Devices                               │
│  ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌────────────────┐  │
│  │  Guest   │   │ Kitchen  │   │  Staff   │   │   Admin/Owner  │  │
│  │ (Phone)  │   │ (Screen) │   │ (Tablet) │   │   (Desktop)    │  │
│  └────┬─────┘   └────┬─────┘   └────┬─────┘   └───────┬────────┘  │
│       │              │              │                  │            │
│       └──────────────┴──────────┬───┴──────────────────┘            │
│                            React SPA                                │
│                     (Vite · React Router)                           │
└─────────────────────────────┬───────────────────────────────────────┘
                              │ REST + WebSocket
┌─────────────────────────────┴───────────────────────────────────────┐
│                        FastAPI Backend                              │
│  ┌────────────────────────────────────────────────────────────────┐ │
│  │  API Layer (/api/v1)                                          │ │
│  │  guest · kitchen · staff · admin · orders · health · auth     │ │
│  ├────────────────────────────────────────────────────────────────┤ │
│  │  Service Layer                                                │ │
│  │  table · order · kitchen · staff · billing · help · admin     │ │
│  │  audit · tax                                                  │ │
│  ├────────────────────────────────────────────────────────────────┤ │
│  │  Real-time                        Auth                        │ │
│  │  ConnectionManager                JWT · RBAC · Refresh tokens │ │
│  │  /ws/kitchen · /ws/staff          Argon2id · Ticket auth      │ │
│  │  /ws/guest                                                    │ │
│  ├────────────────────────────────────────────────────────────────┤ │
│  │  Data Layer (SQLAlchemy 2.0 ORM · Alembic migrations)         │ │
│  │  Restaurant · Branch · DiningTable · TableSession             │ │
│  │  MenuCategory · MenuItem · ItemVariant · ItemAddon            │ │
│  │  Order · OrderItem · OrderItemAddon                           │ │
│  │  Bill · BillItem · BillSplit · Payment                        │ │
│  │  User · RefreshSession · WsTicket · AuditLog · TaxConfig      │ │
│  │  IdempotencyKey · HelpCall                                    │ │
│  └────────────────────────────────────────────────────────────────┘ │
└─────────────────────────────┬───────────────────────────────────────┘
                              │
                    ┌─────────┴─────────┐
                    │  PostgreSQL 16    │
                    │  (Docker or local)│
                    └───────────────────┘
```

### Key Design Decisions

- **Server-side pricing** — The backend recomputes every price from the menu at order time. No price or total from the client is ever trusted.
- **Integer paise** — All money is stored and transmitted as integer paise (1/100 of a rupee). No floating point.
- **Idempotent order placement** — Single-transaction crash-safe design using PostgreSQL's speculative insertion on a unique index. Claim + order + stored response are created atomically.
- **Broadcast-after-commit** — WebSocket events are published only after the database transaction commits successfully. Services are broadcast-free; routes broadcast.
- **No existence leakage** — Cross-tenant queries return 404, never 403.

---

## Quick Start

### Prerequisites

- **Python** 3.12+
- **Node.js** 24+
- **PostgreSQL** 16+ (or use Docker)

### 1. Clone & configure

```bash
git clone https://github.com/<your-username>/tavessa.git
cd tavessa
cp .env.example .env
```

### 2. Start PostgreSQL

**Option A — Docker (recommended):**

```bash
docker compose up -d db
```

**Option B — Local PostgreSQL:**

Create a database named `tavessa` with user `tavessa` / password `tavessa`, or update `DATABASE_URL` in `.env`.

### 3. Backend

```bash
cd backend
pip install -r requirements.txt

# Run migrations & seed demo data
alembic upgrade head
python -m app.seed          # seeds the "Zaika" demo restaurant

# Start the API server
uvicorn app.main:app --reload --port 8000
```

The API is now live at **http://localhost:8000** with interactive docs at [/docs](http://localhost:8000/docs).

### 4. Frontend

```bash
cd frontend
npm install
npm run dev
```

The app is now live at **http://localhost:5173**.

### 5. Try it out

| View | URL | Notes |
|------|-----|-------|
| **Home** | http://localhost:5173 | Landing page with role links |
| **Guest Menu** | http://localhost:5173/t/demo | Demo table session (Table 5, Zaika) |
| **Kitchen** | http://localhost:5173/kitchen | Dev key: `dev-kitchen-only` |
| **Staff** | http://localhost:5173/staff | Dev key: `dev-staff-only` |
| **Admin** | http://localhost:5173/admin | Login with seeded owner credentials |
| **Login** | http://localhost:5173/login | Staff/kitchen/owner sign-in |

### Docker Compose (full stack)

```bash
docker compose up --build
```

This starts PostgreSQL, the API server (port 8000), and the frontend served by Nginx (port 5173).

---

## API Reference

Base URL: `http://localhost:8000/api/v1`

### Guest Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/guest/t/{table_token}` | Open / resume a table session |
| `GET` | `/guest/session` | Current session info |
| `GET` | `/guest/menu` | Browse menu (filter by category, search) |
| `POST` | `/orders` | Place an order (idempotent) |
| `GET` | `/guest/orders` | List session orders |
| `GET` | `/guest/orders/{id}` | Order detail with tracking |
| `DELETE` | `/orders/{id}` | Cancel order (placed only) |
| `POST` | `/guest/help-calls` | Request assistance |
| `GET` | `/guest/help-calls` | List help calls |
| `POST` | `/guest/bill` | Create / retrieve bill |
| `GET` | `/guest/bill` | Get current bill |
| `PATCH` | `/guest/bill/request-payment` | Request cash payment |
| `POST` | `/guest/bill/splits` | Create equal / custom splits |
| `GET` | `/guest/bill/receipt` | Get receipt (after payment) |

### Kitchen Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/kitchen/restaurants` | List restaurants |
| `GET` | `/kitchen/orders` | Active order queue |
| `GET` | `/kitchen/orders/{id}` | Order detail |
| `PATCH` | `/kitchen/orders/{id}` | Transition order status |

### Staff Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/staff/restaurants` | List restaurants |
| `GET` | `/staff/tables` | Floor grid with live status |
| `GET` | `/staff/tables/{id}` | Table detail + orders + help calls |
| `GET` | `/staff/orders` | Active orders |
| `PATCH` | `/staff/orders/{id}/serve` | Mark order as served |
| `GET` | `/staff/help-calls` | Open help calls |
| `PATCH` | `/staff/help-calls/{id}` | Acknowledge / resolve help call |
| `GET` | `/staff/bills` | Billing queue |
| `GET` | `/staff/bills/{id}` | Bill detail |
| `POST` | `/staff/bills/{id}/payments` | Confirm cash payment (idempotent) |
| `GET` | `/staff/bills/{id}/receipt` | Get receipt |

### Admin Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/admin/overview` | Dashboard KPIs |
| `GET` | `/admin/orders` | Paginated order history |
| `GET` | `/admin/bills` | All bills with financials |
| `POST` | `/admin/bills/{id}/void` | Void a bill |
| `POST` | `/admin/bills/{id}/refund` | Process refund |
| `GET` | `/admin/cash` | Daily cash report |
| `GET/POST/PATCH` | `/admin/menu/*` | Menu CRUD |
| `GET/POST/PATCH` | `/admin/staff/*` | Staff management |
| `GET/POST` | `/admin/settings/tax` | Tax configuration |
| `GET/PATCH` | `/admin/settings` | Restaurant settings |
| `GET` | `/admin/audit-logs` | Audit log |

### Auth Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/auth/login` | Login (returns access + refresh tokens) |
| `POST` | `/auth/refresh` | Rotate refresh token |
| `POST` | `/auth/logout` | Revoke refresh token |
| `POST` | `/auth/change-password` | Change password |
| `GET` | `/auth/me` | Current user info |
| `POST` | `/ws/tickets` | Issue WebSocket ticket (staff/kitchen) |

### WebSocket Channels

| Path | Auth | Description |
|------|------|-------------|
| `/ws/kitchen` | Ticket or dev key | Kitchen order events |
| `/ws/staff` | Ticket or dev key | Staff events (orders, help calls, bills) |
| `/ws/guest` | Ticket only | Guest session events (order status, help, billing) |

Full interactive docs available at [http://localhost:8000/docs](http://localhost:8000/docs) (development only).

---

## Project Structure

```
tavessa/
├── backend/
│   ├── alembic/                    # Database migrations
│   │   └── versions/               # Migration scripts (Stages 3–8)
│   ├── app/
│   │   ├── api/v1/                 # Route handlers
│   │   │   ├── admin.py            # Owner/admin endpoints
│   │   │   ├── guest.py            # Guest endpoints (QR session, menu, orders, billing)
│   │   │   ├── kitchen.py          # KDS endpoints
│   │   │   ├── orders.py           # Order placement & cancellation
│   │   │   ├── staff.py            # Waiter/staff endpoints
│   │   │   ├── health.py           # Health check
│   │   │   └── websocket.py        # WebSocket handlers
│   │   ├── auth/                   # Authentication & authorization
│   │   │   ├── bootstrap.py        # Seed default owner on startup
│   │   │   ├── deps.py             # Auth dependencies (JWT, permissions)
│   │   │   ├── models.py           # User, RefreshSession, WsTicket models
│   │   │   ├── permissions.py      # RBAC permission matrix
│   │   │   ├── router.py           # Auth routes (login, refresh, logout)
│   │   │   ├── service.py          # Auth business logic
│   │   │   └── tokens.py           # JWT & token utilities
│   │   ├── core/                   # Framework concerns
│   │   │   ├── config.py           # Settings (pydantic-settings)
│   │   │   ├── deps.py             # FastAPI dependencies
│   │   │   ├── errors.py           # Structured error handling
│   │   │   ├── logging.py          # Logging configuration
│   │   │   ├── rate_limit.py       # SlowAPI rate limiting
│   │   │   └── security.py         # Argon2id password hashing
│   │   ├── db/                     # Database session & base model
│   │   ├── models/                 # SQLAlchemy ORM models
│   │   │   ├── billing.py          # Bill, BillItem, BillSplit, Payment
│   │   │   ├── common.py           # Mixins (IdMixin, TimestampMixin)
│   │   │   ├── idempotency.py      # IdempotencyKey
│   │   │   ├── menu.py             # MenuCategory, MenuItem, ItemVariant, ItemAddon
│   │   │   ├── order.py            # Order, OrderItem, OrderItemAddon
│   │   │   └── restaurant.py       # Restaurant, Branch
│   │   ├── realtime/               # WebSocket connection manager
│   │   ├── schemas/                # Pydantic request/response schemas
│   │   ├── services/               # Business logic (broadcast-free)
│   │   │   ├── admin_service.py    # Dashboard, reports, menu/staff CRUD
│   │   │   ├── audit_service.py    # Audit log writer
│   │   │   ├── billing_service.py  # Bills, splits, cash payments, receipts
│   │   │   ├── help_service.py     # Help call lifecycle
│   │   │   ├── kitchen_service.py  # KDS reads & order transitions
│   │   │   ├── order_service.py    # Order placement (idempotent, server-priced)
│   │   │   ├── staff_service.py    # Floor grid, serving, help resolution
│   │   │   ├── table_service.py    # QR sessions, menu reads
│   │   │   └── tax_service.py      # Tax configuration & computation
│   │   ├── main.py                 # FastAPI app factory
│   │   └── seed.py                 # Demo data seeder (Zaika restaurant)
│   ├── tests/                      # pytest test suite (25 test files)
│   ├── alembic.ini                 # Alembic configuration
│   └── requirements.txt            # Python dependencies
│
├── frontend/
│   ├── src/
│   │   ├── app/                    # App shell, router, providers
│   │   ├── features/
│   │   │   ├── admin/              # Owner dashboard (overview, orders, bills, menu, staff, settings)
│   │   │   ├── auth/               # Login page, auth guards
│   │   │   ├── guest/              # Guest experience (menu, cart, orders, tracking, bill, help)
│   │   │   ├── kitchen/            # Kitchen Display System (3-column board)
│   │   │   └── staff/              # Waiter dashboard (floor grid, orders, billing, help calls)
│   │   ├── shared/                 # Shared API client, types, components, stores
│   │   └── lib/                    # Utility functions
│   ├── components.json             # shadcn/ui configuration
│   ├── package.json
│   ├── tsconfig.json
│   └── vite.config.ts
│
├── .github/workflows/ci.yml       # CI: backend tests + frontend typecheck & build
├── docker-compose.yml              # PostgreSQL + API + Frontend
├── Dockerfile.backend              # Python 3.12 slim
├── Dockerfile.frontend             # Node 24 build → Nginx
├── .env.example                    # Environment variable reference
└── .gitignore
```

---

## Development

### Running Tests

**Backend** (uses SQLite in-memory by default):

```bash
cd backend
python -m pytest -q
```

For PostgreSQL-specific tests (concurrency, speculative insertion):

```bash
# Ensure PostgreSQL is running
cd backend
python -m pytest -q -m "not slow"
```

**Frontend** (Vitest):

```bash
cd frontend
npm test
```

### Database Migrations

```bash
cd backend

# Create a new migration
alembic revision --autogenerate -m "describe your change"

# Apply migrations
alembic upgrade head

# Rollback one step
alembic downgrade -1
```

### Environment Variables

See [`.env.example`](.env.example) for all configuration options. Key variables:

| Variable | Description | Default |
|----------|-------------|---------|
| `ENVIRONMENT` | `development` / `staging` / `production` | `development` |
| `DATABASE_URL` | PostgreSQL connection string | `postgresql+psycopg://tavessa:tavessa@localhost:5432/tavessa` |
| `SECRET_KEY` | JWT signing key (must change in production) | `dev-only-change-me-in-production` |
| `FRONTEND_URL` | Frontend origin for CORS | `http://localhost:5173` |
| `KITCHEN_DEV_KEY` | Dev-only KDS access key | `dev-kitchen-only` |
| `STAFF_DEV_KEY` | Dev-only staff access key | `dev-staff-only` |

---

## Permission Matrix

| Capability | Owner | Manager | Waiter | Kitchen |
|-----------|-------|---------|--------|---------|
| View dashboard | ✅ | ✅ | ❌ | ❌ |
| Manage settings | ✅ | 🔧 | ❌ | ❌ |
| Manage menu | ✅ | ✅ | ❌ | ❌ |
| Manage staff | ✅ | 🔧 | ❌ | ❌ |
| View financials | ✅ | ✅ | ❌ | ❌ |
| Confirm cash | ✅ | ✅ | ✅ | ❌ |
| Serve orders | ✅ | ✅ | ✅ | ❌ |
| Update kitchen | ✅ | ✅ | ❌ | ✅ |
| View kitchen | ✅ | ✅ | ❌ | ✅ |
| Configure tax | ✅ | ❌ | ❌ | ❌ |
| Issue refunds | ✅ | 🔧 | ❌ | ❌ |
| Void bills | ✅ | 🔧 | ❌ | ❌ |

🔧 = Configurable per manager (flags on the user record)

---

## Development Roadmap

The project was built incrementally across 8 stages:

1. **Stage 1** — Project foundation: FastAPI + React skeletons, health check, Docker Compose, CI
2. **Stage 2** — Guest interface: digital menu, cart, order review (frontend, demo data)
3. **Stage 3** — Backend integration: models, migrations, seed data, QR sessions, server-priced orders, idempotency
4. **Stage 4** — Kitchen Display System: kitchen API, order transitions, WebSocket, KDS board
5. **Stage 5** — Staff dashboard: floor grid, active orders, serving, help call management
6. **Stage 6** — Billing: bills, splits, cash payments, receipts, billing queue
7. **Stage 7** — Admin panel: JWT auth, RBAC, users, menu CRUD, tax config, voids, refunds, audit logs
8. **Stage 8** — Guest real-time: guest WebSocket tickets, live tracking, split/tax reconciliation

---

## Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/my-feature`)
3. Commit your changes (`git commit -m 'Add my feature'`)
4. Push to the branch (`git push origin feature/my-feature`)
5. Open a Pull Request

Please ensure:
- Backend tests pass: `cd backend && python -m pytest -q`
- Frontend builds: `cd frontend && npm run build`
- Code follows existing patterns and conventions

---

## License

This project is provided as-is for educational and demonstration purposes.

---

<p align="center">
  Built with ❤️ using FastAPI + React
</p>
