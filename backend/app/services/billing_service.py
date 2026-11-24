"""Billing service — bills, splits, cash payments, receipts
(api-plan.md § 7 as built in Stage 6).

Money rules (integer paise, server-side only):
- A bill is built from the session's NON-CANCELLED orders and
  NON-CANCELLED order items, using the order-item snapshots (name /
  variant / add-ons / unit price) — NEVER the current menu price
  or name. Line total = unit x quantity; subtotal = sum of lines;
  tax / service charge / discount are 0 (decision D1 undecided);
  total = subtotal.
- Bill numbers ``B-YYYY-MM-DD-NNN`` (per restaurant per day) and
  receipt numbers ``R-YYYY-MM-DD-NNN`` (per restaurant per day)
  are allocated under a row lock on the restaurant, the same
  pattern as order numbers — concurrent creations serialise.

Bill regeneration (Stage 6 decision 8, recorded in the roadmap):
- While a bill is ``issued`` AND nothing has been paid on it, its
  lines are RECOMPUTED from the session's current billable items
  on every fetch/request (one transaction: replace its bill_items,
  recompute totals). This is how a guest cancellation after
  billing is reflected, and how new orders join the open bill.
  Items already on ANOTHER bill (e.g. a paid bill) are never
  re-billed. If the recomputed total changes, existing unpaid
  splits are dropped (they summed to the old total).
- Once the bill is ``payment_requested`` it is FROZEN. A ``paid``
  bill is frozen forever. New orders after a paid bill go on the
  NEXT bill, which contains only items not referenced by any
  existing bill_item for the session.
- A partially-paid ``issued`` bill does NOT regenerate either:
  money has already been collected against its lines, so lowering
  its total could put amount_paid above the total. (Recorded here
  and in the roadmap; the lazy-regen rule covers the no-payment
  case, which is the normal one.)
- If regeneration leaves zero billable lines (everything was
  cancelled), the issued bill is deleted — there is nothing to bill.

Payments (cash only, staff only):
- ONE transaction, row lock on the bill (+ the restaurant row for
  the receipt number). A payment must be > 0 and <= the amount due
  on its target (whole bill outstanding, or split outstanding),
  else 422. ``change_due_paise`` is stored as 0 always (decision
  D9: no change handling in v1). ``collected_by`` is always NULL
  (decision 5: no users system yet). A bill with splits is paid
  split-by-split; a bill without splits is one whole-bill target.
- Idempotency reuses the Stage 3.2 single-transaction claim
  pattern (api-plan.md § 10): claim + payment + stored response in
  ONE transaction, scoped by (restaurant_id, bill_id, endpoint,
  key) in the shared ``idempotency_keys`` table (payment claims
  have table_session_id NULL — see models/idempotency.py). Same key
  + same body replays the stored response with
  ``Idempotency-Replayed: true``; same key + different body is a
  422; a failed payment leaves NO claim row, so a corrected retry
  proceeds. A second confirmation for the same split with a NEW
  key hits the status guard and 409s with the existing payment.
- The payments table is append-only in this stage: no
  update/delete code path exists for it (decision 4 — the audit
  trail IS the payments table).

Broadcasting is NOT done here: the service stays broadcast-free so
tests can call it without a WebSocket, and so no event can ever be
published from inside (or before) a transaction. Routes broadcast
after the service returns successfully (architecture.md § 3.2).
Event names are the brief's list (Stage 6 decision 3):
``bill.created`` / ``bill.requested`` / ``payment.recorded`` /
``bill.paid`` — api-plan.md § 7's older ``bill.payment_requested``
and ``payment.confirmed`` are NOT used.
"""
import hashlib
import json
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import case, delete, func, select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.core.errors import ApiError
from app.services.audit_service import write_audit
from app.models import (
    Bill,
    BillItem,
    BillSplit,
    Branch,
    DiningTable,
    IdempotencyKey,
    Order,
    OrderItem,
    OrderItemAddon,
    Payment,
    Restaurant,
    TableSession,
)
from app.schemas.billing import (
    BillItemOut,
    BillOut,
    BillSplitOut,
    PaymentCreateRequest,
    PaymentOut,
    PaymentResultOut,
    ReceiptOut,
    SplitCreateRequest,
    StaffBillListOut,
    StaffBillSummaryOut,
)
from app.schemas.common import Pagination
from app.services.kitchen_service import resolve_restaurant
from app.services.order_service import (
    IDEMPOTENCY_TTL,
    _canonical_hash,
    _delete_claim_row,
    _is_expired,
    _is_lock_timeout,
    _set_lock_timeout,
)
from app.services.table_service import GuestContext, _now

PAYMENT_ENDPOINT = "POST /api/v1/staff/bills/payments"
PAYMENT_LOCK_TIMEOUT_SECONDS = 10
PAYMENT_MAX_CLAIM_ATTEMPTS = 2

TAX_STATUS_TEXT = (
    "Tax: not configured — no tax rate has been set for this restaurant"
)


# ----------------------------------------------------------------------
# Serialisers
# ----------------------------------------------------------------------


def _bill_out(db: Session, bill: Bill) -> BillOut:
    table = db.get(DiningTable, bill.table_id)
    items = (
        db.execute(
            select(BillItem)
            .where(BillItem.bill_id == bill.id)
            .order_by(BillItem.created_at)
        )
        .scalars()
        .all()
    )
    splits = (
        db.execute(
            select(BillSplit)
            .where(BillSplit.bill_id == bill.id)
            .order_by(BillSplit.created_at)
        )
        .scalars()
        .all()
    )
    payments = (
        db.execute(
            select(Payment)
            .where(Payment.bill_id == bill.id)
            .order_by(Payment.collected_at)
        )
        .scalars()
        .all()
    )
    order_ids = [i.order_id for i in items if i.order_id is not None]
    order_numbers: list[str] = []
    if order_ids:
        seen: set[UUID] = set()
        for oid in order_ids:
            if oid in seen:
                continue
            seen.add(oid)
            order = db.get(Order, oid)
            if order is not None:
                order_numbers.append(order.order_number)
    return BillOut(
        id=bill.id,
        bill_number=bill.bill_number,
        table_session_id=bill.table_session_id,
        table_id=bill.table_id,
        table_number=table.number if table is not None else 0,
        status=bill.status,
        items=[
            BillItemOut(
                id=i.id,
                order_id=i.order_id,
                order_item_id=i.order_item_id,
                name=i.item_name_snapshot,
                variant_name=i.variant_name_snapshot,
                add_on_names=list(i.add_on_names_snapshot or []),
                quantity=i.quantity,
                unit_price_paise=i.unit_price_paise,
                total_paise=i.line_total_paise,
            )
            for i in items
        ],
        splits=[
            BillSplitOut(
                id=s.id,
                label=s.label,
                amount_paise=s.amount_paise,
                status=s.status,
                paid_at=s.paid_at,
            )
            for s in splits
        ],
        payments=[
            PaymentOut(
                id=p.id,
                bill_id=p.bill_id,
                bill_split_id=p.bill_split_id,
                method=p.method,
                amount_paise=p.amount_paise,
                change_due_paise=p.change_due_paise,
                collected_by=p.collected_by,
                collected_at=p.collected_at,
                receipt_number=p.receipt_number,
                status=p.status,
                note=p.note,
            )
            for p in payments
        ],
        order_numbers=order_numbers,
        subtotal_paise=bill.subtotal_paise,
        tax_paise=bill.tax_paise,
        service_charge_paise=bill.service_charge_paise,
        discount_paise=bill.discount_paise,
        total_paise=bill.total_paise,
        amount_paid_paise=bill.amount_paid_paise,
        # A voided bill is written off — nothing is owed on it.
        outstanding_paise=(
            0 if bill.status == "voided"
            else max(0, bill.total_paise - bill.amount_paid_paise)
        ),
        issued_at=bill.issued_at,
        payment_requested_at=bill.payment_requested_at,
        paid_at=bill.paid_at,
        version=bill.version,
        voided_at=bill.voided_at,
        void_reason=bill.void_reason,
        tax_rate_bps_snapshot=bill.tax_rate_bps_snapshot,
        tax_mode_snapshot=bill.tax_mode_snapshot,
        tax_label_snapshot=bill.tax_label_snapshot,
    )


def _payment_out(payment: Payment) -> PaymentOut:
    return PaymentOut(
        id=payment.id,
        bill_id=payment.bill_id,
        bill_split_id=payment.bill_split_id,
        method=payment.method,
        amount_paise=payment.amount_paise,
        change_due_paise=payment.change_due_paise,
        collected_by=payment.collected_by,
        collected_at=payment.collected_at,
        receipt_number=payment.receipt_number,
        status=payment.status,
        note=payment.note,
    )


def build_receipt(db: Session, bill: Bill) -> ReceiptOut:
    """Receipt data for a bill. Callers enforce the paid-only rule
    (409 until paid); this builder just renders the stored state."""
    restaurant = db.get(Restaurant, bill.restaurant_id)
    branch = db.get(Branch, bill.branch_id) if bill.branch_id else None
    table = db.get(DiningTable, bill.table_id)
    out = _bill_out(db, bill)
    # Already ordered by collected_at in _bill_out; do not re-sort
    # here — a pre-commit Payment carries an aware datetime while
    # rows loaded from SQLite are naive, and sorting would crash.
    payments = out.payments
    if bill.tax_label_snapshot:
        rate = bill.tax_rate_bps_snapshot
        tax_status = (
            f"Tax: {bill.tax_label_snapshot}"
            + (f" {rate / 100:.2f}%" if rate is not None else "")
            + (f" ({bill.tax_mode_snapshot})" if bill.tax_mode_snapshot else "")
            + " — as configured when this bill was frozen"
        )
    else:
        tax_status = TAX_STATUS_TEXT
    return ReceiptOut(
        restaurant_name=restaurant.name if restaurant else "",
        branch_name=branch.name if branch else None,
        bill_number=bill.bill_number,
        receipt_number=payments[-1].receipt_number if payments else "",
        receipt_numbers=[p.receipt_number for p in payments],
        table_number=out.table_number,
        order_numbers=out.order_numbers,
        items=out.items,
        subtotal_paise=bill.subtotal_paise,
        tax_paise=bill.tax_paise,
        tax_status=tax_status,
        tax_rate_bps_snapshot=bill.tax_rate_bps_snapshot,
        tax_mode_snapshot=bill.tax_mode_snapshot,
        tax_label_snapshot=bill.tax_label_snapshot,
        total_paise=bill.total_paise,
        amount_received_paise=bill.amount_paid_paise,
        outstanding_paise=max(0, bill.total_paise - bill.amount_paid_paise),
        payment_method="Cash",
        payment_timestamps=[p.collected_at for p in payments],
        receipt_timestamp=bill.paid_at,
        bill_id=bill.id,
    )


# ----------------------------------------------------------------------
# Billable lines + numbering
# ----------------------------------------------------------------------


def _billable_order_items(
    db: Session, session_id: UUID, exclude_bill_id: UUID | None = None
) -> list[OrderItem]:
    """Non-cancelled order items of the session's non-cancelled
    orders that are not already on another bill. ``exclude_bill_id``
    is the bill being regenerated — its own lines do not count as
    "already billed". A fresh bill excludes items on ALL existing
    bills (that is how a paid bill's items stay off the next bill)."""
    billed = (
        select(BillItem.order_item_id)
        .join(Bill, Bill.id == BillItem.bill_id)
        .where(
            Bill.table_session_id == session_id,
            BillItem.order_item_id.is_not(None),
            # Stage 7: a VOIDED bill's items are billable again —
            # voiding un-references them (the bill is kept, not
            # deleted, but its items no longer block a new bill).
            Bill.status != "voided",
        )
    )
    if exclude_bill_id is not None:
        billed = billed.where(Bill.id != exclude_bill_id)
    return (
        db.execute(
            select(OrderItem)
            .join(Order, Order.id == OrderItem.order_id)
            .where(
                Order.table_session_id == session_id,
                Order.status != "cancelled",
                OrderItem.status != "cancelled",
                OrderItem.id.not_in(billed),
            )
            .order_by(Order.placed_at, OrderItem.created_at)
        )
        .scalars()
        .all()
    )


def _next_bill_number(db: Session, restaurant: Restaurant) -> str:
    """``B-YYYY-MM-DD-NNN`` — sequential per restaurant per day,
    allocated under a row lock on the restaurant (same pattern as
    order numbers)."""
    locked = (
        db.execute(
            select(Restaurant)
            .where(Restaurant.id == restaurant.id)
            .with_for_update()
        )
        .scalars()
        .first()
    )
    today = _now().date()
    day_start = datetime(today.year, today.month, today.day, tzinfo=timezone.utc)
    count = db.execute(
        select(func.count()).select_from(Bill).where(
            Bill.restaurant_id == restaurant.id,
            Bill.issued_at >= day_start,
        )
    ).scalar_one()
    return f"B-{today.isoformat()}-{count + 1:03d}"


def _next_receipt_number(db: Session, restaurant: Restaurant) -> str:
    """``R-YYYY-MM-DD-NNN`` — sequential per restaurant per day.
    Caller must already hold the restaurant row lock (the payment
    transaction locks the bill first, then the restaurant here)."""
    today = _now().date()
    day_start = datetime(today.year, today.month, today.day, tzinfo=timezone.utc)
    count = db.execute(
        select(func.count()).select_from(Payment).where(
            Payment.restaurant_id == restaurant.id,
            Payment.collected_at >= day_start,
        )
    ).scalar_one()
    return f"R-{today.isoformat()}-{count + 1:03d}"


def _build_lines(db: Session, bill: Bill, items: list[OrderItem]) -> int:
    """Replace the bill's lines from order-item snapshots and
    recompute totals. Returns the new subtotal. Tax follows the
    config effective NOW (Stage 7, D1): while the bill is issued
    it regenerates with the current config; at the freeze point
    the snapshot columns are set and totals stop moving."""
    from app.services.tax_service import active_tax_config, compute_tax

    db.execute(delete(BillItem).where(BillItem.bill_id == bill.id))
    subtotal = 0
    for item in items:
        add_ons = (
            db.execute(
                select(OrderItemAddon).where(
                    OrderItemAddon.order_item_id == item.id
                )
            )
            .scalars()
            .all()
        )
        line_total = item.unit_price_paise * item.quantity
        subtotal += line_total
        db.add(BillItem(
            bill_id=bill.id,
            order_item_id=item.id,
            order_id=item.order_id,
            item_name_snapshot=item.name_snapshot,
            variant_name_snapshot=item.variant_name_snapshot,
            add_on_names_snapshot=[a.name_snapshot for a in add_ons],
            unit_price_paise=item.unit_price_paise,
            quantity=item.quantity,
            line_total_paise=line_total,
        ))
    bill.subtotal_paise = subtotal
    tax_paise, total = compute_tax(
        subtotal, active_tax_config(db, bill.restaurant_id)
    )
    bill.tax_paise = tax_paise
    bill.service_charge_paise = 0
    bill.discount_paise = 0
    bill.total_paise = total
    return subtotal


def freeze_tax_on_bill(db: Session, bill: Bill) -> None:
    """Snapshot the tax policy + computed tax onto the bill at the
    freeze point. Idempotent: a bill that already has a snapshot
    (or was frozen unconfigured) is left alone — later config
    changes never alter a frozen/paid bill."""
    from app.services.tax_service import (
        active_tax_config,
        compute_tax,
        tax_snapshot_fields,
    )

    if bill.status != "issued":
        # Only an issued bill can be frozen: a payment_requested
        # bill is already frozen (even when it froze unconfigured,
        # whose snapshot columns are NULL by design), and terminal
        # bills never move again.
        return
    config = active_tax_config(db, bill.restaurant_id)
    tax_paise, total = compute_tax(bill.subtotal_paise, config)
    bill.tax_paise = tax_paise
    bill.total_paise = total
    for key, value in tax_snapshot_fields(config).items():
        setattr(bill, key, value)


def _regenerate_issued_bill(db: Session, bill: Bill) -> Bill | None:
    """Recompute an ``issued`` bill with no payments from the
    session's current billable items (decision 8). Returns the bill,
    or None when nothing is billable any more — the bill is deleted
    in that case. Commits. Only called for issued bills with
    amount_paid == 0."""
    items = _billable_order_items(db, bill.table_session_id, exclude_bill_id=bill.id)
    if not items:
        db.delete(bill)
        db.commit()
        return None
    old_total = bill.total_paise
    _build_lines(db, bill, items)
    if bill.total_paise != old_total:
        # Existing splits summed to the old total; drop them rather
        # than leave a bill whose splits no longer sum to its total.
        db.execute(
            delete(BillSplit).where(
                BillSplit.bill_id == bill.id,
                BillSplit.status == "unpaid",
            )
        )
        # The bill genuinely changed: bump the version so a client
        # holding the old bill can tell. A no-change regeneration
        # (the common re-fetch) leaves the version alone.
        bill.version += 1
    db.commit()
    db.refresh(bill)
    return bill


def _open_bill(db: Session, session_id: UUID) -> Bill | None:
    return (
        db.execute(
            select(Bill).where(
                Bill.table_session_id == session_id,
                Bill.status.in_(("issued", "payment_requested")),
            )
        )
        .scalars()
        .first()
    )


def _most_recent_paid_bill(db: Session, session_id: UUID) -> Bill | None:
    return (
        db.execute(
            select(Bill)
            .where(
                Bill.table_session_id == session_id,
                Bill.status.in_(("paid", "refunded", "partially_refunded")),
            )
            .order_by(Bill.paid_at.desc(), Bill.issued_at.desc())
        )
        .scalars()
        .first()
    )


# ----------------------------------------------------------------------
# Guest
# ----------------------------------------------------------------------


def get_or_create_bill(db: Session, ctx: GuestContext) -> tuple[BillOut, bool]:
    """POST /guest/bill — create-or-retrieve for the active session.

    Returns (bill, created): created=True only when a NEW bill was
    generated (the router broadcasts ``bill.created`` then). An open
    ``issued`` bill regenerates (decision 8); an open
    ``payment_requested`` bill is returned frozen; a paid bill plus
    new billable items yields a NEW bill. No billable items and no
    bill -> 409 ``nothing_to_bill``."""
    open_bill = _open_bill(db, ctx.session.id)
    if open_bill is not None:
        if open_bill.status == "payment_requested":
            return _bill_out(db, open_bill), False
        if open_bill.amount_paid_paise > 0:
            # Partially paid: frozen (see module docstring).
            return _bill_out(db, open_bill), False
        regenerated = _regenerate_issued_bill(db, open_bill)
        if regenerated is not None:
            return _bill_out(db, regenerated), False
        # Regeneration emptied the bill and deleted it — fall through
        # to the creation path below.
    items = _billable_order_items(db, ctx.session.id)
    if not items:
        raise ApiError(
            409,
            "nothing_to_bill",
            "There is nothing to bill yet — place an order first.",
            {"table_session_id": str(ctx.session.id)},
        )
    bill = Bill(
        restaurant_id=ctx.restaurant.id,
        branch_id=ctx.session.branch_id,
        table_session_id=ctx.session.id,
        table_id=ctx.session.table_id,
        bill_number=_next_bill_number(db, ctx.restaurant),
        status="issued",
        subtotal_paise=0,
        tax_paise=0,
        service_charge_paise=0,
        discount_paise=0,
        total_paise=0,
        amount_paid_paise=0,
        issued_at=_now(),
    )
    db.add(bill)
    db.flush()
    _build_lines(db, bill, items)
    db.commit()
    db.refresh(bill)
    return _bill_out(db, bill), True


def get_bill_for_guest(db: Session, ctx: GuestContext) -> BillOut:
    """GET /guest/bill — the open bill (regenerated if issued), else
    the most recent paid bill (receipt view); none -> 404
    ``bill_not_found``."""
    open_bill = _open_bill(db, ctx.session.id)
    if open_bill is not None:
        if open_bill.status == "issued" and open_bill.amount_paid_paise == 0:
            regenerated = _regenerate_issued_bill(db, open_bill)
            if regenerated is not None:
                return _bill_out(db, regenerated)
            # Emptied + deleted — fall through to the paid bill.
        else:
            return _bill_out(db, open_bill)
    paid = _most_recent_paid_bill(db, ctx.session.id)
    if paid is not None:
        return _bill_out(db, paid)
    raise ApiError(404, "bill_not_found", "No bill exists for this table yet.")


def request_payment(db: Session, ctx: GuestContext) -> tuple[BillOut, bool]:
    """PATCH /guest/bill/request-payment — issued ->
    payment_requested (its unpaid splits too). Creates NO payment
    row. Already payment_requested -> 200 idempotent return
    (transitioned=False, so the router broadcasts nothing twice).
    Paid -> 409; no bill -> 404."""
    bill = _open_bill(db, ctx.session.id)
    if bill is None:
        if _most_recent_paid_bill(db, ctx.session.id) is not None:
            raise ApiError(
                409, "conflict", "This bill is already paid.",
                {"status": "paid"},
            )
        raise ApiError(404, "bill_not_found", "No bill exists for this table yet.")
    if bill.status == "payment_requested":
        return _bill_out(db, bill), False
    # Stage 7 freeze point: snapshot the tax policy + computed
    # tax onto the bill now, BEFORE the status flips (freeze only
    # applies to an issued bill); later config changes never alter it.
    freeze_tax_on_bill(db, bill)
    bill.status = "payment_requested"
    bill.payment_requested_at = _now()
    bill.version += 1
    for split in bill.splits:
        if split.status == "unpaid":
            split.status = "payment_requested"
    db.commit()
    db.refresh(bill)
    return _bill_out(db, bill), True


def create_splits(
    db: Session, ctx: GuestContext, request: SplitCreateRequest
) -> BillOut:
    """POST /guest/bill/splits — equal or custom, validated
    server-side. Replaces existing UNPAID splits in one transaction;
    refused once any split is paid or payment_requested -> 409.
    Only while the bill is issued/payment_requested."""
    bill = _open_bill(db, ctx.session.id)
    if bill is None:
        raise ApiError(404, "bill_not_found", "No bill exists for this table yet.")
    for split in bill.splits:
        if split.status in ("paid", "payment_requested"):
            raise ApiError(
                409,
                "conflict",
                "Splits can no longer be changed — a split is already "
                f"{split.status}.",
                {"split_status": split.status},
            )
    if request.mode == "equal":
        if request.parts is None:
            raise ApiError(
                422, "validation_failed",
                "Equal split needs 'parts' (>= 2).", {"field": "parts"},
            )
        n = request.parts
        base = bill.total_paise // n
        remainder = bill.total_paise % n
        amounts = [base + (1 if i < remainder else 0) for i in range(n)]
    else:
        amounts = request.amounts_paise
        if amounts is None or len(amounts) < 2:
            raise ApiError(
                422, "validation_failed",
                "Custom split needs at least 2 amounts.", {"field": "amountsPaise"},
            )
        if any(not isinstance(a, int) or a <= 0 for a in amounts):
            raise ApiError(
                422, "validation_failed",
                "Each split amount must be a whole amount above ₹0.",
                {"field": "amountsPaise"},
            )
        if sum(amounts) != bill.total_paise:
            raise ApiError(
                422, "split_sum_mismatch",
                "The split amounts do not add up to the bill total.",
                {
                    "field": "amountsPaise",
                    "split_sum_paise": sum(amounts),
                    "total_paise": bill.total_paise,
                },
            )
    db.execute(delete(BillSplit).where(BillSplit.bill_id == bill.id))
    split_status = "unpaid" if bill.status == "issued" else "payment_requested"
    for idx, amount in enumerate(amounts):
        db.add(BillSplit(
            bill_id=bill.id,
            label=f"Split {idx + 1}",
            amount_paise=amount,
            status=split_status,
        ))
    bill.version += 1
    db.commit()
    db.refresh(bill)
    return _bill_out(db, bill)


def get_receipt_for_guest(db: Session, ctx: GuestContext) -> ReceiptOut:
    """GET /guest/bill/receipt — only after the bill is paid, else
    409 ``receipt_not_available`` (a receipt is never issued as
    paid before the backend confirms a cash payment)."""
    bill = _open_bill(db, ctx.session.id) or _most_recent_paid_bill(
        db, ctx.session.id
    )
    if bill is None:
        raise ApiError(404, "bill_not_found", "No bill exists for this table yet.")
    if bill.status not in ("paid", "refunded", "partially_refunded"):
        raise ApiError(
            409,
            "receipt_not_available",
            "The receipt is available only after the bill is paid.",
            {"status": bill.status},
        )
    return build_receipt(db, bill)


# ----------------------------------------------------------------------
# Staff
# ----------------------------------------------------------------------

BILL_FILTERS: dict[str, tuple[str, ...]] = {
    "issued": ("issued",),
    "payment_requested": ("payment_requested",),
    "paid": ("paid",),
}


def _staff_bill_row(db: Session, bill: Bill) -> StaffBillListOut:
    table = db.get(DiningTable, bill.table_id)
    order_count = (
        db.execute(
            select(func.count(func.distinct(BillItem.order_id))).where(
                BillItem.bill_id == bill.id,
                BillItem.order_id.is_not(None),
            )
        ).scalar_one()
    )
    return StaffBillListOut(
        id=bill.id,
        bill_number=bill.bill_number,
        table_id=bill.table_id,
        table_number=table.number if table is not None else 0,
        table_session_id=bill.table_session_id,
        status=bill.status,
        total_paise=bill.total_paise,
        amount_paid_paise=bill.amount_paid_paise,
        outstanding_paise=max(0, bill.total_paise - bill.amount_paid_paise),
        order_count=order_count,
        issued_at=bill.issued_at,
        payment_requested_at=bill.payment_requested_at,
    )


def open_bill_summary(db: Session, session_id: UUID) -> StaffBillSummaryOut | None:
    """The table's open bill summary for GET /staff/tables/:id."""
    bill = _open_bill(db, session_id)
    if bill is None:
        return None
    return StaffBillSummaryOut(
        id=bill.id,
        bill_number=bill.bill_number,
        status=bill.status,
        total_paise=bill.total_paise,
        amount_paid_paise=bill.amount_paid_paise,
        outstanding_paise=max(0, bill.total_paise - bill.amount_paid_paise),
        issued_at=bill.issued_at,
        payment_requested_at=bill.payment_requested_at,
    )


def list_bills(
    db: Session,
    restaurant_id: UUID,
    status_filter: str | None,
    table_id: UUID | None,
    page: int,
    per_page: int,
):
    """GET /staff/bills — payment_requested first, then issued,
    then issued_at desc (within a status, newest request first)."""
    restaurant = db.get(Restaurant, restaurant_id)
    if restaurant is None:
        raise ApiError(404, "not_found", "Restaurant not found.")
    if status_filter is not None and status_filter not in BILL_FILTERS:
        raise ApiError(
            422, "validation_failed",
            f"status must be one of {sorted(BILL_FILTERS)}.",
            {"field": "status"},
        )
    if table_id is not None:
        table = db.get(DiningTable, table_id)
        if table is None or table.restaurant_id != restaurant_id:
            raise ApiError(404, "not_found", "Table not found.")
    base_q = select(Bill).where(Bill.restaurant_id == restaurant_id)
    if status_filter is not None:
        base_q = base_q.where(Bill.status.in_(BILL_FILTERS[status_filter]))
    if table_id is not None:
        base_q = base_q.where(Bill.table_id == table_id)
    total = db.execute(
        select(func.count()).select_from(base_q.subquery())
    ).scalar_one()
    total_pages = max(1, (total + per_page - 1) // per_page)
    rows = (
        db.execute(
            base_q.order_by(
                case((Bill.status == "payment_requested", 0), else_=1),
                Bill.payment_requested_at.desc().nullslast(),
                Bill.issued_at.desc(),
            )
            .offset((page - 1) * per_page)
            .limit(per_page)
        )
        .scalars()
        .all()
    )
    from app.schemas.billing import StaffBillListResponse

    return StaffBillListResponse(
        data=[_staff_bill_row(db, b) for b in rows],
        pagination=Pagination(
            page=page, per_page=per_page, total=total, total_pages=total_pages
        ),
    )


def _get_staff_bill(db: Session, restaurant_id: UUID, bill_id: UUID) -> Bill:
    """Row fetch for staff reads. Unknown id or wrong restaurant
    -> 404 (no leakage)."""
    restaurant = db.get(Restaurant, restaurant_id)
    if restaurant is None:
        raise ApiError(404, "not_found", "Restaurant not found.")
    bill = db.get(Bill, bill_id)
    if bill is None or bill.restaurant_id != restaurant_id:
        raise ApiError(404, "not_found", "Bill not found.")
    return bill


def get_staff_bill(db: Session, restaurant_id: UUID, bill_id: UUID) -> BillOut:
    return _bill_out(db, _get_staff_bill(db, restaurant_id, bill_id))


def list_staff_payments(
    db: Session, restaurant_id: UUID, bill_id: UUID, page: int, per_page: int
):
    from app.schemas.billing import StaffPaymentListResponse

    bill = _get_staff_bill(db, restaurant_id, bill_id)
    base_q = select(Payment).where(Payment.bill_id == bill.id)
    total = db.execute(
        select(func.count()).select_from(base_q.subquery())
    ).scalar_one()
    total_pages = max(1, (total + per_page - 1) // per_page)
    rows = (
        db.execute(
            base_q.order_by(Payment.collected_at.desc(), Payment.created_at.desc())
            .offset((page - 1) * per_page)
            .limit(per_page)
        )
        .scalars()
        .all()
    )
    return StaffPaymentListResponse(
        data=[_payment_out(p) for p in rows],
        pagination=Pagination(
            page=page, per_page=per_page, total=total, total_pages=total_pages
        ),
    )


def get_staff_receipt(db: Session, restaurant_id: UUID, bill_id: UUID) -> ReceiptOut:
    bill = _get_staff_bill(db, restaurant_id, bill_id)
    if bill.status != "paid":
        raise ApiError(
            409,
            "receipt_not_available",
            "The receipt is available only after the bill is paid.",
            {"status": bill.status},
        )
    return build_receipt(db, bill)


# ----------------------------------------------------------------------
# Cash payment confirmation (staff only) — idempotent, one tx
# ----------------------------------------------------------------------


def _in_progress_error() -> ApiError:
    return ApiError(
        409,
        "idempotency_in_progress",
        "A payment with this Idempotency-Key is already being recorded. "
        "Retry with the same key.",
        {"field": "Idempotency-Key"},
    )


def _get_payment_claim(
    db: Session, restaurant_id: UUID, bill_id: UUID, key: str
) -> IdempotencyKey | None:
    return (
        db.execute(
            select(IdempotencyKey).where(
                IdempotencyKey.restaurant_id == restaurant_id,
                IdempotencyKey.bill_id == bill_id,
                IdempotencyKey.endpoint == PAYMENT_ENDPOINT,
                IdempotencyKey.key == key,
            )
        )
        .scalars()
        .first()
    )


def _handle_payment_claim_conflict(
    db: Session,
    restaurant_id: UUID,
    bill_id: UUID,
    key: str,
    request_hash: str,
) -> tuple[dict, int, bool] | None:
    """After this request's claim INSERT raised a unique violation
    (a COMMITTED row with the scoped key exists — an in-flight owner
    would have made the INSERT block instead). Returns (body, status,
    replayed=True) for a replay, or None when the caller should
    attempt the claim again (the row was expired and deleted).
    Raises 422 for a body mismatch, 409 for a fresh pending row
    (steady state: a committed row always has its response filled,
    so a pending row here means the owner is still in flight)."""
    db.expire_all()
    row = _get_payment_claim(db, restaurant_id, bill_id, key)
    if row is None:
        return None
    if _is_expired(row):
        _delete_claim_row(db, row)
        return None
    if row.request_hash != request_hash:
        raise ApiError(
            422,
            "idempotency_key_reused",
            "This Idempotency-Key was already used with a different request body.",
            {"field": "Idempotency-Key"},
        )
    if row.response_body is not None:
        return row.response_body, row.response_status or 201, True
    raise _in_progress_error()


def _existing_payment_details(db: Session, bill: Bill) -> dict:
    """Details for the 409 double-confirmation guard: the latest
    payment for the bill, so the caller can see what already exists."""
    payment = (
        db.execute(
            select(Payment)
            .where(Payment.bill_id == bill.id)
            .order_by(Payment.collected_at.desc())
        )
        .scalars()
        .first()
    )
    details: dict = {"bill_id": str(bill.id), "bill_status": bill.status}
    if payment is not None:
        details["payment_id"] = str(payment.id)
        details["receipt_number"] = payment.receipt_number
    return details


def record_payment(
    db: Session,
    restaurant_id: UUID,
    bill_id: UUID,
    request: PaymentCreateRequest,
    idempotency_key: str,
    collected_by: UUID | None = None,
) -> tuple[dict, int, bool, dict | None]:
    """POST /staff/bills/:id/payments — single transaction, same
    pattern as order placement (Stage 3.2): claim + payment +
    stored response in ONE transaction.

    Returns (response_body, status, replayed, event): ``event`` is
    None for a replay (no new payment — nothing is broadcast) and
    otherwise carries the bill/payment summary the router
    broadcasts as ``payment.recorded`` (plus ``bill.paid`` when the
    bill is fully paid)."""
    body = request.model_dump(by_alias=True, mode="json")
    request_hash = _canonical_hash(body)

    for _ in range(PAYMENT_MAX_CLAIM_ATTEMPTS):
        outcome = _record_payment_once(
            db, restaurant_id, bill_id, request, request_hash, idempotency_key,
            collected_by=collected_by,
        )
        if outcome is not None:
            return outcome
    raise _in_progress_error()


def _record_payment_once(
    db: Session,
    restaurant_id: UUID,
    bill_id: UUID,
    request: PaymentCreateRequest,
    request_hash: str,
    idempotency_key: str,
    collected_by: UUID | None = None,
) -> tuple[dict, int, bool, dict | None] | None:
    """One payment attempt, entirely in ONE transaction.

    LOCK ORDER (deadlock-safety, found by the concurrency test):
    the bill row lock comes FIRST, the claim INSERT second. The
    claim's ``bill_id`` FK makes its INSERT take a FOR KEY SHARE
    lock on the bill row, and SELECT ... FOR UPDATE blocks on
    another transaction's FOR KEY SHARE — so inserting the claim
    first and locking the bill second deadlocks two concurrent
    payments (each holds FOR KEY SHARE, each waits for FOR UPDATE).
    Locking the bill first serialises the attempters, and the claim
    INSERT then only ever takes a lock the holder already owns.

    The claim is re-checked AFTER the bill lock is held, before
    any status validation: a same-key retry that arrives after the
    owner committed (bill now paid) must REPLAY the stored
    response, not 409 on the paid status. A pre-lock claim check
    alone cannot see the owner's commit in that window.
    """
    _set_lock_timeout(db)

    restaurant = db.get(Restaurant, restaurant_id)
    if restaurant is None:
        raise ApiError(404, "not_found", "Restaurant not found.")
    bill = (
        db.execute(
            select(Bill)
            .where(Bill.id == bill_id, Bill.restaurant_id == restaurant_id)
            .with_for_update()
        )
        .scalars()
        .first()
    )
    if bill is None:
        raise ApiError(404, "not_found", "Bill not found.")

    # Re-check the claim now that the bill lock is held: the owner
    # (if any) has committed by now, so a stored response here is a
    # replay. A row with no response is a leftover, never an owner
    # in flight — the owner holds this same bill lock while its
    # claim is uncommitted.
    existing = _get_payment_claim(db, restaurant_id, bill_id, idempotency_key)
    if existing is not None:
        if _is_expired(existing):
            db.execute(delete(IdempotencyKey).where(IdempotencyKey.id == existing.id))
            db.flush()
        elif existing.request_hash != request_hash:
            raise ApiError(
                422,
                "idempotency_key_reused",
                "This Idempotency-Key was already used with a different request body.",
                {"field": "Idempotency-Key"},
            )
        elif existing.response_body is not None:
            db.rollback()  # release the bill lock; nothing changed
            return existing.response_body, existing.response_status or 201, True, None
        else:
            raise _in_progress_error()

    # --- Claim: INSERT + flush only (no commit) ---------------------
    # Safe now: this transaction already holds the bill row lock,
    # so no other payment attempt for this bill can be mid-claim.
    claim = IdempotencyKey(
        restaurant_id=restaurant_id,
        table_session_id=None,
        endpoint=PAYMENT_ENDPOINT,
        key=idempotency_key,
        request_hash=request_hash,
        response_status=None,
        response_body=None,
        order_id=None,
        bill_id=bill_id,
        payment_id=None,
        expires_at=_now() + IDEMPOTENCY_TTL,
    )
    db.add(claim)
    try:
        db.flush()
    except IntegrityError:
        # Unreachable while the bill lock is held (the pre-check
        # above saw no row, and no other transaction can insert one
        # for this bill without the lock) — kept as a guard: treat
        # it as the conflict path rather than crashing.
        db.rollback()
        conflict = _handle_payment_claim_conflict(
            db, restaurant_id, bill_id, idempotency_key, request_hash
        )
        if conflict is None:
            return None
        body, status, replayed = conflict
        return body, status, replayed, None
    except OperationalError as exc:
        db.rollback()
        if _is_lock_timeout(exc):
            raise _in_progress_error()
        raise

    try:
        if bill.status == "paid":
            raise ApiError(
                409,
                "conflict",
                "This bill is already paid.",
                _existing_payment_details(db, bill),
            )
        if bill.status not in ("issued", "payment_requested"):
            raise ApiError(
                409,
                "conflict",
                f"Cannot record a payment on a {bill.status} bill.",
                {"status": bill.status},
            )

        # Stage 7 freeze: a bill paid without a prior
        # payment_requested transition freezes its tax now, BEFORE
        # the amount-due check, so the payment validates against
        # the frozen total. (A payment_requested bill is already
        # frozen — freeze_tax_on_bill is a no-op for it.)
        freeze_tax_on_bill(db, bill)

        splits = (
            db.execute(
                select(BillSplit)
                .where(BillSplit.bill_id == bill.id)
                .order_by(BillSplit.created_at)
            )
            .scalars()
            .all()
        )
        target_split: BillSplit | None = None
        if splits:
            if request.bill_split_id is None:
                raise ApiError(
                    422,
                    "validation_failed",
                    "This bill is split — pay one split at a time "
                    "(billSplitId is required).",
                    {"field": "billSplitId"},
                )
            target_split = next(
                (s for s in splits if s.id == request.bill_split_id), None
            )
            if target_split is None:
                raise ApiError(404, "not_found", "Bill split not found.")
            if target_split.status == "paid":
                raise ApiError(
                    409,
                    "conflict",
                    "This split is already paid.",
                    _existing_payment_details(db, bill),
                )
            paid_on_split = (
                db.execute(
                    select(func.coalesce(func.sum(Payment.amount_paise), 0)).where(
                        Payment.bill_split_id == target_split.id
                    )
                ).scalar_one()
            )
            due = target_split.amount_paise - paid_on_split
        else:
            if request.bill_split_id is not None:
                raise ApiError(
                    422,
                    "validation_failed",
                    "This bill has no splits — pay the whole bill "
                    "(omit billSplitId).",
                    {"field": "billSplitId"},
                )
            due = bill.total_paise - bill.amount_paid_paise

        if request.amount_paise > due:
            raise ApiError(
                422,
                "amount_exceeds_due",
                "The payment is more than the amount due.",
                {
                    "field": "amountPaise",
                    "amount_paise": request.amount_paise,
                    "amount_due_paise": due,
                },
            )

        # Receipt number under the restaurant row lock (same pattern
        # as order numbers) so concurrent confirmations serialise.
        db.execute(
            select(Restaurant)
            .where(Restaurant.id == restaurant.id)
            .with_for_update()
        )
        receipt_number = _next_receipt_number(db, restaurant)
        payment = Payment(
            restaurant_id=restaurant_id,
            bill_id=bill.id,
            bill_split_id=target_split.id if target_split else None,
            method="cash",
            idempotency_key=idempotency_key,
            amount_paise=request.amount_paise,
            collected_by=collected_by,  # Stage 7: JWT user id, else NULL.
            collected_at=_now(),
            change_due_paise=0,  # Decision 9 (D9): no change handling.
            status="confirmed",
            receipt_number=receipt_number,
            note=request.note,
        )
        db.add(payment)
        db.flush()
        # Stage 7 § I: a cash payment is a sensitive action — record
        # it in the same transaction as the payment, so a failed
        # payment never leaves a false success record (and a replay
        # writes nothing new: this code only runs for a fresh payment).
        write_audit(
            db,
            restaurant_id=restaurant_id,
            branch_id=bill.branch_id,
            action="payment.recorded",
            actor_user_id=collected_by,
            entity_type="payment",
            entity_id=payment.id,
            details={
                "bill_id": str(bill.id),
                "bill_number": bill.bill_number,
                "amount_paise": payment.amount_paise,
                "receipt_number": payment.receipt_number,
            },
        )
        bill.amount_paid_paise += request.amount_paise
        fully_paid = False
        if target_split is not None:
            if request.amount_paise == due:
                target_split.status = "paid"
                target_split.paid_at = _now()
            if all(s.status == "paid" for s in splits):
                bill.status = "paid"
                bill.paid_at = _now()
                fully_paid = True
        else:
            if bill.amount_paid_paise >= bill.total_paise:
                bill.status = "paid"
                bill.paid_at = _now()
                fully_paid = True
        bill.version += 1
        claim.payment_id = payment.id
        result = PaymentResultOut(
            payment=_payment_out(payment),
            bill=_bill_out(db, bill),
            receipt=build_receipt(db, bill) if fully_paid else None,
        )
        response_body = {"data": result.model_dump(by_alias=True, mode="json")}
        claim.response_status = 201
        claim.response_body = response_body
        db.commit()
    except ApiError:
        db.rollback()
        raise
    except OperationalError as exc:
        db.rollback()
        if _is_lock_timeout(exc):
            raise _in_progress_error()
        raise
    except Exception:
        db.rollback()
        raise
    db.refresh(payment)
    db.refresh(bill)
    event = {
        "restaurant_id": str(restaurant_id),
        "bill_id": str(bill.id),
        "bill_number": bill.bill_number,
        "table_id": str(bill.table_id),
        "payment_id": str(payment.id),
        "receipt_number": payment.receipt_number,
        "amount_paise": payment.amount_paise,
        "fully_paid": fully_paid,
        "bill_status": bill.status,
    }
    return response_body, 201, False, event
