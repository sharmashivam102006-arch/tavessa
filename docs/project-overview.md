# Tavessa — Project Overview

> Status: Blueprint v1 — 23 Nov 2026. No application code built yet.
> Companion docs: `architecture.md`, `database-design.md`, `api-plan.md`,
> `development-roadmap.md`. The README is the entry point.

## 1. What Tavessa is

Tavessa is one central restaurant system with four role-specific interfaces
sharing the same backend, the same PostgreSQL database, and the same live
event stream. A guest ordering from a phone, a waiter on the floor, a cook at
the KDS screen, and the owner on the dashboard all see the same order, the
same table, and the same bill — at the same time.

**Tagline:** "Every Table. Every Order. In Sync."

**First restaurant:** Zaika (demonstration restaurant).
**Future:** multi-restaurant SaaS. The schema is tenant-ready from day one
(`restaurant_id` on every tenant-scoped table); there is no separate
"multi-tenant v2" rewrite planned. See `database-design.md` § 9.

## 2. The four interfaces

### 2.1 Guest — `/t/:tableToken` (mobile-first, no login)

Opened by scanning the QR code printed for a table. Access is limited to that
table's session — a guest cannot see another table's orders, bills, or session.

| Capability | Notes |
|---|---|
| Restaurant info + menu | Categories, items, images, descriptions, prices (INR). Only available items shown as orderable. |
| Customise | Variants (e.g. size / portion), add-ons, quantity, special instructions per item. |
| Cart + place order | Cart is per table session. Placing an order is idempotent (see `api-plan.md` § 7). |
| Order confirmation + live tracking | Order number, item-level status, overall status. Updates arrive over WebSocket; REST re-fetch is the source of truth on reconnect. |
| Request waiter assistance | One open help call per table at a time (see § 5, step 10). |
| Request the bill | Generates (or re-opens) the bill for the session's unpaid orders. |
| View bill, split it | Equal split or custom amounts. Splits are a presentation of one bill, not separate bills, until payment is recorded per split (see § 4). |
| Pay by Cash | Guest selects cash. This is a *request*, not a payment. Nothing is marked paid. |
| Payment confirmation + receipt | Shown only after staff confirm cash collection. Receipt is printable / viewable. |
| Review after the visit | One review per table session, after the bill is paid. Moderation decision is open — see § 7. |

**Explicitly out of scope for the guest:** seeing other tables, editing the
menu, cancelling an order after the kitchen has accepted it (cancellation
rules are in § 5), paying online, or marking anything paid.

### 2.2 Waiter / Staff — `/staff` (login required)

Optimised for speed on a phone/tablet on the floor: big tap targets, table
status at a glance, one-tap status changes.

| Capability | Notes |
|---|---|
| Staff login | Email/username + password, role `waiter` (or `staff`). JWT. |
| Floor map | All tables with status: available / occupied / reserved / cleaning / out of service. Tap a table for its session, orders, bill. |
| Incoming orders | New orders for assigned tables (and unassigned, per restaurant setting). |
| Help calls | List, accept, resolve. Accepting assigns the call to that waiter. |
| Order details + serving status | Mark items / orders served once food is actually delivered. |
| Bill requests | See which tables requested the bill / cash payment. |
| Cash collection | Record cash collected **after actually collecting it**, then mark the bill (or split) paid. This is the only path to `paid`. |
| Assigned tables + work status | "My tables" view; shift / assignment is recorded, not assumed. |

### 2.3 Kitchen Display System — `/kitchen` (login required)

Optimised for a wall-mounted / counter screen: large readable cards, minimal
interaction, high contrast, timers visible from a distance.

| Capability | Notes |
|---|---|
| Secure kitchen login | Role `kitchen`. A shared kitchen PIN is **not** sufficient — named login so actions are auditable. |
| Live orders, no refresh | New orders appear via WebSocket. Reconnect re-fetches the active queue. |
| Order card | Order number, table number, items, quantities, variants/add-ons, special instructions, time since placed. |
| Stations | Main Kitchen, Tandoor, Beverages (seed for Zaika; configurable per restaurant). An item belongs to exactly one station; a ticket is one order × one station. |
| Item status | `queued → preparing → ready → served`, or `cancelled` where authorised. Item status and order status are separate fields. |
| Order status | Derived from item statuses + explicit kitchen accept; see `database-design.md` § 6. |
| Timers | Elapsed time per ticket, with a configurable "breaching" threshold (e.g. > 15 min) for visual alert. No auto-escalation in v1. |
| Ready notification | When an item/order becomes ready, staff and the guest's session are notified over WebSocket. |

### 2.4 Owner / Admin — `/admin` (login required)

Organised dashboard, not a generic template: live operations first, then money,
then configuration.

| Capability | Notes |
|---|---|
| Restaurant overview | Live occupancy, active orders, orders in kitchen, help calls open, bills awaiting cash. |
| Daily sales + cash | Sales for today, cash collected today, cash still outstanding (bills issued, not yet paid). |
| Menu + category management | Create/edit categories and items, variants, add-ons, availability toggle. Price changes never rewrite history (price snapshots — see § 4). |
| Staff management | Create staff, assign roles, assign tables, activate/deactivate. |
| Order history, bills, payments | Searchable/filterable; every bill links to its orders, splits, payments, refunds. |
| Basic analytics | Sales by day / category / item, busiest hours, average order value, top items. No forecasting in v1. |
| Restaurant settings | Name, tables, stations, opening hours, tax settings (once decided — see § 7), QR token rotation. |

## 3. Users and roles

| Role | Who | Login | Can |
|---|---|---|---|
| `owner` / `admin` | Restaurant owner / manager | Password + JWT | Everything in § 2.4, plus staff management |
| `waiter` | Floor staff | Password + JWT | § 2.2 |
| `kitchen` | Kitchen staff | Password + JWT | § 2.3 |
| `guest` | Customer at a table | QR token (no password) | § 2.1, scoped to one table session only |

One user account has one role per restaurant in v1. A person who works at two
restaurants has two memberships, not a shared cross-tenant account. Roles and
permissions are enforced server-side on every endpoint, not just by hiding
navigation in the frontend. See `architecture.md` § 8.

Staff "assignments" (`staff_assignments`) record which waiter is responsible
for which tables during a shift; they are data, not a separate role.

## 4. Billing and payment rules — cash only

This is the most consequential product rule in v1. It is repeated here
deliberately, because getting it wrong is a money bug, not a UI bug.

1. **Cash only.** No UPI, Razorpay, Stripe, Paytm, gateway, or online payment
   processing. Do not add any of these in v1.
2. **Bill generation.** A bill is generated from the session's unpaid orders.
   Line items are copied from order items at their snapshot prices. The bill
   total is computed on the backend. Frontend totals are display-only.
3. **Price snapshots.** `order_items` store `unit_price_paise`,
   `item_name_snapshot`, `variant_name_snapshot` at order time. A later menu
   price change never alters an old order or bill. `bill_items` snapshot the
   same values again at bill time, so a bill is self-contained.
4. **Splitting.** Equal split divides the bill total into N parts (integer
   paise, remainder assigned deterministically to the first split — see
   `database-design.md` § 6); custom split takes explicit paise amounts per
   part and must sum exactly to the bill total. Splits are parts of one bill.
5. **Pay by Cash is a request.** The guest tapping "Pay by Cash" creates a
   `payment_requested` state on the bill and notifies staff. It creates no
   payment record and changes no paid flag.
6. **Only staff confirm payment.** A waiter (or owner) confirms cash collection
   only after actually collecting the cash, creating a `payments` row
   (`method = cash`, `collected_by`, `collected_at`, amount in paise) and
   moving the bill (or split) to `paid`. The confirmation is audited
   (`audit_logs`).
7. **Receipts.** A receipt is generated from the paid bill + payment record:
   restaurant, table, order numbers, items, totals, amount paid, change due
   if any, collected-by, timestamp. Receipts are read-only once issued.
8. **Refunds.** Only an authorised role (owner/admin in v1) records a refund,
   against a paid bill/payment, with a reason. A refund creates a `refunds`
   row and adjusts the bill to `refunded` / `partially_refunded`; it never
   deletes the payment. Refund approver and limits are an open decision (§ 7).
9. **Daily cash report.** Cash collected per day, per collector, outstanding
   bills, refunds — reconcilable against the till. The owner dashboard shows
   this; it is not a guess from frontend state.

### Worked example — Table 5, Zaika

Two guests share a ₹1,000 bill (₹600 food + ₹400 food, tax treatment TBD).
Equal split: ₹500 / ₹500. Guest A pays ₹500 cash to the waiter; the waiter
confirms ₹500 against split 1 — split 1 is `paid`, split 2 remains
`payment_requested`. Guest B pays ₹500; the waiter confirms — split 2 `paid`,
bill `paid`, receipt issued. If Guest B instead pays ₹600, the extra ₹100 is
recorded as change due / overpayment per the recorded rule, not silently
absorbed — the exact handling is a Stage 6 implementation detail to confirm
against the tax/rounding decision.

## 5. Main business workflow (the golden path)

The 18-step flow from the brief, with the system behaviour at each step:

| # | Actor | Step | System behaviour |
|---|---|---|---|
| 1 | Guest | Scans QR at Table 5 | Token validated server-side; a `table_sessions` row is created (or the active session resumed); guest scope = Zaika + Table 5 only. |
| 2 | Guest | Zaika menu opens | Menu fetched for that restaurant; unavailable items marked, not hidden silently (availability is explicit). |
| 3 | Guest | Selects items + customisations | Cart held client-side (Zustand) until order placement; prices shown are display-only. |
| 4 | Guest | Places order | `POST /orders` with `Idempotency-Key`. Backend validates restaurant, table session, item availability, variant/add-on validity, recomputes every price. |
| 5 | Backend | Validates + stores | Order + items + per-station kitchen tickets created in **one DB transaction**. Table becomes `occupied` if it was not. Then — and only then — events are broadcast. |
| 6 | Kitchen | Receives order, no refresh | WebSocket `order.created` → KDS queue; ticket cards appear grouped by station. |
| 7 | Kitchen | Accepts + prepares | Ticket/item status `queued → preparing`. Elapsed timer starts at `placed_at`. |
| 8 | Kitchen | Updates item / order status | Item `ready` → staff + guest notified (`order.item_ready`, `order.ready`). Order status derived, not set by hand-waving. |
| 9 | Guest | Sees real-time progress | Guest WebSocket channel for that table session receives status events; UI re-fetches on reconnect. |
| 10 | Guest | Requests waiter assistance | `POST /help-calls`. One open call per table. Waiters see it on floor map + list; accepting assigns it. |
| 11 | Waiter | Receives + handles request | Waiter accepts → `acknowledged`; visits table; resolves → `resolved`. Guest sees acknowledgement. |
| 12 | Guest | Requests the bill | `POST /bills` from session's unpaid orders. Bill generated in a transaction; status `issued`. |
| 13 | System | Generates the bill | Line items snapshot prices; totals computed server-side; splits (if requested) validated to sum to total. |
| 14 | Guest | Selects Pay by Cash | Bill → `payment_requested`; staff notified. No payment row yet. |
| 15 | Waiter | Collects cash | Waiter goes to the table, collects the amount, counts it. |
| 16 | Waiter | Confirms collection | `POST /bills/{id}/payments` (staff auth, amount validated against bill/split). Payment row created; bill/split → `paid`; audit log written; receipt generated. |
| 17 | System | Records payment + receipt | Receipt available to guest + owner. Table session can close once all bills are paid. |
| 18 | Owner | Dashboard updates | Sales, cash collected, occupancy update via the same event stream; dashboard re-fetches aggregates on reconnect. |

**Failure branches (designed, not improvised later):**

- *Duplicate tap on "Place order":* same `Idempotency-Key` returns the original
  order (200/201 with the same `order_id`), no second order, no second kitchen
  ticket. See `api-plan.md` § 7.
- *Kitchen cancels an item:* item → `cancelled` (authorised roles only),
  order total recomputed, bill (if already issued) must be regenerated or
  adjusted — Stage 4/6 rule: **a bill cannot be paid while it contains a
  cancelled-but-not-adjusted line**. Implementation detail in Stage 6.
- *Guest closes the browser mid-order:* session persists server-side; reopening
  the same QR token resumes the session and re-fetches state.
- *Waiter marks served before kitchen says ready:* not permitted — serving
  requires item `ready` (enforced server-side).
- *Two waiters confirm the same cash payment:* payment confirmation is
  transactional on the bill/split row (row lock / status guard); the second
  attempt fails with `409 Conflict` and returns the existing payment.
- *Bill requested, then guest orders more:* new order attaches to the session;
  the bill must be regenerated (or a second bill issued) — v1 rule: **one open
  bill per session; adding an order after bill issue voids/regenerates the
  draft bill** (finalise in Stage 6).

## 6. Non-goals for v1

- Online payments, wallets, or any gateway integration.
- Table reservations / waitlist (table `reserved` status exists for manual use,
  but there is no public booking flow).
- Delivery / takeaway ordering (dine-in table sessions only).
- Loyalty, coupons, or discounts beyond a simple bill-level adjustment recorded
  by the owner (discount handling is an open decision — see § 7).
- Multi-branch franchising UI (branches exist in the schema for future use;
  v1 operates one branch per restaurant).
- Forecasting, demand prediction, or AI features.
- Native mobile apps (responsive web only).
- Microservices, Kubernetes, or a separate realtime service (see
  `architecture.md` § 6 for why Redis is deferred).

## 7. Open decisions / assumptions (to confirm before or during Stage 1)

These are **not** decided by this blueprint. Each needs a recorded answer
before the stage that depends on it.

| # | Decision | Why it matters | Needed by |
|---|---|---|---|
| D1 | Tax: are menu prices GST-inclusive or exclusive? Is there a service charge? How is rounding done (per line / per bill)? | Changes every total, bill, receipt, and report. | Stage 3 (orders) / Stage 6 (billing) |
| D2 | Discounts: any bill-level discount / complimentary item in v1? Who can apply it? | Affects bill totals and audit. | Stage 6 |
| D3 | Refund authority: owner only, or owner + manager? Any amount limit requiring owner approval? | Affects roles and payment endpoints. | Stage 6 |
| D4 | QR printing: who prints table QR codes, what size/format, and how are tokens rotated if a code is photographed/shared? | Affects token expiry + rotation UX. | Stage 2 |
| D5 | Table session expiry: how long does a guest session live after inactivity? What closes a session (bill paid + explicit close, timeout, staff close)? | Affects session lifecycle + table freeing. | Stage 2 |
| D6 | Review moderation: are reviews published immediately, or held for owner approval? One review per session — can it be edited? | Affects review endpoints + admin UI. | Stage 2 / 7 |
| D7 | KDS hardware: what device/screen will the kitchen use? Is there a backup if the KDS screen goes offline? | Affects KDS layout, offline handling, printer need. | Stage 4 |
| D8 | Order cancellation window: until when can a guest cancel an order/item (before kitchen accepts only)? Who can cancel after that? | Affects order status machine. | Stage 3 |
| D9 | Split remainder + change: equal-split remainder goes to first split; overpayment/change handling. | Affects payment confirmation. | Stage 6 |
| D10 | Restaurant settings: opening hours, table count for Zaika, station list, help-call SLA — seed values for Zaika. | Needed for realistic seed data. | Stage 1 |

**Assumptions made in this blueprint (challenge them in review):**

- A1: Currency is INR, amounts in paise, Zaika is in India (implied by
  Tandoor station, cash-first operation, and the brief's context).
- A2: One branch per restaurant in v1; `branches` exists but is not exposed
  as a separate management surface.
- A3: Kitchen staff are named users (auditable), not a shared PIN.
- A4: No kitchen printer in v1 — KDS screen is the ticket. If a printer is
  required, it is a Stage 4 addition, not a redesign.
- A5: Guest devices are modern smartphones with a camera for QR scanning;
  the QR itself is printed, not generated on the guest's phone.
