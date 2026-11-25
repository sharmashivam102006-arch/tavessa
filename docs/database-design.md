# Tavessa — Database Design

> Status: Blueprint v1 — 23 Nov 2026. No application code built yet.
> Companion docs: `project-overview.md`, `architecture.md`, `api-plan.md`,
> `development-roadmap.md`.
>
> **Stage 3 implementation status (23 Nov 2026):** the following
> tables are implemented, migrated (`9cd1cf3c6050`) and verified
> against real PostgreSQL: `restaurants`, `branches`,
> `dining_tables`, `table_tokens`, `table_sessions`,
> `menu_categories`, `menu_items`, `item_variants`,
> `item_add_ons`, `orders`, `order_items`, `order_item_add_ons`,
> `help_calls`, `idempotency_keys`. NOT yet implemented (later
> stages): `users`, `staff_assignments`, `kitchen_stations`,
> `kitchen_tickets`, `bills`, `bill_items`, `bill_splits`,
> `payments`, `refunds`, `reviews`, `notifications`, `audit_logs`.

## 1. Principles

1. **Relational, normalised where it matters, snapshotted where history matters.**
   Menu data is normalised (categories, items, variants, add-ons). Order and
   bill history is snapshotted (prices and names copied at order/bill time),
   so history never changes when the menu does.
2. **One tenant column, everywhere it applies.** Every tenant-scoped table
   carries `restaurant_id` (FK to `restaurants`), and every query in the
   application scopes by it. Tenant isolation is an application rule, tested
   by negative tests — not a hope.
3. **Statuses are separate per entity.** A table, an order, an order item, a
   kitchen ticket, a bill, a payment, and a help call each have their own
   status column and their own state machine. No shared status, no
   overloading.
4. **Money is integer paise.** `BIGINT`, never `FLOAT`. No `NUMERIC` for money
   in application arithmetic.
5. **No table without a workflow.** Every table below is used by a named
   workflow in `project-overview.md` § 5. If a workflow does not need it, the
   table does not exist.
6. **UUID primary keys** for business tables; `BIGSERIAL` only for
   append-only logs where ordering by insertion is the point (`audit_logs`
   uses UUID too, with `created_at` as the ordering key — see § 3).
7. **Timestamps:** `created_at` / `updated_at` (`TIMESTAMPTZ`, UTC) on all
   business tables. Soft delete is **not** used in v1 except where stated
   (`menu_items.is_available` is availability, not deletion; deactivated
   staff are `is_active = false`). Deleted rows are not silently hidden by a
   global filter — history must remain queryable.

## 2. Entity-relationship overview

```
restaurants 1───* branches 1───* dining_tables 1───* table_sessions
     │               │                │                    │
     │               │                │                    ├──* orders ──* order_items
     │               │                │                    ├──* bills ──* bill_items
     │               │                │                    │              └──* bill_splits ──* payments
     │               │                │                    ├──* help_calls
     │               │                │                    └──* reviews
     │               │
     │               ├──* users ──* staff_assignments ──* dining_tables
     │               │     │
     │               │     └──* audit_logs
     │               │
     │               ├──* menu_categories 1───* menu_items ──* item_variants
     │               │                              │  └─* item_add_ons ──* add_ons
     │               │                              └─* order_items (snapshot)
     │               │
     │               └──* kitchen_stations 1───* kitchen_tickets ──* order_items
     │
     └──* table_tokens ──* dining_tables (one active token per table)

orders ──* kitchen_tickets (one per order × station)
bills ──* payments, bills ──* refunds
idempotency_keys (per restaurant + key, for order placement)
notifications (derived/event log for staff + guest — see § 3.18)
```

Reading guide: `1───*` is one-to-many. `order_items` belongs to one order and
one menu item (snapshot); a `kitchen_ticket` groups the order items of one
order that belong to one station.

## 3. Tables

Conventions used below: `PK` primary key, `FK` foreign key, `UQ` unique,
`NN` not null. All tables have `id UUID PK DEFAULT gen_random_uuid()`,
`created_at TIMESTAMPTZ NN DEFAULT now()`, `updated_at TIMESTAMPTZ NN
DEFAULT now()` unless stated otherwise. All tenant-scoped tables have
`restaurant_id UUID NN FK → restaurants.id`.

### 3.1 `restaurants` — the tenant

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `name` | TEXT NN | e.g. `Zaika` |
| `slug` | TEXT NN UQ | URL-safe, e.g. `zaika` |
| `tagline` | TEXT | "Every Table. Every Order. In Sync." is the platform tagline; restaurant tagline is separate |
| `address` | TEXT | |
| `phone` | TEXT | |
| `currency` | CHAR(3) NN DEFAULT 'INR' | v1: INR only |
| `contact_name` | TEXT | Stage 7: owner-editable contact name |
| `contact_email` | TEXT | Stage 7: owner-editable contact email |
| `tax_mode` | TEXT NN DEFAULT 'inclusive' | **Placeholder — value decided by D1.** `inclusive` / `exclusive` / `none` — superseded in Stage 7 by the `tax_configurations` table (see § 3.29); this column is not used for billing. |
| `service_charge_bps` | INT NN DEFAULT 0 | Basis points; 0 in v1 unless D1 decides otherwise |
| `is_active` | BOOLEAN NN DEFAULT true | |
| `settings` | JSONB DEFAULT '{}' | Small, versioned settings only (see § 8). Not a dumping ground. |

Indexes: `slug` (UQ). Seed: one row, Zaika.

### 3.2 `branches`

One branch per restaurant in v1; the table exists so a second branch is a data
operation, not a schema change. Not exposed as a management surface in v1.

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `restaurant_id` | UUID NN FK | |
| `name` | TEXT NN | e.g. `Zaika — Main` |
| `address` | TEXT | |
| `timezone` | TEXT NN DEFAULT 'Asia/Kolkata' | Stage 7: branch timezone (IANA, validated against zoneinfo). All dashboard/report day boundaries are computed in this timezone, never server TZ. Seeded Zaika branch = `Asia/Kolkata`. |
| `contact_name` | TEXT | Stage 7 |
| `contact_phone` | TEXT | Stage 7 |
| `contact_email` | TEXT | Stage 7 |
| `is_active` | BOOLEAN NN DEFAULT true | |

UQ: `(restaurant_id, name)`. Index: `restaurant_id`.

### 3.3 `users` — staff and owners (not guests)

Guests never have a row here. A user belongs to one restaurant in v1.

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `restaurant_id` | UUID NN FK | |
| `branch_id` | UUID FK → branches.id, nullable | Null = all branches of that restaurant (v1) |
| `email` | TEXT NN | Login identifier, **stored lowercased** (works on SQLite tests too — no CITEXT dependency). |
| `password_hash` | TEXT NN | Argon2id — never plaintext |
| `role` | TEXT NN | `owner` \| `manager` \| `waiter` \| `kitchen` (Stage 7: `admin` renamed to `manager`; see § 6). |
| `is_active` | BOOLEAN NN DEFAULT true | Deactivation blocks login + token validation; every request re-validates the user, so it is effective immediately |
| `can_manage_staff` | BOOLEAN NN DEFAULT false | Stage 7: manager permission flag |
| `can_manage_settings` | BOOLEAN NN DEFAULT false | Stage 7: manager permission flag |
| `can_refund` | BOOLEAN NN DEFAULT false | Stage 7: manager permission flag |
| `can_void` | BOOLEAN NN DEFAULT false | Stage 7: manager permission flag |
| `must_change_password` | BOOLEAN NN DEFAULT false | Stage 7: set on user creation / password reset; cleared by `/auth/change-password` |
| `deactivated_at` | TIMESTAMPTZ | Stage 7: set on deactivate |
| `last_login_at` | TIMESTAMPTZ | |
| `created_at` | TIMESTAMPTZ NN DEFAULT now() | |

UQ: `(restaurant_id, email)`. Indexes: `restaurant_id`, `email`.
Check: `role IN ('owner','manager','waiter','kitchen')`.
**Stage 7 as built:** no `name`/`phone` columns — the blueprint's
`name`/`phone`/`CITEXT` were not built (email is the identifier; the
display name is the email). Owner always has all permissions; manager
flags default False.

### 3.4 `dining_tables`

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `restaurant_id` | UUID NN FK | |
| `branch_id` | UUID NN FK | |
| `number` | TEXT NN | Display number, e.g. `5`. TEXT so `A-1` works. |
| `label` | TEXT | e.g. `Window · 4 seats` |
| `seats` | INT NN CHECK (seats > 0) | |
| `zone` | TEXT | e.g. `Main Hall`, `Patio` — floor-map grouping |
| `position_x`, `position_y` | INT | Floor-map coordinates (nullable until the map editor exists; Stage 5) |
| `status` | TEXT NN DEFAULT 'available' | See § 6 — table status machine |
| `is_active` | BOOLEAN NN DEFAULT true | Out-of-service tables are `status='out_of_service'`, not deleted |

UQ: `(restaurant_id, branch_id, number)`. Indexes: `(restaurant_id, status)`.

### 3.5 `table_tokens` — QR tokens

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `restaurant_id` | UUID NN FK | |
| `table_id` | UUID NN FK → dining_tables.id | |
| `token_hash` | TEXT NN UQ | SHA-256 hex of the raw token. The raw token is shown **once** at creation (for QR printing) and never stored. |
| `is_active` | BOOLEAN NN DEFAULT true | Rotation sets old token `is_active=false` |
| `expires_at` | TIMESTAMPTZ | Null = no expiry (rotation is the control; expiry decided by D5) |
| `created_by` | UUID FK → users.id | Who generated/rotated it |
| `revoked_at` | TIMESTAMPTZ | Set on rotation/revocation |

Indexes: `token_hash` (UQ), `(table_id, is_active)`.
Rule: at most one active token per table — enforced by a partial unique index
`UQ (table_id) WHERE is_active` (Stage 2 implementation).
> **Stage 3 as built (23 Nov 2026):** tokens carry a `status` column
> (`active` | `revoked` | `expired`) instead of the `is_active`
> boolean above, plus `last_used_at`; expiry is `status = 'expired'`
> OR `expires_at` passed. The partial unique index is
> `UQ (table_id) WHERE status = 'active'`. `revoked_at`/`created_by`
> are not stored (rotation is a dev CLI in Stage 3; the admin
> rotate endpoint lands in Stage 7).

### 3.6 `table_sessions` — one visit at a table

Created on QR scan; closed when the visit ends.

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `restaurant_id` | UUID NN FK | |
| `table_id` | UUID NN FK | |
| `token_id` | UUID FK → table_tokens.id | Which token opened it |
| `guest_name` | TEXT | Optional, guest-supplied |
| `guest_count` | INT CHECK (guest_count > 0) | Optional |
| `status` | TEXT NN DEFAULT 'active' | `active` \| `billing_requested` \| `closed` \| `expired` — see § 6 |
| `opened_at` | TIMESTAMPTZ NN DEFAULT now() | |
| `closed_at` | TIMESTAMPTZ | |
| `closed_by` | UUID FK → users.id, nullable | Staff-closed sessions record who |

Indexes: `(table_id, status)`, `(restaurant_id, status)`.
Rule: at most one `active` session per table — partial unique index
`UQ (table_id) WHERE status = 'active'`. A scan while a session is active
resumes that session; it does not create a second one.

### 3.7 `menu_categories`

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `restaurant_id` | UUID NN FK | |
| `name` | TEXT NN | e.g. `Starters`, `Main Course`, `Breads`, `Beverages` |
| `description` | TEXT | |
| `sort_order` | INT NN DEFAULT 0 | |
| `is_active` | BOOLEAN NN DEFAULT true | |

UQ: `(restaurant_id, name)`. Index: `(restaurant_id, sort_order)`.

### 3.8 `menu_items`

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `restaurant_id` | UUID NN FK | |
| `category_id` | UUID NN FK → menu_categories.id | |
| `name` | TEXT NN | e.g. `Paneer Butter Masala` |
| `description` | TEXT | |
| `image_url` | TEXT | Served URL; upload validated (see `architecture.md` § 9) |
| `base_price_paise` | BIGINT NN CHECK (>= 0) | Price without variant/add-on |
| `is_available` | BOOLEAN NN DEFAULT true | Owner toggles this; unavailable items cannot be ordered |
| `is_veg` | BOOLEAN | `true` veg / `false` non-veg / null unknown |
| `spice_level` | INT CHECK (0–3) | Optional |
| `sort_order` | INT NN DEFAULT 0 | |
| `station_id` | UUID FK → kitchen_stations.id | Default station for this item (see § 3.12). An item belongs to exactly one station. |

Indexes: `(restaurant_id, category_id)`, `(restaurant_id, is_available)`.

### 3.9 `item_variants`

E.g. `Half / Full`, `Regular / Large`. Zero or more per item; at most one
variant per order item.

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `menu_item_id` | UUID NN FK → menu_items.id ON DELETE CASCADE | |
| `name` | TEXT NN | e.g. `Full` |
| `price_delta_paise` | BIGINT NN DEFAULT 0 | Added to `base_price_paise`. Can be negative (rare); `CHECK` allows it, service validates the final price ≥ 0. |
| `sort_order` | INT NN DEFAULT 0 | |
| `is_active` | BOOLEAN NN DEFAULT true | |

UQ: `(menu_item_id, name)`. Note: `restaurant_id` is reached via the item;
variant queries always join through the item and check its restaurant.

### 3.10 `add_ons` and 3.11 `item_add_ons`

> **Stage 3 scoping choice (23 Nov 2026, recorded):** the catalogue +
> join split below is NOT what was built. The Zaika demo data defines
> add-ons **per item** (same name repeated with per-item ids, no shared
> catalogue id in the frontend contract), so Stage 3 stores one
> `item_add_ons` row scoped to `(restaurant_id, menu_item_id)` with
> `slug` unique per item, carrying `name` + `price_paise` itself
> (`app/models/menu.py: ItemAddon`). No separate `add_ons` catalogue
> table exists. If a shared catalogue is ever needed, it is a
> migration that adds `add_ons` and re-points these rows — the wire
> shape (`id`, `name`, `pricePaise`, `isAvailable`) does not change.

`add_ons` is the catalogue (e.g. `Extra Cheese`, `Extra Raita`); `item_add_ons`
is the many-to-many join saying which add-ons a given item offers, with a
per-item price (the same add-on can cost differently on different items).

**`add_ons`**

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `restaurant_id` | UUID NN FK | |
| `name` | TEXT NN | |
| `is_active` | BOOLEAN NN DEFAULT true | |

UQ: `(restaurant_id, name)`.

**`item_add_ons`**

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `menu_item_id` | UUID NN FK ON DELETE CASCADE | |
| `add_on_id` | UUID NN FK | |
| `price_paise` | BIGINT NN CHECK (>= 0) | Price of this add-on *for this item* |
| `is_active` | BOOLEAN NN DEFAULT true | |

UQ: `(menu_item_id, add_on_id)`.

### 3.12 `kitchen_stations`

Seed for Zaika: `Main Kitchen`, `Tandoor`, `Beverages`. Configurable per
restaurant; not hard-coded in application code.

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `restaurant_id` | UUID NN FK | |
| `name` | TEXT NN | |
| `sort_order` | INT NN DEFAULT 0 | |
| `is_active` | BOOLEAN NN DEFAULT true | |

UQ: `(restaurant_id, name)`.

### 3.13 `orders`

One order = one placement by one table session (a guest may place several
orders in a visit; each is a separate order with its own number).

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `restaurant_id` | UUID NN FK | |
| `branch_id` | UUID FK | |
| `table_session_id` | UUID NN FK → table_sessions.id | |
| `table_id` | UUID NN FK | Denormalised for fast floor/KDS queries; must match the session's table (enforced in service) |
| `order_number` | TEXT NN | Human number, e.g. `A-104`. Unique per restaurant per day — see below |
| `status` | TEXT NN DEFAULT 'placed' | See § 6 — order status machine |
| `subtotal_paise` | BIGINT NN | Sum of order-item line totals, server-computed |
| `tax_paise` | BIGINT NN DEFAULT 0 | Per D1 |
| `service_charge_paise` | BIGINT NN DEFAULT 0 | Per D1 |
| `total_paise` | BIGINT NN | `subtotal + tax + service_charge`, server-computed |
| `placed_by` | TEXT NN DEFAULT 'guest' | `guest` \| `waiter` (future) — v1 is `guest` |
| `placed_at` | TIMESTAMPTZ NN DEFAULT now() | |
| `accepted_at`, `ready_at`, `served_at`, `completed_at`, `cancelled_at` | TIMESTAMPTZ | Set on transition; null until then |
| `cancel_reason` | TEXT | |
| `idempotency_key` | TEXT | Echo of the request key (see § 3.20) |
| `version` | INT NN DEFAULT 1 | Optimistic locking for concurrent status updates |

UQ: `(restaurant_id, order_number)` — with `order_number` scoped per day in
application logic (e.g. `A-104` resets daily; the DB uniqueness is per
restaurant + number, so the service must include the date in the number or use
a daily sequence — Stage 3 implementation detail, recorded here so it is not
discovered late: **use a per-restaurant daily sequence table or a
`order_number` that embeds the date**).
> **Stage 3 decision (23 Nov 2026):** the daily reset was NOT built.
> Order numbers are a per-restaurant running sequence that never
> resets, format `<SLUG>-0001` (e.g. `ZAIKA-0001`), allocated under a
> row lock on the restaurant row (`order_service._next_order_number`).
> The `(restaurant_id, order_number)` uniqueness is the source of
> truth. A daily reset, if ever wanted, is a service change, not a
> schema change.
Indexes: `(restaurant_id, status)`, `(table_session_id, placed_at)`,
`(restaurant_id, placed_at)`.

### 3.14 `order_items`

Price and name snapshots live here. This is the table that makes "a price
change never rewrites history" true.

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `order_id` | UUID NN FK → orders.id ON DELETE CASCADE | |
| `menu_item_id` | UUID NN FK | Kept for analytics; the snapshot below is the billing truth |
| `item_name_snapshot` | TEXT NN | Name at order time |
| `variant_id` | UUID FK → item_variants.id, nullable | |
| `variant_name_snapshot` | TEXT | Null when no variant |
| `unit_price_paise` | BIGINT NN CHECK (>= 0) | `base + variant delta` at order time |
| `quantity` | INT NN CHECK (quantity >= 1 AND quantity <= 20) | Max is a Stage 3 constant, not a guess in the UI |
| `add_ons_snapshot` | JSONB NN DEFAULT '[]' | `[{add_on_id, name, price_paise}]` — snapshot, not a live join |
| `add_ons_total_paise` | BIGINT NN DEFAULT 0 | Per-unit add-on total |
| `line_total_paise` | BIGINT NN | `(unit_price + add_ons_total) * quantity`, server-computed |
| `special_instructions` | TEXT | Per-item note, e.g. `less spicy` |
| `station_id` | UUID NN FK → kitchen_stations.id | Copied from the menu item at order time |
| `status` | TEXT NN DEFAULT 'queued' | See § 6 — item status machine |
| `prepared_at`, `ready_at`, `served_at`, `cancelled_at` | TIMESTAMPTZ | |

Indexes: `(order_id)`, `(station_id, status)`, `(order_id, station_id)`.
Check: `line_total_paise = (unit_price_paise + add_ons_total_paise) * quantity`
— enforced in the service and asserted in tests; a DB generated column is an
acceptable alternative, decided in Stage 3.
> **Stage 3 as built (23 Nov 2026):** add-ons are NOT a JSONB snapshot
> on this row. Each chosen add-on is an `order_item_add_ons` row
> (`order_item_id`, `add_on_id`, `name_snapshot`, `price_paise`), and
> `unit_price_paise` is the per-unit price **including** variant delta
> + add-ons, so `total_paise = unit_price_paise × quantity`.
> `station_id` is not a column in Stage 3 — the item's station is
> plain text on `menu_items.station`; `kitchen_stations` lands in
> Stage 4.

### 3.15 `kitchen_tickets` — one per order × station

Derived at order placement; the KDS reads tickets, not raw orders.

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `restaurant_id` | UUID NN FK | |
| `order_id` | UUID NN FK → orders.id ON DELETE CASCADE | |
| `station_id` | UUID NN FK → kitchen_stations.id | |
| `table_id` | UUID NN FK | For the KDS card header |
| `status` | TEXT NN DEFAULT 'queued' | `queued` \| `preparing` \| `ready` \| `served` \| `cancelled` — derived from its items, plus explicit accept |
| `accepted_at`, `ready_at` | TIMESTAMPTZ | |
| `sla_breached_at` | TIMESTAMPTZ | Set when elapsed time passes the station threshold (Stage 4) |

UQ: `(order_id, station_id)`. Indexes: `(restaurant_id, station_id, status)`,
`(station_id, status, created_at)`.
Rule: a ticket exists only if the order has at least one item for that
station. Ticket status is derived: `ready` when all its non-cancelled items
are `ready`/`served`; `preparing` when any item is `preparing`; otherwise
`queued`. The derivation lives in `kitchen_service`, in the same transaction
as the item update — not in a trigger, not in the frontend.

### 3.16 `bills`

One open bill per table session in v1 (see `project-overview.md` § 5,
failure branch). A bill is generated from the session's unpaid orders.

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `restaurant_id` | UUID NN FK | |
| `branch_id` | UUID FK | Nullable (single-branch Zaika in v1) |
| `table_session_id` | UUID NN FK → table_sessions.id | |
| `table_id` | UUID NN FK → dining_tables.id | Denormalised for staff lists, like orders/help_calls |
| `bill_number` | TEXT NN | e.g. `B-2026-11-23-018`. UQ per restaurant. |
| `status` | TEXT NN DEFAULT 'issued' | See § 6 — bill status machine. CHECK `issued \| payment_requested \| paid \| voided \| refunded \| partially_refunded` (Stage 7 added the last three). |
| `subtotal_paise` | BIGINT NN | Server-computed from bill items |
| `tax_paise` | BIGINT NN DEFAULT 0 | Stage 7 (D1 decided): computed from the active tax config at freeze; 0 with no snapshot = unconfigured at freeze, not 0% |
| `tax_rate_bps_snapshot` | INT | Stage 7: NULL = unconfigured at freeze |
| `tax_mode_snapshot` | TEXT | Stage 7: `inclusive` \| `exclusive`, NULL = unconfigured at freeze |
| `tax_label_snapshot` | TEXT | Stage 7: e.g. `GST`, NULL = unconfigured at freeze |
| `voided_at` | TIMESTAMPTZ | Stage 7: set on void |
| `void_reason` | TEXT | Stage 7: required on void |
| `voided_by` | UUID FK → users.id | Stage 7: the owner/manager who voided |
| `service_charge_paise` | BIGINT NN DEFAULT 0 | Per D1 |
| `discount_paise` | BIGINT NN DEFAULT 0 | Per D2; 0 unless discounts are decided in |
| `total_paise` | BIGINT NN | Server-computed |
| `amount_paid_paise` | BIGINT NN DEFAULT 0 | Sum of confirmed payments; updated in the payment transaction |
| `issued_at` | TIMESTAMPTZ NN DEFAULT now() | |
| `payment_requested_at` | TIMESTAMPTZ | Explicit column, set on the `issued → payment_requested` transition (not derived from `updated_at`) |
| `paid_at` | TIMESTAMPTZ | Set when `amount_paid == total` |
| `version` | INT NN DEFAULT 1 | Optimistic locking for payment confirmation |

> **Stage 7 as implemented (24 Nov 2026):** the void columns and
> the `voided`/`refunded`/`partially_refunded` states ARE built (see
> rows above). Voided bills do NOT block a new bill (the partial UQ
> below still covers only `issued`/`payment_requested`), and voided
> bills' items are NOT counted as referenced when computing billable
> items — a voided bill's items become billable again. A voided
> bill's outstanding is 0 (written off).

UQ: `(restaurant_id, bill_number)`. Partial UQ: one open bill per session —
`UQ (table_session_id) WHERE status IN ('issued','payment_requested')`
(Stage 6 implementation, unchanged in Stage 7).
Indexes: `(restaurant_id, status)`, `(table_session_id)`, `(restaurant_id, issued_at)`.

### 3.17 `bill_items`

Snapshot again at bill time, so a bill is self-contained even if the order is
later amended.

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `bill_id` | UUID NN FK → bills.id ON DELETE CASCADE | |
| `order_item_id` | UUID FK → order_items.id | Nullable if the bill line is a manual adjustment (Stage 6) |
| `order_id` | UUID FK → orders.id | For traceability |
| `item_name_snapshot` | TEXT NN | |
| `variant_name_snapshot` | TEXT | |
| `add_on_names_snapshot` | JSON NN | List of add-on names (JSON, following the order_items snapshot pattern) |
| `unit_price_paise` | BIGINT NN | Copied from `order_items` |
| `quantity` | INT NN | |
| `line_total_paise` | BIGINT NN | Server-computed |

Index: `(bill_id)`.

### 3.18 `bill_splits`

Parts of one bill. Equal or custom; the parts must sum to the bill total.

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `bill_id` | UUID NN FK → bills.id ON DELETE CASCADE | |
| `label` | TEXT NN | e.g. `Split 1`, or a guest name |
| `amount_paise` | BIGINT NN CHECK (> 0) | |
| `mode` | TEXT | Stage 8: `equal` \| `custom`; NULL = created before Stage 8 (treated as `custom` — never silently re-derived). CHECK `mode IS NULL OR mode IN ('equal','custom')`. |
| `status` | TEXT NN DEFAULT 'unpaid' | `unpaid` \| `payment_requested` \| `paid` |
| `paid_at` | TIMESTAMPTZ | |

Index: `(bill_id)`. Rule (service + test): `SUM(splits) = bills.total_paise`.
Equal split: `base = total // n`, `remainder = total % n`, first `remainder`
splits get `+1` paise — deterministic, documented, tested. Custom split:
explicit amounts, must sum exactly, else `422`.

> **Stage 8 — split / tax-freeze reconciliation (25 Nov 2026).**
> The Stage 7 limitation is closed: splits created before a tax freeze
> can no longer disagree with the frozen total.
>
> - **Equal splits are re-derived at freeze.** In the same transaction
>   as `freeze_tax_on_bill`, the UNPAID equal splits are recomputed
>   from the frozen total minus already-paid split amounts, using the
>   existing remainder rule above. Equal means equal — no customer
>   meaning is lost. A `bill.splits_reconciled` audit row is written
>   (old/new amounts) whenever the amounts actually change; an exact
>   reconciliation writes no audit row.
> - **Custom splits block the freeze.** If custom (or pre-Stage 8,
>   `mode` NULL) splits no longer sum to the frozen total, the whole
>   freeze transaction rolls back — the bill stays `issued`, totals
>   unchanged — and the API returns `409 split_reconciliation_required`
>   with `{expected_total_paise, splits_sum_paise, difference_paise}`
>   and a message telling the guest to update the splits. The guest
>   re-creates splits (validated against the LIVE-computed total with
>   the current tax config, not the stored pre-freeze total) and
>   retries the payment request.
> - **Payments never accept a stale split amount.** A split payment
>   validates the split's amount against the frozen bill total at
>   payment time under the bill + split row lock; a mismatch → `409`,
>   no payment row. A split's outstanding is always `split amount −
>   payments on that split`; refunds reduce the payment, not the split
>   amount.
> - **History is untouched.** Freeze/reconciliation only ever touches
>   `issued` bills. Paid bills and their recorded payments never
>   change. `POST /guest/bill` regeneration of an `issued` bill no
>   longer drops unpaid splits when the total changes — splits survive
>   regeneration and are reconciled at freeze.
> - **Trade-off recorded:** the freeze can now fail with a 409 the
>   guest must act on (re-create splits). That is deliberate: silently
>   rewriting a guest's custom amounts would invent a payment the
>   guest never agreed to, and silently accepting a stale split amount
>   would over/under-collect. The 409 body carries everything the UI
>   needs to show the fix.

### 3.19 `payments` — the only record that money was collected

A row is created **only** by authorised staff confirming cash collection.
There is no guest-created payment row.

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `restaurant_id` | UUID NN FK | |
| `bill_id` | UUID NN FK → bills.id | |
| `bill_split_id` | UUID FK → bill_splits.id, nullable | Null = payment against the whole bill |
| `method` | TEXT NN DEFAULT 'cash' | v1: `cash` only. Check constraint enforces it — adding a method is a migration + decision, not a string change. |
| `idempotency_key` | TEXT NN | The request's `Idempotency-Key` header (required); replay key for the payment claim |
| `amount_paise` | BIGINT NN CHECK (> 0) | Must be ≤ amount due on the bill/split (service-validated) |
| `collected_by` | UUID FK → users.id, nullable | Stage 7: the confirming user's id when called with a JWT; NULL only for dev-key calls in dev (Stage 6: always NULL — no users system yet). |
| `collected_at` | TIMESTAMPTZ NN DEFAULT now() | |
| `change_due_paise` | BIGINT NN DEFAULT 0 | Overpayment handling per D9 — always 0 in v1 (no change handling) |
| `status` | TEXT NN DEFAULT 'confirmed' | `confirmed` \| `refunded` \| `partially_refunded` (Stage 7 added the last two; recomputed on refund — the payment row itself is never deleted or overwritten). |
| `receipt_number` | TEXT NN | Generated at confirmation, e.g. `R-2026-11-23-001`. UQ **per restaurant** `(restaurant_id, receipt_number)` — the daily sequence restarts per restaurant, so the UQ is not global. |
| `note` | TEXT | Optional waiter note recorded at confirmation |

Indexes: `(bill_id)`, `(restaurant_id, collected_at)`, `(collected_by, collected_at)`.
Check: `method = 'cash'`.

> **Stage 6 as implemented (23 Nov 2026):** payments are append-only —
> there is no update/delete code path for this table anywhere in the
> app. The audit trail in this stage IS this table (timestamps, amounts,
> receipt numbers, idempotency keys); there is no `audit_logs` table.
>
> **Stage 7 (24 Nov 2026):** `audit_logs` now exists (§ 3.25) and
> `payment.recorded` is written there in the same transaction as the
> payment. Payments stay append-only — only the `status` column
> changes, via refunds (never a delete/overwrite).

### 3.20 `refunds`

> **BUILT in Stage 7 (24 Nov 2026)** — see `api-plan.md` § 7.3.

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `restaurant_id` | UUID NN FK | |
| `payment_id` | UUID NN FK → payments.id | |
| `bill_id` | UUID NN FK | |
| `amount_paise` | BIGINT NN CHECK (> 0) | ≤ payment amount minus prior refunds (service-validated under row lock) |
| `reason` | TEXT NN | Required |
| `refund_reference` | TEXT NN | e.g. `RFD-2026-11-24-001`. UQ per restaurant. |
| `refunded_by` | UUID NN FK → users.id | Owner, or manager with `can_refund` (D3 decided in Stage 7) |
| `refunded_at` | TIMESTAMPTZ NN DEFAULT now() | |
| `idempotency_key` | TEXT NN | The request's `Idempotency-Key` header (required) |
| `created_at` | TIMESTAMPTZ NN DEFAULT now() | |

UQ: `(restaurant_id, bill_id, idempotency_key)` — the refund claim
scope, reusing the Stage 3.2/Stage 6 single-transaction claim pattern.
Index: `(payment_id)`, `(bill_id)`.

### 3.21 `help_calls`

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `restaurant_id` | UUID NN FK | |
| `table_session_id` | UUID NN FK | |
| `table_id` | UUID NN FK | |
| `status` | TEXT NN DEFAULT 'open' | `open` \| `acknowledged` \| `resolved` \| `cancelled` — see § 6 |
| `note` | TEXT | Guest's note, e.g. `Need extra plates` |
| `assigned_to` | UUID FK → users.id, nullable | Waiter who accepted |
| `created_at` | TIMESTAMPTZ NN | `requested_at` is `created_at` |
| `acknowledged_at`, `resolved_at` | TIMESTAMPTZ | |

Indexes: `(restaurant_id, status)`, `(table_session_id, status)`.
Rule: one open call per table session — partial unique index
`UQ (table_session_id) WHERE status IN ('open','acknowledged')`.

### 3.22 `staff_assignments`

Which waiter is responsible for which tables (per shift / period).

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `restaurant_id` | UUID NN FK | |
| `user_id` | UUID NN FK → users.id | Must be role `waiter` (service-validated) |
| `table_id` | UUID NN FK → dining_tables.id | |
| `assigned_from` | TIMESTAMPTZ NN DEFAULT now() | |
| `assigned_until` | TIMESTAMPTZ | Null = open-ended |
| `is_active` | BOOLEAN NN DEFAULT true | |

Index: `(user_id, is_active)`, `(table_id, is_active)`.

### 3.23 `reviews`

One review per table session, after the bill is paid.

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `restaurant_id` | UUID NN FK | |
| `table_session_id` | UUID NN FK | |
| `rating` | INT NN CHECK (1–5) | |
| `comment` | TEXT | |
| `guest_name` | TEXT | Optional |
| `status` | TEXT NN DEFAULT 'published' | `published` \| `held` — value decided by D6 |
| `created_at` | TIMESTAMPTZ NN | |

UQ: `(table_session_id)` — one review per session. Index:
`(restaurant_id, created_at)`.

### 3.24 `notifications`

A persistent record of events worth showing in a staff/owner inbox or
re-delivering after reconnect. This is **not** the realtime transport —
WebSocket is the transport; this table is the durable record for things a
user missed. Keep it small: only `help_call`, `bill.payment_requested`,
and `order.ready` create rows in v1, and rows are pruned by a retention rule
(Stage 8 decision, e.g. 30 days).

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `restaurant_id` | UUID NN FK | |
| `user_id` | UUID FK → users.id, nullable | Null = broadcast to a role/channel |
| `type` | TEXT NN | e.g. `help_call.open`, `bill.payment_requested` |
| `title`, `body` | TEXT NN | |
| `reference_id` | UUID | Order/bill/call id |
| `is_read` | BOOLEAN NN DEFAULT false | |
| `created_at` | TIMESTAMPTZ NN | |

Index: `(user_id, is_read, created_at)`, `(restaurant_id, created_at)`.

### 3.25 `audit_logs` — append-only

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `restaurant_id` | UUID NN FK | |
| `branch_id` | UUID FK → branches.id, nullable | Stage 7 |
| `actor_user_id` | UUID FK → users.id, nullable | Stage 7: null = system/bootstrap (no `user_id`/`table_session_id`/`ip_address` columns — not built) |
| `action` | TEXT NN | e.g. `login.success`, `refund.created`, `menu.price_changed`, `bill.voided`, `user.role_changed` |
| `entity_type`, `entity_id` | TEXT / UUID | What changed |
| `details` | JSONB DEFAULT '{}' | Before/after values for sensitive fields. The writer drops any key containing `password`/`token`/`secret`/`hash`/`otp`/`cookie`/`dev_key`/`authorization` and truncates values to 500 chars — secrets are never written. |
| `correlation_id` | TEXT | Stage 7: nullable |
| `created_at` | TIMESTAMPTZ NN DEFAULT now() | |

**Stage 7 as built (24 Nov 2026):** the table exists now (migration
`b7c2e9a1d4f5`). No UPDATE/DELETE methods anywhere, no API route
mutates it. Audit rows are written in the same transaction as the
action where possible; failed operations write no success record
(except login failures, recorded as failures).

Indexes: `(restaurant_id, created_at)`, `(entity_type, entity_id)`.
Rule: no UPDATE/DELETE on this table in application code; retention is a
separate, owner-approved archival operation, not a row delete.

### 3.26 `idempotency_keys`

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `restaurant_id` | UUID NN FK | |
| `table_session_id` | UUID NN FK → table_sessions.id | **Stage 3.1:** scope column — a key is per-session |
| `key` | TEXT NN | Client-supplied `Idempotency-Key` header |
| `endpoint` | TEXT NN | e.g. `POST /api/v1/orders` |
| `request_hash` | TEXT NN | Hash of the canonical request body — a replay with a different body is a `422`, not a replay |
| `response_status` | INT | Stored response status (NULL while the claim is pending) |
| `response_body` | JSONB | Stored response, returned verbatim on replay (NULL while pending) |
| `order_id` | UUID FK → orders.id, nullable | Convenience link for order placement; set in the same transaction as the order |
| `created_at` | TIMESTAMPTZ NN | |
| `expires_at` | TIMESTAMPTZ NN | e.g. created + 24 h; replay after expiry is a new request |

UQ: `(restaurant_id, table_session_id, endpoint, key)` — **Stage 3.1:**
the scope gained `table_session_id`, so one session can never replay
or reuse another session's key, and the same key string in a
different session is an independent claim. Index: `expires_at` (for
cleanup).

Claim lifecycle (Stage 3.2, api-plan.md § 10): claim + order +
stored response are created in ONE transaction — the claim row is
inserted (flush, no commit), the order is created with `order_id`
set on the claim, the stored response is filled in, and a single
commit lands all three. Any failure rolls the whole transaction
back. In steady state a committed row ALWAYS has its response
filled; a row is never visible with `response_body` NULL. Such
rows can only be leftovers from Stage 3.1's three-transaction
design (claim committed separately) and are handled in the
conflict path: rebuild from `order_id` when set, or delete the row
and retry the claim when it is older than 60 s (expired rows the
same). A failed placement leaves no row at all, so a corrected
retry with the same key proceeds.
Only order placement uses this table in v1; bill generation and payment
confirmation rely on status guards + row locks instead (they are not safely
replayable by key alone — see `api-plan.md` § 7).

### 3.27 `refresh_sessions` — rotating refresh tokens (Stage 7)

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `user_id` | UUID NN FK → users.id | |
| `family_id` | UUID NN | Token family — reuse of a revoked token revokes the whole family |
| `token_hash` | TEXT NN | SHA-256 hex of the opaque refresh token; the raw token is never stored |
| `expires_at` | TIMESTAMPTZ NN | 7 days from issue |
| `revoked_at` | TIMESTAMPTZ | NULL = live |
| `ip_address` | TEXT | Optional, for audit |
| `user_agent` | TEXT | Optional, for audit |
| `created_at` | TIMESTAMPTZ NN DEFAULT now() | |

Index: `(user_id)`, `(family_id)`. Rotation on every use (old token
revoked, new token same family). Logout revokes the presented token;
change-password / deactivate / password-reset revoke ALL of the user's
sessions.

### 3.28 `ws_tickets` — single-use WebSocket tickets (Stage 7; guest tickets in Stage 8)

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `user_id` | UUID FK → users.id, nullable | Stage 8: NULL for guest tickets (a guest has no user). NOT NULL before Stage 8 (migration `c8d3e5f6a7b8`). |
| `table_session_id` | UUID FK → table_sessions.id, nullable | Stage 8: set ONLY for guest tickets — the exact session the ticket is bound to. NULL for staff/kitchen tickets. |
| `restaurant_id` | UUID NN FK | |
| `channel` | TEXT NN | `kitchen` \| `staff` \| `guest` (CHECK; `guest` added in Stage 8). |
| `ticket_hash` | TEXT NN UQ | SHA-256 hex of the opaque ticket; the raw ticket is never stored or logged |
| `expires_at` | TIMESTAMPTZ NN | 60 s from issue |
| `used_at` | TIMESTAMPTZ | NULL = unused; set (single-use) at connect |
| `created_at` | TIMESTAMPTZ NN DEFAULT now() | |

Server-side by design: single-use requires server state, so tickets
are NOT signed JWTs in the URL. Validation at connect: hash lookup,
not expired, not used, user still active and still holding the channel
permission, restaurant matches. **Stage 8 (guest tickets):** `user_id`
is NULL; instead the ticket is bound to `table_session_id` — validation
additionally requires the session to exist and still be `active` and its
QR token not revoked/expired, and the ticket's restaurant/branch/table
must match that session. A guest ticket presented to `/ws/kitchen` or
`/ws/staff` (or a staff/kitchen ticket to `/ws/guest`) → 4403.

### 3.29 `tax_configurations` — owner tax config history (Stage 7, D1)

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `restaurant_id` | UUID NN FK | |
| `branch_id` | UUID FK → branches.id, nullable | NULL = restaurant-level (v1: all configs are restaurant-level) |
| `enabled` | BOOLEAN NN | |
| `rate_bps` | INT | CHECK 0–10000; required when enabled |
| `mode` | TEXT NN | `inclusive` \| `exclusive` (CHECK) |
| `label` | TEXT | e.g. `GST` |
| `effective_from` | TIMESTAMPTZ NN | Must be now-or-future at creation |
| `created_by` | UUID FK → users.id, nullable | NULL = bootstrap/system |
| `created_at` | TIMESTAMPTZ NN DEFAULT now() | |

History = rows, never updates. UQ: `(restaurant_id, effective_from)` —
no two configs share an effective_from. The active config at any instant
is the one with the greatest `effective_from` ≤ now. No row = the
restaurant's tax is **unconfigured** — distinct from an explicit 0%
rate (`enabled=true`, `rate_bps=0`).

## 4. What was deliberately *not* created

| Candidate | Why not |
|---|---|
| `roles` / `permissions` tables | v1 has four fixed roles with code-enforced checks. A permission table adds a management UI and a second source of truth for authorisation with no workflow needing it. Revisit if a fifth role or per-user permissions are required. |
| `customers` table | Guests are anonymous table sessions in v1. No accounts, no PII store, no loyalty. Add it only with a loyalty / CRM decision. |
| `reservations` / `waitlist` | No booking flow in v1 (see non-goals). Table `reserved` status covers manual use. |
| `discounts` / `coupons` | No discount workflow decided yet (D2). A bill-level `discount_paise` column covers the v1 case. |
| `inventory` / `stock` | No stock workflow in the brief. Menu availability is the v1 control. |
| `kitchen_print_jobs` | No printer in v1 (assumption A4). |
| `shifts` | `staff_assignments` with `assigned_from/until` covers v1; a full shift/roster table is a later HR feature. |
| `restaurant_events` (event store) | Events are notifications, not the source of truth. A `version` per restaurant is kept on the broadcast path (Stage 8), not as a separate event-sourced store. |

## 5. Relationships — integrity rules

- **Delete rules:** menu item deletion is not a physical delete in v1 — use
  `is_active = false`. Order/bill/payment rows are never deleted. User
  deactivation is `is_active = false`, never a delete (audit history must
  keep pointing at the user). `ON DELETE CASCADE` is used only for
  genuinely owned children (`order_items` under an order, `bill_items` under
  a bill, variants/add-on links under a menu item).
- **Denormalised `table_id` on `orders` / `tickets` / `help_calls`:** always
  copied from the session at creation and validated to match; it exists so
  the floor map and KDS can query by table without joining sessions on every
  poll. If it ever disagrees with the session, the session wins and the
  disagreement is a bug, not a "second truth".
- **Every money column** has a `CHECK (>= 0)` except `price_delta_paise`
  (variant delta) and `discount_paise` (which is stored as a positive amount
  subtracted from the total).

## 6. Status machines

Each machine is enforced in the service layer, in a transaction, against the
current DB state. Illegal transitions return `409 Conflict`. The frontend
never decides a transition is legal — it only offers buttons for transitions
the backend advertises for the current state.

### Tables — `dining_tables.status`

```
available → occupied → cleaning → available
available → reserved → occupied
any → out_of_service → available
```

`occupied` is set when the first order is placed or the session opens
(Stage 2/3 detail: session open marks the table `occupied`); `cleaning` is
set by staff when the session closes; `available` is set by staff when
cleaning is done.

### Table sessions — `table_sessions.status`

```
active → billing_requested → closed
active → closed
active → expired   (timeout, per D5)
```

### Orders — `orders.status`

```
placed → accepted → preparing → ready → served → completed
placed → cancelled          (guest, before accepted — per D8)
accepted/preparing → cancelled   (staff/kitchen only, with reason)
```

> **Stage 4 as implemented (23 Nov 2026):** the kitchen moves an
> order order-level only — placed/accepted → preparing →
> ready — via `PATCH /kitchen/orders/:id/status`. The transition
> is enforced in `kitchen_service.transition_order` against the
> current DB state under a row lock (`SELECT … FOR UPDATE`);
> anything else → 409 with current + requested status. The order
> and its non-cancelled items move together in ONE transaction
> (items queued → preparing on →preparing, →ready on →ready;
> `accepted_at` set if null on →preparing, `ready_at` set on
> →ready; `version` += 1). There is no per-item transition
> endpoint and no separate ticket status to derive — see
> `kitchen_tickets` below. `served`/`completed` are staff-side
> steps (Stage 5), not kitchen steps.
>
> **Stage 5 as implemented (23 Nov 2026):** the staff move is
> ready → served only, via `PATCH /staff/orders/:id/serve`
> (empty body). Enforced in `staff_service.serve_order` against
> the current DB state under a row lock; anything else → 409
> with current + requested status. The order and its
> non-cancelled items move together in ONE transaction (items
> ready → served; `served_at` set; `version` += 1). **Serving
> does NOT close the session and does NOT change the table
> status** — the session stays `active` and the table stays
> `occupied` until Stage 6 (billing/session close). A served
> order leaves both the kitchen active queue and the staff active
> list. `completed` is still a Stage 6 step (bill paid).

`completed` is set when the bill is paid (or when staff explicitly complete a
served order with no bill — Stage 3 detail). Order status is **derived**
from item statuses where possible: `ready` when all non-cancelled items are
`ready`/`served`; the service recomputes it in the same transaction as any
item change.

### Order items — `order_items.status`

```
queued → preparing → ready → served
queued/preparing → cancelled   (authorised roles only, with reason)
```

> **Stage 4 as implemented:** items move only as a consequence of
> their order's transition (see Orders above) or of a guest cancel
> (placed → cancelled cancels the items too). No endpoint moves a
> single item on its own.

### Bills — `bills.status`

```
issued → payment_requested → paid
issued → paid
```

> **Stage 6 as implemented (23 Nov 2026):** a bill is generated from
> the session's non-cancelled orders (`POST /guest/bill`, status
> `issued`). While `issued` its lines are RECOMPUTED from the
> session's current non-cancelled orders on every bill fetch/request
> (one transaction: replace that bill's `bill_items`, recompute
> totals) — except a partially-paid `issued` bill, which does NOT
> regenerate (money already collected), and a no-change regen does
> not bump `version`. `issued → payment_requested` happens on the
> guest's Pay by Cash (`PATCH /guest/bill/request-payment`, which
> creates NO payment row); the bill is then FROZEN — new orders wait
> for a later bill. `payment_requested → paid` (or `issued → paid`)
> happens only through a staff cash confirmation
> (`POST /staff/bills/:id/payments`); a `paid` bill is frozen forever,
> and new orders after it go on a NEW bill containing only order items
> not referenced by any existing `bill_item` for the session. There
> **Stage 7 (24 Nov 2026):** `voided`/`refunded`/
> `partially_refunded` states ARE built now: `issued → voided` and
> `payment_requested → voided` (owner, or manager with `can_void`,
> only with zero confirmed payments — any confirmed payment → 409
> "refund required first"; repeat void → 409); `paid →
> refunded/partially_refunded` via refunds (status recomputed).
> Voided bills do not block a new bill and their items become
> billable again. **Tax freeze point:** the transition to
> `payment_requested` (and payment time, when a bill is paid without
> a prior request) snapshots the applicable tax policy + computed
> tax onto the bill; frozen/paid bills never change afterwards.
> One open bill per session is enforced by the partial unique index
> `UQ (table_session_id) WHERE status IN
> ('issued','payment_requested')`. Session close after all bills are
> paid is still deferred (Stage 5 deferred it, Stage 6 did not add
> it).

### Bill splits — `bill_splits.status`

```
unpaid → payment_requested → paid
unpaid → paid
```

> **Stage 6 as implemented:** splits are created by the guest
> (`POST /guest/bill/splits`, equal or custom, server-validated) and
> replace existing UNPAID splits in one transaction; replacement is
> refused (409) once any split is paid or `payment_requested`. Splits
> on a `payment_requested` bill inherit `payment_requested`. A split
> is `paid` when a staff payment covers its full amount; the bill is
> `paid` when the whole bill (or all splits) are paid. Rule enforced
> in service + test: `SUM(splits) = bills.total_paise` whenever
> splits exist.

### Payments — `payments.status`

```
confirmed   (only state in Stage 6)
```

> **Stage 6 as implemented:** a payment row is created ONLY by staff
> confirming cash (`POST /staff/bills/:id/payments`), in ONE
> transaction with the idempotency claim (Stage 3.2 pattern, scope
> `(restaurant_id, bill_id, endpoint, key)`), the bill/split row lock
> (taken BEFORE the claim insert — see `api-plan.md` § 7.2 for the
> deadlock the other order caused), the amount validation
> (`0 < amount ≤ due on target`, else 422), and the
> `amount_paid_paise` update. The row is append-only: no
> update/delete code path exists. `method` is CHECK-constrained to
> `'cash'`; `collected_by` is NULL (always, until staff auth lands);
> `change_due_paise` is always 0 (D9: no change handling in v1);
> `receipt_number` is `R-YYYY-MM-DD-NNN`, sequential per restaurant
> per day. A second confirmation for the same split with a NEW key
> fails at the status guard (409, existing payment returned); the
> same key + same body replays 201 with `Idempotency-Replayed: true`
> and no WS event.

### Kitchen tickets — `kitchen_tickets.status`

Derived from items (see § 3.15): `queued → preparing → ready → served`,
plus `cancelled` when all its items are cancelled.

> **Stage 4 as implemented: this table does NOT exist and this
> machine does not run.** A "ticket" IS an order + its item
> snapshots (`GET /kitchen/orders` is the queue); the order-level
> machine above is the only kitchen machine. Station
> grouping/filtering is deferred — `menu_items.station` (TEXT)
> already records each item's station for later use.

### Bills — `bills.status`

```
issued → payment_requested → paid
issued → voided
payment_requested → paid
paid → refunded / partially_refunded   (via refunds; status recomputed)
```

### Bill splits — `bill_splits.status`

```
unpaid → payment_requested → paid
```

### Payments — `payments.status`

```
confirmed → refunded   (when fully refunded)
confirmed → partially_refunded (when partly refunded — recomputed)

Payments are NEVER voided — only bills are. (The blueprint's
`confirmed → voided` line was wrong; removed in Stage 7.)
```

### Help calls — `help_calls.status`

```
open → acknowledged → resolved
open → cancelled   (guest or staff)
```

> **Stage 5 as implemented (23 Nov 2026):** open→acknowledged via
> `PATCH /staff/help-calls/:id/acknowledge` (sets
> `acknowledged_at`), acknowledged→resolved via `PATCH
> /staff/help-calls/:id/resolve` (sets `resolved_at`). Resolving
> an open call → 409 — a waiter must acknowledge first. Both
> under a row lock, one transaction each. Acknowledge does NOT
> assign a waiter: no `acknowledged_by`/`resolved_by` columns
> exist (no users system yet). Cancel is not implemented yet.

### Reviews — `reviews.status`

`published` | `held` (per D6).

## 7. Indexes — the ones that matter

Beyond the PK/UQ indexes above, these composite indexes exist because a
named screen queries by them on every load:

| Query | Index |
|---|---|
| KDS queue per station | `(restaurant_id, station_id, status, created_at)` on `kitchen_tickets` |
| Floor map | `(restaurant_id, status)` on `dining_tables` |
| Active orders per restaurant | `(restaurant_id, status, placed_at)` on `orders` |
| Orders per session | `(table_session_id, placed_at)` on `orders` |
| Bills per restaurant per day | `(restaurant_id, issued_at)` on `bills` |
| Cash collected per day | `(restaurant_id, collected_at)` on `payments` |
| Menu by category | `(restaurant_id, category_id, sort_order)` on `menu_items` |
| Audit trail | `(restaurant_id, created_at)` on `audit_logs` |

Do not add an index for a query that does not exist yet. Measure before
adding more.

## 8. `settings` JSONB — rule

`restaurants.settings` holds only small, versioned, documented keys
(e.g. `kds_sla_minutes`, `help_call_sla_minutes`). Anything that needs
querying, constraining, or auditing is a real column. If a setting grows a
workflow, promote it to a column in a migration.

## 9. Tenant isolation strategy

1. **Application scoping (v1 mechanism):** every service function receives a
   `restaurant_id` (from the JWT or the QR token) and every repository query
   filters by it. A query without a restaurant filter is a code-review failure.
2. **Database constraints:** FKs to `restaurants` on every tenant table;
   unique constraints are scoped per restaurant (e.g. table number, menu
   category name).
3. **Tests:** negative tests prove (a) a user from restaurant A cannot read
   restaurant B's orders/bills/tables, (b) a guest token for Table 5 cannot
   read Table 6, (c) a kitchen user cannot call billing endpoints.
4. **Row-Level Security (future hardening):** PostgreSQL RLS with a
   `current_restaurant_id` session variable is a Stage 9 option. It is not
   the v1 mechanism because it adds a second authorisation layer that must
   be kept in sync with the application layer, and the application layer is
   where the tests live. If RLS is added, it is defence-in-depth, not a
   replacement for app scoping.
5. **Onboarding a second restaurant:** insert a `restaurants` row, a branch,
   stations, tables, tokens, menu, and users. No code change, no migration.
   The demo seed (`seed/zaika.py`) is the template for that operation.

## 10. Migrations

- Alembic, one migration per schema change, with a descriptive name.
- Every migration is reversible in development (`downgrade()` implemented).
  Production downgrades are a reviewed manual operation.
- Seed data (Zaika) is **not** a migration — it is a separate seed script
  that can be re-run idempotently. Migrations change schema; seeds change
  demo data. Never mix them.
- No migration may backfill money columns with a guessed value; backfills
  use `0` + a documented follow-up, or the migration is split.
