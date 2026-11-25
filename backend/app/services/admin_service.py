"""Admin service — Stage 7 (dashboard, history, menu, users,
settings, tax, voids, refunds, reports, audit reads).

Every query is scoped by the caller's restaurant; branch-scoped
users (``branch_id`` set) see only their branch. No audit row is
ever updated or deleted here — ``audit_service.write_audit`` only
appends, in the same transaction as the action it records.
"""
import re
import uuid
from datetime import date, datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.models import AuditLog, Refund, TaxConfiguration, User
from app.auth.service import revoke_all_sessions
from app.core.errors import ApiError
from app.core.security import hash_password
from app.models import (
    Bill,
    BillItem,
    Branch,
    DiningTable,
    IdempotencyKey,
    ItemAddon,
    ItemVariant,
    MenuCategory,
    MenuItem,
    Order,
    OrderItem,
    Payment,
    Restaurant,
)
from app.schemas.admin import (
    AdminBillOut,
    AdminOrderOut,
    AdminPaymentOut,
    AdminRefundOut,
    AuditLogOut,
    BestSellerOut,
    BranchOut,
    CategoryOut,
    DailyCashOut,
    DashboardOverviewOut,
    MenuItemOut,
    OrdersByStatusOut,
    RecentActivityOut,
    RecentPaymentOut,
    RestaurantOut,
    TableOut,
    TaxConfigOut,
    VariantOut,
)
from app.schemas.auth import UserOut
from app.services.audit_service import write_audit
from app.services.billing_service import _bill_out
from app.services.kitchen_service import resolve_restaurant
from app.services.order_service import (
    IDEMPOTENCY_TTL,
    _canonical_hash,
    _is_expired,
    _is_lock_timeout,
    _set_lock_timeout,
)
from app.services.table_service import _now
from app.services.tax_service import (
    active_tax_config,
    branch_timezone,
    day_bounds_in_tz,
)

REFUND_ENDPOINT = "POST /api/v1/admin/bills/refunds"
REFUND_LOCK_TIMEOUT_SECONDS = 10


# ----------------------------------------------------------------------
# Scoping
# ----------------------------------------------------------------------

def resolve_admin_restaurant(
    db: Session, user: User, restaurant_id: UUID | None
) -> Restaurant:
    """The admin restaurant: the caller's own restaurant, or an
    explicit ``restaurant_id`` that must match it (else 403)."""
    if restaurant_id is not None and restaurant_id != user.restaurant_id:
        raise ApiError(403, "forbidden", "This restaurant is not yours.")
    restaurant = db.get(Restaurant, user.restaurant_id)
    if restaurant is None:
        raise ApiError(404, "not_found", "Restaurant not found.")
    return restaurant


def resolve_admin_branch(
    db: Session, user: User, restaurant_id: UUID, branch_id: UUID | None
) -> Branch | None:
    """Branch scope: a branch-scoped user is pinned to their branch
    (a different requested branch -> 403); an all-branch user may
    pick one or none (None = all branches). Returns the Branch row
    or None."""
    if user.branch_id is not None:
        if branch_id is not None and branch_id != user.branch_id:
            raise ApiError(403, "forbidden", "This branch is not yours.")
        branch = db.get(Branch, user.branch_id)
        if branch is None or branch.restaurant_id != restaurant_id:
            raise ApiError(404, "not_found", "Branch not found.")
        return branch
    if branch_id is None:
        return None
    branch = db.get(Branch, branch_id)
    if branch is None or branch.restaurant_id != restaurant_id:
        raise ApiError(404, "not_found", "Branch not found.")
    return branch


def _user_display(db: Session, user_id: UUID | None) -> str | None:
    if user_id is None:
        return None
    user = db.get(User, user_id)
    return user.email if user else None


def _paginate(total: int, page: int, per_page: int) -> dict:
    return {
        "page": page,
        "per_page": per_page,
        "total": total,
        "total_pages": max(1, (total + per_page - 1) // per_page),
    }


# ----------------------------------------------------------------------
# Dashboard
# ----------------------------------------------------------------------

def _period_bounds(
    preset: str, branch_tz, from_date: date | None, to_date: date | None
) -> tuple[datetime, datetime]:
    now_local = datetime.now(branch_tz)
    today = now_local.date()
    if preset == "custom":
        if from_date is None or to_date is None:
            raise ApiError(
                422, "validation_failed",
                "preset=custom needs from and to dates.",
                {"field": "preset"},
            )
        if to_date < from_date:
            raise ApiError(
                422, "validation_failed", "to must not be before from.",
                {"field": "to"},
            )
        start = datetime.combine(from_date, datetime.min.time(), tzinfo=branch_tz)
        end = datetime.combine(to_date + timedelta(days=1), datetime.min.time(), tzinfo=branch_tz)
        return start.astimezone(timezone.utc), end.astimezone(timezone.utc)
    if preset == "today":
        start_d, end_d = today, today
    elif preset == "yesterday":
        start_d = end_d = today - timedelta(days=1)
    elif preset == "last7":
        start_d, end_d = today - timedelta(days=6), today
    elif preset == "last30":
        start_d, end_d = today - timedelta(days=29), today
    else:
        raise ApiError(
            422, "validation_failed",
            "preset must be today, yesterday, last7, last30 or custom.",
            {"field": "preset"},
        )
    start = datetime.combine(start_d, datetime.min.time(), tzinfo=branch_tz)
    end = datetime.combine(end_d + timedelta(days=1), datetime.min.time(), tzinfo=branch_tz)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def dashboard_overview(
    db: Session,
    user: User,
    restaurant_id: UUID | None,
    branch_id: UUID | None,
    preset: str,
    from_date: date | None,
    to_date: date | None,
) -> DashboardOverviewOut:
    restaurant = resolve_admin_restaurant(db, user, restaurant_id)
    branch = resolve_admin_branch(db, user, restaurant.id, branch_id)
    tz = branch_timezone(db, branch.id if branch else None)
    start, end = _period_bounds(preset, tz, from_date, to_date)
    rid = restaurant.id
    bid = branch.id if branch else None

    def _branch_filter(column):
        return column == bid if bid is not None else True

    # Gross sales: SUM of totals of NON-VOIDED bills issued in the
    # period. Unpaid bills count here — gross sales is NOT cash
    # collected (that is the separate cash_collected field).
    gross = db.execute(
        select(func.coalesce(func.sum(Bill.total_paise), 0)).where(
            Bill.restaurant_id == rid,
            Bill.status != "voided",
            Bill.issued_at >= start,
            Bill.issued_at < end,
            _branch_filter(Bill.branch_id),
        )
    ).scalar_one()
    bill_count = db.execute(
        select(func.count()).select_from(Bill).where(
            Bill.restaurant_id == rid,
            Bill.status != "voided",
            Bill.issued_at >= start,
            Bill.issued_at < end,
            _branch_filter(Bill.branch_id),
        )
    ).scalar_one()
    # Cash collected: SUM of confirmed payments collected in the
    # period (refunds are a separate field — never netted here).
    pay_q = (
        select(func.coalesce(func.sum(Payment.amount_paise), 0))
        .select_from(Payment)
        .join(Bill, Bill.id == Payment.bill_id)
        .where(
            Payment.restaurant_id == rid,
            Payment.collected_at >= start,
            Payment.collected_at < end,
        )
    )
    if bid is not None:
        pay_q = pay_q.where(Bill.branch_id == bid)
    cash = db.execute(pay_q).scalar_one()
    # Refunds in the period.
    ref_q = (
        select(func.coalesce(func.sum(Refund.amount_paise), 0))
        .select_from(Refund)
        .join(Bill, Bill.id == Refund.bill_id)
        .where(
            Refund.restaurant_id == rid,
            Refund.refunded_at >= start,
            Refund.refunded_at < end,
        )
    )
    if bid is not None:
        ref_q = ref_q.where(Bill.branch_id == bid)
    refunds = db.execute(ref_q).scalar_one()
    # Outstanding: SUM of outstanding on OPEN bills AS OF NOW (not
    # period-bound).
    open_bills = db.execute(
        select(Bill).where(
            Bill.restaurant_id == rid,
            Bill.status.in_(("issued", "payment_requested")),
            _branch_filter(Bill.branch_id),
        )
    ).scalars().all()
    outstanding = sum(
        max(0, b.total_paise - b.amount_paid_paise) for b in open_bills
    )
    order_count = db.execute(
        select(func.count()).select_from(Order).where(
            Order.restaurant_id == rid,
            Order.placed_at >= start,
            Order.placed_at < end,
            _branch_filter(Order.branch_id),
        )
    ).scalar_one()
    status_rows = db.execute(
        select(Order.status, func.count()).where(
            Order.restaurant_id == rid,
            Order.placed_at >= start,
            Order.placed_at < end,
            _branch_filter(Order.branch_id),
        ).group_by(Order.status)
    ).all()
    recent_payments = db.execute(
        select(Payment)
        .where(Payment.restaurant_id == rid, Payment.collected_at >= start, Payment.collected_at < end)
        .order_by(Payment.collected_at.desc())
        .limit(10)
    ).scalars().all()
    recent_activity = db.execute(
        select(AuditLog)
        .where(AuditLog.restaurant_id == rid)
        .order_by(AuditLog.created_at.desc())
        .limit(10)
    ).scalars().all()
    best_rows = db.execute(
        select(
            OrderItem.menu_item_id,
            OrderItem.name_snapshot,
            func.sum(OrderItem.quantity).label("qty"),
            func.sum(OrderItem.total_paise).label("rev"),
        )
        .join(Order, Order.id == OrderItem.order_id)
        .where(
            Order.restaurant_id == rid,
            Order.status != "cancelled",
            Order.placed_at >= start,
            Order.placed_at < end,
            _branch_filter(Order.branch_id),
        )
        .group_by(OrderItem.menu_item_id, OrderItem.name_snapshot)
        .order_by(func.sum(OrderItem.quantity).desc())
        .limit(10)
    ).all()
    return DashboardOverviewOut(
        restaurant_id=rid,
        branch_id=bid,
        period_start=start,
        period_end=end,
        preset=preset,
        timezone=str(tz),
        gross_sales_paise=int(gross),
        cash_collected_paise=int(cash),
        refunds_paise=int(refunds),
        net_cash_paise=int(cash) - int(refunds),
        outstanding_paise=int(outstanding),
        bill_count=int(bill_count),
        order_count=int(order_count),
        average_order_value_paise=int(gross // bill_count) if bill_count else 0,
        orders_by_status=[
            OrdersByStatusOut(status=s, count=int(c)) for s, c in status_rows
        ],
        recent_payments=[
            RecentPaymentOut(
                id=p.id,
                bill_id=p.bill_id,
                bill_number=_bill_number(db, p.bill_id),
                table_number=_table_number(db, p.bill_id),
                amount_paise=p.amount_paise,
                receipt_number=p.receipt_number,
                collected_at=p.collected_at,
                collected_by_name=_user_display(db, p.collected_by),
            )
            for p in recent_payments
        ],
        recent_activity=[
            RecentActivityOut(
                id=a.id,
                action=a.action,
                entity_type=a.entity_type,
                entity_id=a.entity_id,
                details=a.details,
                created_at=a.created_at,
            )
            for a in recent_activity
        ],
        best_sellers=[
            BestSellerOut(
                menu_item_id=r[0], name=r[1], quantity=int(r[2]), revenue_paise=int(r[3])
            )
            for r in best_rows
        ],
    )


def _bill_number(db: Session, bill_id: UUID) -> str:
    bill = db.get(Bill, bill_id)
    return bill.bill_number if bill else ""


def _table_number(db: Session, bill_id: UUID) -> int:
    bill = db.get(Bill, bill_id)
    if bill is None:
        return 0
    table = db.get(DiningTable, bill.table_id)
    return table.number if table else 0


# ----------------------------------------------------------------------
# History
# ----------------------------------------------------------------------

def list_admin_orders(
    db: Session, user: User, restaurant_id: UUID | None, branch_id: UUID | None,
    status: str | None, table_id: UUID | None,
    from_date: date | None, to_date: date | None, page: int, per_page: int,
):
    restaurant = resolve_admin_restaurant(db, user, restaurant_id)
    branch = resolve_admin_branch(db, user, restaurant.id, branch_id)
    q = select(Order).where(Order.restaurant_id == restaurant.id)
    if branch is not None:
        q = q.where(Order.branch_id == branch.id)
    if status:
        q = q.where(Order.status == status)
    if table_id:
        q = q.where(Order.table_id == table_id)
    if from_date or to_date:
        tz = branch_timezone(db, branch.id if branch else None)
        if from_date:
            start = datetime.combine(from_date, datetime.min.time(), tzinfo=tz).astimezone(timezone.utc)
            q = q.where(Order.placed_at >= start)
        if to_date:
            end = datetime.combine(to_date + timedelta(days=1), datetime.min.time(), tzinfo=tz).astimezone(timezone.utc)
            q = q.where(Order.placed_at < end)
    total = db.execute(select(func.count()).select_from(q.subquery())).scalar_one()
    rows = db.execute(
        q.order_by(Order.placed_at.desc()).offset((page - 1) * per_page).limit(per_page)
    ).scalars().all()
    out = []
    for o in rows:
        table = db.get(DiningTable, o.table_id)
        count = db.execute(
            select(func.count()).select_from(OrderItem).where(OrderItem.order_id == o.id)
        ).scalar_one()
        out.append(AdminOrderOut(
            id=o.id, order_number=o.order_number,
            table_number=table.number if table else 0,
            table_session_id=o.table_session_id, status=o.status,
            subtotal_paise=o.subtotal_paise, total_paise=o.total_paise,
            placed_at=o.placed_at, item_count=int(count),
        ))
    from app.schemas.admin import AdminOrderListResponse
    return AdminOrderListResponse(data=out, pagination=_paginate(total, page, per_page))


def list_admin_bills(
    db: Session, user: User, restaurant_id: UUID | None, branch_id: UUID | None,
    status: str | None, table_id: UUID | None, page: int, per_page: int,
):
    restaurant = resolve_admin_restaurant(db, user, restaurant_id)
    branch = resolve_admin_branch(db, user, restaurant.id, branch_id)
    q = select(Bill).where(Bill.restaurant_id == restaurant.id)
    if branch is not None:
        q = q.where(Bill.branch_id == branch.id)
    if status:
        q = q.where(Bill.status == status)
    if table_id:
        q = q.where(Bill.table_id == table_id)
    total = db.execute(select(func.count()).select_from(q.subquery())).scalar_one()
    rows = db.execute(
        q.order_by(Bill.issued_at.desc()).offset((page - 1) * per_page).limit(per_page)
    ).scalars().all()
    out = []
    for b in rows:
        refunded = db.execute(
            select(func.coalesce(func.sum(Refund.amount_paise), 0)).where(Refund.bill_id == b.id)
        ).scalar_one()
        collected = db.execute(
            select(func.coalesce(func.sum(Payment.amount_paise), 0)).where(Payment.bill_id == b.id)
        ).scalar_one()
        out.append(AdminBillOut(
            id=b.id, bill_number=b.bill_number,
            table_number=_table_number(db, b.id), status=b.status,
            total_paise=b.total_paise, amount_paid_paise=b.amount_paid_paise,
            collected_paise=int(collected),
            # A voided bill is written off — nothing is owed on it.
            outstanding_paise=(
                0 if b.status == "voided"
                else max(0, b.total_paise - b.amount_paid_paise)
            ),
            refund_paise=int(refunded),
            net_collected_paise=int(collected) - int(refunded),
            issued_at=b.issued_at, paid_at=b.paid_at,
            voided_at=b.voided_at, void_reason=b.void_reason,
        ))
    from app.schemas.admin import AdminBillListResponse
    return AdminBillListResponse(data=out, pagination=_paginate(total, page, per_page))


def list_admin_payments(
    db: Session, user: User, restaurant_id: UUID | None, branch_id: UUID | None,
    page: int, per_page: int,
):
    restaurant = resolve_admin_restaurant(db, user, restaurant_id)
    branch = resolve_admin_branch(db, user, restaurant.id, branch_id)
    q = select(Payment).where(Payment.restaurant_id == restaurant.id)
    if branch is not None:
        q = q.join(Bill, Bill.id == Payment.bill_id).where(Bill.branch_id == branch.id)
    total = db.execute(select(func.count()).select_from(q.subquery())).scalar_one()
    rows = db.execute(
        q.order_by(Payment.collected_at.desc()).offset((page - 1) * per_page).limit(per_page)
    ).scalars().all()
    from app.schemas.admin import AdminPaymentListResponse
    return AdminPaymentListResponse(
        data=[
            AdminPaymentOut(
                id=p.id, bill_id=p.bill_id, bill_number=_bill_number(db, p.bill_id),
                receipt_number=p.receipt_number, amount_paise=p.amount_paise,
                method=p.method, status=p.status, collected_at=p.collected_at,
                collected_by_name=_user_display(db, p.collected_by),
            )
            for p in rows
        ],
        pagination=_paginate(total, page, per_page),
    )


def _refund_out(db: Session, refund: Refund) -> AdminRefundOut:
    bill = db.get(Bill, refund.bill_id)
    payment = db.get(Payment, refund.payment_id)
    return AdminRefundOut(
        id=refund.id, refund_reference=refund.refund_reference,
        bill_id=refund.bill_id,
        bill_number=bill.bill_number if bill else "",
        payment_id=refund.payment_id,
        receipt_number=payment.receipt_number if payment else None,
        amount_paise=refund.amount_paise, reason=refund.reason,
        refunded_by_name=_user_display(db, refund.refunded_by),
        refunded_at=refund.refunded_at,
    )


def list_admin_refunds(
    db: Session, user: User, restaurant_id: UUID | None, branch_id: UUID | None,
    page: int, per_page: int,
):
    restaurant = resolve_admin_restaurant(db, user, restaurant_id)
    branch = resolve_admin_branch(db, user, restaurant.id, branch_id)
    q = select(Refund).where(Refund.restaurant_id == restaurant.id)
    if branch is not None:
        q = q.join(Bill, Bill.id == Refund.bill_id).where(Bill.branch_id == branch.id)
    total = db.execute(select(func.count()).select_from(q.subquery())).scalar_one()
    rows = db.execute(
        q.order_by(Refund.refunded_at.desc()).offset((page - 1) * per_page).limit(per_page)
    ).scalars().all()
    from app.schemas.admin import AdminRefundListResponse
    return AdminRefundListResponse(
        data=[_refund_out(db, r) for r in rows],
        pagination=_paginate(total, page, per_page),
    )


def daily_cash_report(
    db: Session, user: User, restaurant_id: UUID | None, branch_id: UUID | None,
    day: date,
) -> DailyCashOut:
    restaurant = resolve_admin_restaurant(db, user, restaurant_id)
    branch = resolve_admin_branch(db, user, restaurant.id, branch_id)
    tz = branch_timezone(db, branch.id if branch else None)
    start, end = day_bounds_in_tz(tz, datetime.combine(day, datetime.min.time(), tzinfo=tz))
    rid = restaurant.id
    bid = branch.id if branch else None
    pay_q = (
        select(Payment)
        .join(Bill, Bill.id == Payment.bill_id)
        .where(
            Payment.restaurant_id == rid,
            Payment.collected_at >= start, Payment.collected_at < end,
        )
    )
    ref_q = (
        select(Refund)
        .join(Bill, Bill.id == Refund.bill_id)
        .where(
            Refund.restaurant_id == rid,
            Refund.refunded_at >= start, Refund.refunded_at < end,
        )
    )
    bill_q = select(Bill).where(
        Bill.restaurant_id == rid, Bill.issued_at >= start, Bill.issued_at < end
    )
    if bid is not None:
        pay_q = pay_q.where(Bill.branch_id == bid)
        ref_q = ref_q.where(Bill.branch_id == bid)
        bill_q = bill_q.where(Bill.branch_id == bid)
    payments = db.execute(pay_q).scalars().all()
    refunds = db.execute(ref_q).scalars().all()
    bills = db.execute(bill_q).scalars().all()
    open_bills = db.execute(
        select(Bill).where(
            Bill.restaurant_id == rid,
            Bill.status.in_(("issued", "payment_requested")),
            Bill.branch_id == bid if bid is not None else True,
        )
    ).scalars().all()
    gross = sum(p.amount_paise for p in payments)
    refund_total = sum(r.amount_paise for r in refunds)
    return DailyCashOut(
        date=day.isoformat(),
        timezone=str(tz),
        period_start=start,
        period_end=end,
        gross_cash_collected_paise=gross,
        cash_refunds_paise=refund_total,
        net_cash_paise=gross - refund_total,
        outstanding_paise=sum(max(0, b.total_paise - b.amount_paid_paise) for b in open_bills),
        bill_count=len(bills),
        payment_count=len(payments),
        refund_count=len(refunds),
    )


# ----------------------------------------------------------------------
# Bill void + cash refunds
# ----------------------------------------------------------------------

def _get_bill_scoped(
    db: Session, restaurant_id: UUID, bill_id: UUID, for_update: bool = False
) -> Bill:
    query = select(Bill).where(
        Bill.id == bill_id, Bill.restaurant_id == restaurant_id
    )
    if for_update:
        query = query.with_for_update()
    bill = db.execute(query).scalars().first()
    if bill is None:
        raise ApiError(404, "not_found", "Bill not found.")
    return bill


def void_bill(db: Session, user: User, bill_id: UUID, reason: str):
    """POST /admin/bills/{id}/void. Only from issued /
    payment_requested with ZERO confirmed payments (any payment ->
    409 'refund required first'). A repeat void -> 409 with the
    bill. The bill is kept (never deleted); its items become
    billable again via the Stage 7 billable-items rule.

    Stage 9: the bill row is locked BEFORE the status check, so
    concurrent voids serialise — the losers re-read the committed
    ``voided`` bill and 409 instead of all succeeding (the old
    ``db.get`` read had no lock: eight simultaneous voids all
    returned 200)."""
    bill = _get_bill_scoped(db, user.restaurant_id, bill_id, for_update=True)
    if user.branch_id is not None and bill.branch_id != user.branch_id:
        raise ApiError(403, "forbidden", "This bill belongs to another branch.")
    if bill.status == "voided":
        raise ApiError(
            409, "conflict", "This bill is already voided.",
            {"bill_id": str(bill.id), "status": bill.status},
        )
    if bill.status not in ("issued", "payment_requested"):
        raise ApiError(
            409, "conflict",
            f"Only an issued or payment-requested bill can be voided (status is {bill.status}).",
            {"status": bill.status},
        )
    payment_count = db.execute(
        select(func.count()).select_from(Payment).where(Payment.bill_id == bill.id)
    ).scalar_one()
    if payment_count:
        raise ApiError(
            409, "conflict",
            "This bill has confirmed payments — record a refund instead of voiding it.",
            {"payment_count": int(payment_count)},
        )
    bill.status = "voided"
    bill.voided_at = _now()
    bill.void_reason = reason
    bill.voided_by = user.id
    bill.version += 1
    write_audit(
        db, restaurant_id=user.restaurant_id, action="bill.voided",
        actor_user_id=user.id, branch_id=bill.branch_id,
        entity_type="bill", entity_id=bill.id,
        details={"bill_number": bill.bill_number, "reason": reason},
    )
    db.commit()
    db.refresh(bill)
    return _bill_out(db, bill)


def _next_refund_reference(db: Session, restaurant: Restaurant) -> str:
    today = _now().date()
    day_start = datetime(today.year, today.month, today.day, tzinfo=timezone.utc)
    count = db.execute(
        select(func.count()).select_from(Refund).where(
            Refund.restaurant_id == restaurant.id,
            Refund.refunded_at >= day_start,
        )
    ).scalar_one()
    return f"RFD-{today.isoformat()}-{count + 1:03d}"


def _refund_claim(db: Session, restaurant_id: UUID, bill_id: UUID, key: str):
    return db.execute(
        select(IdempotencyKey).where(
            IdempotencyKey.restaurant_id == restaurant_id,
            IdempotencyKey.bill_id == bill_id,
            IdempotencyKey.endpoint == REFUND_ENDPOINT,
            IdempotencyKey.key == key,
        )
    ).scalars().first()


def create_refund(
    db: Session, user: User, bill_id: UUID, payment_id: UUID,
    amount_paise: int, reason: str, idempotency_key: str,
) -> tuple[dict, int, bool]:
    """POST /admin/bills/{id}/refunds. Idempotency-Key REQUIRED,
    scope (restaurant_id, bill_id, endpoint, key) — the Stage 6
    single-transaction claim pattern: claim + refund + stored
    response in ONE transaction, bill row locked first. Duplicate
    key + same body replays; different body -> 422; concurrent
    refunds cannot exceed the refundable amount."""
    from app.schemas.admin import RefundDataResponse

    body = {
        "payment_id": str(payment_id),
        "amount_paise": amount_paise,
        "reason": reason,
    }
    request_hash = _canonical_hash(body)
    for _ in range(2):
        outcome = _create_refund_once(
            db, user, bill_id, payment_id, amount_paise, reason,
            idempotency_key, request_hash,
        )
        if outcome is not None:
            return outcome
    raise ApiError(
        409, "idempotency_in_progress",
        "A refund with this Idempotency-Key is already being recorded.",
        {"field": "Idempotency-Key"},
    )


def _create_refund_once(
    db: Session, user: User, bill_id: UUID, payment_id: UUID,
    amount_paise: int, reason: str, idempotency_key: str, request_hash: str,
) -> tuple[dict, int, bool] | None:
    _set_lock_timeout(db)
    restaurant = db.get(Restaurant, user.restaurant_id)
    if restaurant is None:
        raise ApiError(404, "not_found", "Restaurant not found.")
    bill = (
        db.execute(
            select(Bill)
            .where(Bill.id == bill_id, Bill.restaurant_id == restaurant.id)
            .with_for_update()
        )
        .scalars()
        .first()
    )
    if bill is None:
        raise ApiError(404, "not_found", "Bill not found.")
    if user.branch_id is not None and bill.branch_id != user.branch_id:
        raise ApiError(403, "forbidden", "This bill belongs to another branch.")
    existing = _refund_claim(db, restaurant.id, bill_id, idempotency_key)
    if existing is not None:
        if _is_expired(existing):
            db.execute(
                select(IdempotencyKey).where(IdempotencyKey.id == existing.id)
            )
            db.delete(existing)
            db.flush()
        elif existing.request_hash != request_hash:
            raise ApiError(
                422, "idempotency_key_reused",
                "This Idempotency-Key was already used with a different request body.",
                {"field": "Idempotency-Key"},
            )
        elif existing.response_body is not None:
            db.rollback()
            return existing.response_body, existing.response_status or 201, True
        else:
            raise ApiError(
                409, "idempotency_in_progress",
                "A refund with this Idempotency-Key is already being recorded.",
                {"field": "Idempotency-Key"},
            )
    claim = IdempotencyKey(
        restaurant_id=restaurant.id,
        table_session_id=None,
        endpoint=REFUND_ENDPOINT,
        key=idempotency_key,
        request_hash=request_hash,
        response_status=None,
        response_body=None,
        order_id=None,
        bill_id=bill_id,
        payment_id=payment_id,
        expires_at=_now() + IDEMPOTENCY_TTL,
    )
    db.add(claim)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        return None
    except Exception as exc:
        db.rollback()
        if _is_lock_timeout(exc):
            raise ApiError(
                409, "idempotency_in_progress",
                "A refund with this Idempotency-Key is already being recorded.",
                {"field": "Idempotency-Key"},
            )
        raise
    try:
        if bill.status not in ("paid", "partially_refunded"):
            raise ApiError(
                409, "conflict",
                f"Only a paid bill can be refunded (status is {bill.status}).",
                {"status": bill.status},
            )
        payment = (
            db.execute(
                select(Payment)
                .where(Payment.id == payment_id, Payment.bill_id == bill.id)
                .with_for_update()
            )
            .scalars()
            .first()
        )
        if payment is None:
            raise ApiError(404, "not_found", "Payment not found for this bill.")
        prior = db.execute(
            select(func.coalesce(func.sum(Refund.amount_paise), 0)).where(
                Refund.payment_id == payment.id
            )
        ).scalar_one()
        refundable = payment.amount_paise - int(prior)
        if amount_paise > refundable:
            raise ApiError(
                422, "validation_failed",
                "The refund is more than the refundable amount on this payment.",
                {
                    "field": "amountPaise",
                    "amount_paise": amount_paise,
                    "refundable_paise": refundable,
                },
            )
        db.execute(
            select(Restaurant).where(Restaurant.id == restaurant.id).with_for_update()
        )
        refund = Refund(
            restaurant_id=restaurant.id,
            bill_id=bill.id,
            payment_id=payment.id,
            amount_paise=amount_paise,
            reason=reason,
            refund_reference=_next_refund_reference(db, restaurant),
            refunded_by=user.id,
            refunded_at=_now(),
            idempotency_key=idempotency_key,
        )
        db.add(refund)
        db.flush()
        if amount_paise == refundable:
            payment.status = "refunded"
        else:
            payment.status = "partially_refunded"
        total_refunded = db.execute(
            select(func.coalesce(func.sum(Refund.amount_paise), 0)).where(
                Refund.bill_id == bill.id
            )
        ).scalar_one()
        if int(total_refunded) >= bill.amount_paid_paise:
            bill.status = "refunded"
        else:
            bill.status = "partially_refunded"
        bill.version += 1
        claim.payment_id = payment.id
        write_audit(
            db, restaurant_id=restaurant.id, action="refund.created",
            actor_user_id=user.id, branch_id=bill.branch_id,
            entity_type="refund", entity_id=refund.id,
            details={
                "refund_reference": refund.refund_reference,
                "bill_number": bill.bill_number,
                "amount_paise": amount_paise,
                "reason": reason,
            },
        )
        from app.schemas.admin import RefundDataResponse

        response_body = {
            "data": _refund_out(db, refund).model_dump(by_alias=True, mode="json")
        }
        claim.response_status = 201
        claim.response_body = response_body
        db.commit()
    except ApiError:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        if _is_lock_timeout(exc):
            raise ApiError(
                409, "idempotency_in_progress",
                "A refund with this Idempotency-Key is already being recorded.",
                {"field": "Idempotency-Key"},
            )
        raise
    return response_body, 201, False


# ----------------------------------------------------------------------
# Menu management
# ----------------------------------------------------------------------

def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return slug or "item"


def _category_out(db: Session, category: MenuCategory) -> CategoryOut:
    count = db.execute(
        select(func.count()).select_from(MenuItem).where(
            MenuItem.category_id == category.id
        )
    ).scalar_one()
    return CategoryOut(
        id=category.id, name=category.name, description=category.description,
        sort_order=category.sort_order, is_active=category.is_active,
        item_count=int(count),
    )


def list_categories(db: Session, user: User) -> list[CategoryOut]:
    rows = db.execute(
        select(MenuCategory)
        .where(MenuCategory.restaurant_id == user.restaurant_id)
        .order_by(MenuCategory.sort_order, MenuCategory.name)
    ).scalars().all()
    return [_category_out(db, c) for c in rows]


def create_category(db: Session, user: User, name: str, description, sort_order: int):
    slug = _slugify(name)
    existing = db.execute(
        select(MenuCategory).where(
            MenuCategory.restaurant_id == user.restaurant_id,
            MenuCategory.slug == slug,
        )
    ).scalars().first()
    if existing is not None:
        raise ApiError(409, "conflict", "A category with this name already exists.")
    row = MenuCategory(
        restaurant_id=user.restaurant_id, slug=slug, name=name,
        description=description, sort_order=sort_order, is_active=True,
    )
    db.add(row)
    db.flush()
    write_audit(
        db, restaurant_id=user.restaurant_id, action="menu.category_created",
        actor_user_id=user.id, entity_type="menu_category", entity_id=row.id,
        details={"name": name},
    )
    db.commit()
    db.refresh(row)
    return _category_out(db, row)


def _get_category(db: Session, user: User, category_id: UUID) -> MenuCategory:
    row = db.get(MenuCategory, category_id)
    if row is None or row.restaurant_id != user.restaurant_id:
        raise ApiError(404, "not_found", "Category not found.")
    return row


def update_category(db: Session, user: User, category_id: UUID, **changes):
    row = _get_category(db, user, category_id)
    for key, value in changes.items():
        if value is not None:
            setattr(row, key, value)
    write_audit(
        db, restaurant_id=user.restaurant_id, action="menu.category_updated",
        actor_user_id=user.id, entity_type="menu_category", entity_id=row.id,
        details={"name": row.name},
    )
    db.commit()
    db.refresh(row)
    return _category_out(db, row)


def reorder_categories(db: Session, user: User, category_ids: list[UUID]):
    rows = db.execute(
        select(MenuCategory).where(MenuCategory.restaurant_id == user.restaurant_id)
    ).scalars().all()
    if {c.id for c in rows} != set(category_ids) or len(set(category_ids)) != len(category_ids):
        raise ApiError(
            422, "validation_failed",
            "categoryIds must list every category exactly once.",
            {"field": "categoryIds"},
        )
    by_id = {c.id: c for c in rows}
    for idx, cid in enumerate(category_ids):
        by_id[cid].sort_order = idx
    db.commit()
    return list_categories(db, user)


def _menu_item_out(db: Session, item: MenuItem) -> MenuItemOut:
    category = db.get(MenuCategory, item.category_id)
    variants = db.execute(
        select(ItemVariant).where(ItemVariant.menu_item_id == item.id)
        .order_by(ItemVariant.sort_order, ItemVariant.name)
    ).scalars().all()
    add_ons = db.execute(
        select(ItemAddon).where(ItemAddon.menu_item_id == item.id)
        .order_by(ItemAddon.name)
    ).scalars().all()
    return MenuItemOut(
        id=item.id, category_id=item.category_id,
        category_name=category.name if category else None,
        name=item.name, description=item.description,
        price_paise=item.price_paise, is_veg=item.is_veg,
        is_available=item.is_available, is_popular=item.is_popular,
        image_url=item.image_url, station=item.station,
        sort_order=item.sort_order,
        variants=[
            VariantOut(id=v.id, name=v.name, price_delta_paise=v.price_delta_paise,
                       sort_order=v.sort_order, is_available=v.is_available)
            for v in variants
        ],
        add_ons=[_addon_out(a) for a in add_ons],
    )


def _addon_out(a: ItemAddon):
    from app.schemas.admin import AddOnOut

    return AddOnOut(id=a.id, name=a.name, price_paise=a.price_paise, is_available=a.is_available)


def list_menu_items(
    db: Session, user: User, category_id: UUID | None, page: int, per_page: int,
):
    q = select(MenuItem).where(MenuItem.restaurant_id == user.restaurant_id)
    if category_id:
        q = q.where(MenuItem.category_id == category_id)
    total = db.execute(select(func.count()).select_from(q.subquery())).scalar_one()
    rows = db.execute(
        q.order_by(MenuItem.sort_order, MenuItem.name)
        .offset((page - 1) * per_page).limit(per_page)
    ).scalars().all()
    from app.schemas.admin import MenuItemListResponse
    return MenuItemListResponse(
        data=[_menu_item_out(db, i) for i in rows],
        pagination=_paginate(total, page, per_page),
    )


def create_menu_item(db: Session, user: User, **fields):
    category = _get_category(db, user, fields.pop("category_id"))
    slug = _slugify(fields["name"])
    existing = db.execute(
        select(MenuItem).where(
            MenuItem.restaurant_id == user.restaurant_id, MenuItem.slug == slug
        )
    ).scalars().first()
    if existing is not None:
        raise ApiError(409, "conflict", "A menu item with this name already exists.")
    item = MenuItem(restaurant_id=user.restaurant_id, category_id=category.id, slug=slug, **fields)
    db.add(item)
    db.flush()
    write_audit(
        db, restaurant_id=user.restaurant_id, action="menu.item_created",
        actor_user_id=user.id, entity_type="menu_item", entity_id=item.id,
        details={"name": item.name, "price_paise": item.price_paise},
    )
    db.commit()
    db.refresh(item)
    return _menu_item_out(db, item)


def _get_menu_item(db: Session, user: User, item_id: UUID) -> MenuItem:
    item = db.get(MenuItem, item_id)
    if item is None or item.restaurant_id != user.restaurant_id:
        raise ApiError(404, "not_found", "Menu item not found.")
    return item


def update_menu_item(db: Session, user: User, item_id: UUID, **changes):
    item = _get_menu_item(db, user, item_id)
    old_price = item.price_paise
    if changes.get("category_id") is not None:
        _get_category(db, user, changes.pop("category_id"))
        item.category_id = changes.pop("category_id")
    for key, value in changes.items():
        if value is not None:
            setattr(item, key, value)
    action = "menu.item_updated"
    details: dict = {"name": item.name}
    if item.price_paise != old_price:
        action = "menu.price_changed"
        details["old_price_paise"] = old_price
        details["new_price_paise"] = item.price_paise
    if changes.get("is_available") is False:
        details["deactivated"] = True
    write_audit(
        db, restaurant_id=user.restaurant_id, action=action,
        actor_user_id=user.id, entity_type="menu_item", entity_id=item.id,
        details=details,
    )
    db.commit()
    db.refresh(item)
    return _menu_item_out(db, item)


def create_variant(db: Session, user: User, item_id: UUID, name: str, price_delta_paise: int, sort_order: int, is_available: bool):
    item = _get_menu_item(db, user, item_id)
    slug = _slugify(name)
    existing = db.execute(
        select(ItemVariant).where(ItemVariant.menu_item_id == item.id, ItemVariant.slug == slug)
    ).scalars().first()
    if existing is not None:
        raise ApiError(409, "conflict", "A variant with this name already exists for this item.")
    row = ItemVariant(menu_item_id=item.id, slug=slug, name=name,
                      price_delta_paise=price_delta_paise, sort_order=sort_order,
                      is_available=is_available)
    db.add(row)
    db.flush()
    write_audit(
        db, restaurant_id=user.restaurant_id, action="menu.variant_created",
        actor_user_id=user.id, entity_type="menu_item", entity_id=item.id,
        details={"name": name, "item_name": item.name},
    )
    db.commit()
    db.refresh(row)
    return VariantOut(id=row.id, name=row.name, price_delta_paise=row.price_delta_paise,
                      sort_order=row.sort_order, is_available=row.is_available)


def _get_variant(db: Session, user: User, item_id: UUID, variant_id: UUID) -> ItemVariant:
    item = _get_menu_item(db, user, item_id)
    row = db.get(ItemVariant, variant_id)
    if row is None or row.menu_item_id != item.id:
        raise ApiError(404, "not_found", "Variant not found for this item.")
    return row


def update_variant(db: Session, user: User, item_id: UUID, variant_id: UUID, **changes):
    row = _get_variant(db, user, item_id, variant_id)
    for key, value in changes.items():
        if value is not None:
            setattr(row, key, value)
    db.commit()
    db.refresh(row)
    return VariantOut(id=row.id, name=row.name, price_delta_paise=row.price_delta_paise,
                      sort_order=row.sort_order, is_available=row.is_available)


def delete_variant(db: Session, user: User, item_id: UUID, variant_id: UUID) -> None:
    row = _get_variant(db, user, item_id, variant_id)
    db.delete(row)
    db.commit()


def create_add_on(db: Session, user: User, item_id: UUID, name: str, price_paise: int, is_available: bool):
    item = _get_menu_item(db, user, item_id)
    slug = _slugify(name)
    existing = db.execute(
        select(ItemAddon).where(ItemAddon.menu_item_id == item.id, ItemAddon.slug == slug)
    ).scalars().first()
    if existing is not None:
        raise ApiError(409, "conflict", "An add-on with this name already exists for this item.")
    row = ItemAddon(menu_item_id=item.id, restaurant_id=user.restaurant_id,
                    slug=slug, name=name, price_paise=price_paise, is_available=is_available)
    db.add(row)
    db.flush()
    write_audit(
        db, restaurant_id=user.restaurant_id, action="menu.add_on_created",
        actor_user_id=user.id, entity_type="menu_item", entity_id=item.id,
        details={"name": name, "item_name": item.name},
    )
    db.commit()
    db.refresh(row)
    return _addon_out(row)


def _get_add_on(db: Session, user: User, item_id: UUID, add_on_id: UUID) -> ItemAddon:
    item = _get_menu_item(db, user, item_id)
    row = db.get(ItemAddon, add_on_id)
    if row is None or row.menu_item_id != item.id:
        raise ApiError(404, "not_found", "Add-on not found for this item.")
    return row


def update_add_on(db: Session, user: User, item_id: UUID, add_on_id: UUID, **changes):
    row = _get_add_on(db, user, item_id, add_on_id)
    for key, value in changes.items():
        if value is not None:
            setattr(row, key, value)
    db.commit()
    db.refresh(row)
    return _addon_out(row)


def delete_add_on(db: Session, user: User, item_id: UUID, add_on_id: UUID) -> None:
    row = _get_add_on(db, user, item_id, add_on_id)
    db.delete(row)
    db.commit()


# ----------------------------------------------------------------------
# Users
# ----------------------------------------------------------------------

def _active_owner_count(db: Session, restaurant_id: UUID, exclude_id: UUID | None = None) -> int:
    q = select(func.count()).select_from(User).where(
        User.restaurant_id == restaurant_id,
        User.role == "owner",
        User.is_active.is_(True),
    )
    if exclude_id is not None:
        q = q.where(User.id != exclude_id)
    return int(db.execute(q).scalar_one())


def _guard_last_owner(db: Session, user: User, target: User) -> None:
    """Refuse any change that would leave zero active owners."""
    if target.role == "owner" and target.is_active:
        if _active_owner_count(db, target.restaurant_id, exclude_id=target.id) == 0:
            raise ApiError(
                409, "conflict",
                "This is the last active owner — demote or deactivate another owner first.",
            )


def _user_out_row(user: User) -> UserOut:
    return UserOut.model_validate(user, from_attributes=True)


def list_users(db: Session, user: User, page: int, per_page: int):
    q = select(User).where(User.restaurant_id == user.restaurant_id)
    total = db.execute(select(func.count()).select_from(q.subquery())).scalar_one()
    rows = db.execute(
        q.order_by(User.email).offset((page - 1) * per_page).limit(per_page)
    ).scalars().all()
    from app.schemas.auth import UserListResponse
    return UserListResponse(
        data=[_user_out_row(u) for u in rows],
        pagination=_paginate(total, page, per_page),
    )


def create_user(
    db: Session, actor: User, email: str, password: str, role: str,
    branch_id: UUID | None, flags: dict,
):
    """Role rules: only an owner can create owner/manager accounts;
    a manager with can_manage_staff can create waiter/kitchen only."""
    if role in ("owner", "manager") and actor.role != "owner":
        raise ApiError(403, "forbidden", "Only the owner can create owner or manager accounts.")
    normalised = email.strip().lower()
    existing = db.execute(
        select(User).where(User.restaurant_id == actor.restaurant_id, User.email == normalised)
    ).scalars().first()
    if existing is not None:
        raise ApiError(409, "conflict", "A user with this email already exists.")
    branch = None
    if branch_id is not None:
        branch = db.get(Branch, branch_id)
        if branch is None or branch.restaurant_id != actor.restaurant_id:
            raise ApiError(404, "not_found", "Branch not found.")
    if actor.branch_id is not None and branch_id != actor.branch_id:
        raise ApiError(403, "forbidden", "You can only create users for your own branch.")
    # A manager may not grant flags they do not themselves hold.
    if actor.role != "owner":
        for flag in ("can_manage_staff", "can_manage_settings", "can_refund", "can_void"):
            if flags.get(flag) and not getattr(actor, flag, False):
                raise ApiError(403, "forbidden", f"You cannot grant {flag}.")
    row = User(
        restaurant_id=actor.restaurant_id,
        branch_id=branch_id,
        email=normalised,
        password_hash=hash_password(password),
        role=role,
        is_active=True,
        must_change_password=True,
        **{k: bool(flags.get(k, False)) for k in ("can_manage_staff", "can_manage_settings", "can_refund", "can_void")},
    )
    db.add(row)
    db.flush()
    write_audit(
        db, restaurant_id=actor.restaurant_id, action="user.created",
        actor_user_id=actor.id, entity_type="user", entity_id=row.id,
        details={"email": normalised, "role": role},
    )
    db.commit()
    db.refresh(row)
    return _user_out_row(row)


def _get_user_scoped(db: Session, actor: User, user_id: UUID) -> User:
    row = db.get(User, user_id)
    if row is None or row.restaurant_id != actor.restaurant_id:
        raise ApiError(404, "not_found", "User not found.")
    return row


def update_user(db: Session, actor: User, user_id: UUID, **changes):
    target = _get_user_scoped(db, actor, user_id)
    new_role = changes.get("role")
    if new_role is not None and new_role != target.role:
        if new_role == "owner" and actor.role != "owner":
            raise ApiError(403, "forbidden", "Only the owner can promote someone to owner.")
        if target.id == actor.id and new_role == "owner":
            raise ApiError(403, "forbidden", "You cannot change your own role to owner.")
        if target.role == "owner" and new_role != "owner":
            _guard_last_owner(db, actor, target)
        target.role = new_role
    if changes.get("branch_id") is not None:
        branch = db.get(Branch, changes["branch_id"])
        if branch is None or branch.restaurant_id != actor.restaurant_id:
            raise ApiError(404, "not_found", "Branch not found.")
        target.branch_id = changes["branch_id"]
    if changes.get("is_active") is False and target.is_active:
        _guard_last_owner(db, actor, target)
        target.is_active = False
        target.deactivated_at = _now()
        revoke_all_sessions(db, target)
    elif changes.get("is_active") is True and not target.is_active:
        target.is_active = True
        target.deactivated_at = None
    for flag in ("can_manage_staff", "can_manage_settings", "can_refund", "can_void"):
        if changes.get(flag) is not None:
            setattr(target, flag, changes[flag])
    write_audit(
        db, restaurant_id=actor.restaurant_id, action="user.role_changed",
        actor_user_id=actor.id, entity_type="user", entity_id=target.id,
        details={"email": target.email, "role": target.role},
    )
    db.commit()
    db.refresh(target)
    return _user_out_row(target)


def deactivate_user(db: Session, actor: User, user_id: UUID):
    target = _get_user_scoped(db, actor, user_id)
    if target.id == actor.id:
        raise ApiError(403, "forbidden", "You cannot deactivate your own account.")
    if not target.is_active:
        raise ApiError(409, "conflict", "This user is already deactivated.")
    _guard_last_owner(db, actor, target)
    target.is_active = False
    target.deactivated_at = _now()
    revoke_all_sessions(db, target)
    write_audit(
        db, restaurant_id=actor.restaurant_id, action="user.deactivated",
        actor_user_id=actor.id, entity_type="user", entity_id=target.id,
        details={"email": target.email, "role": target.role},
    )
    db.commit()
    db.refresh(target)
    return _user_out_row(target)


def reset_user_password(db: Session, actor: User, user_id: UUID, new_password: str):
    target = _get_user_scoped(db, actor, user_id)
    target.password_hash = hash_password(new_password)
    target.must_change_password = True
    revoke_all_sessions(db, target)
    write_audit(
        db, restaurant_id=actor.restaurant_id, action="user.password_reset",
        actor_user_id=actor.id, entity_type="user", entity_id=target.id,
        details={"email": target.email},
    )
    db.commit()
    db.refresh(target)
    return _user_out_row(target)


# ----------------------------------------------------------------------
# Settings + tax
# ----------------------------------------------------------------------

def get_restaurant_settings(db: Session, user: User) -> RestaurantOut:
    r = db.get(Restaurant, user.restaurant_id)
    return RestaurantOut(
        id=r.id, name=r.name, slug=r.slug, tagline=r.tagline, cuisine=r.cuisine,
        address=r.address, phone=r.phone, contact_name=r.contact_name,
        contact_email=r.contact_email, opening_hours=r.opening_hours,
        currency=r.currency,
    )


def update_restaurant_settings(db: Session, user: User, **changes):
    r = db.get(Restaurant, user.restaurant_id)
    for key, value in changes.items():
        if value is not None:
            setattr(r, key, value)
    write_audit(
        db, restaurant_id=user.restaurant_id, action="settings.restaurant_updated",
        actor_user_id=user.id, entity_type="restaurant", entity_id=r.id,
        details={"name": r.name},
    )
    db.commit()
    db.refresh(r)
    return get_restaurant_settings(db, user)


def list_branches(db: Session, user: User) -> list[BranchOut]:
    rows = db.execute(
        select(Branch).where(Branch.restaurant_id == user.restaurant_id)
        .order_by(Branch.name)
    ).scalars().all()
    return [
        BranchOut(id=b.id, name=b.name, slug=b.slug, address=b.address,
                  timezone=b.timezone or "Asia/Kolkata",
                  contact_name=b.contact_name, contact_phone=b.contact_phone,
                  contact_email=b.contact_email, is_active=b.is_active)
        for b in rows
    ]


def update_branch_settings(db: Session, user: User, branch_id: UUID, **changes):
    branch = db.get(Branch, branch_id)
    if branch is None or branch.restaurant_id != user.restaurant_id:
        raise ApiError(404, "not_found", "Branch not found.")
    if user.branch_id is not None and user.branch_id != branch_id:
        raise ApiError(403, "forbidden", "This branch is not yours.")
    for key, value in changes.items():
        if value is not None:
            setattr(branch, key, value)
    write_audit(
        db, restaurant_id=user.restaurant_id, action="settings.branch_updated",
        actor_user_id=user.id, entity_type="branch", entity_id=branch.id,
        details={"name": branch.name},
    )
    db.commit()
    db.refresh(branch)
    return BranchOut(id=branch.id, name=branch.name, slug=branch.slug,
                     address=branch.address, timezone=branch.timezone,
                     contact_name=branch.contact_name,
                     contact_phone=branch.contact_phone,
                     contact_email=branch.contact_email, is_active=branch.is_active)


def list_tables(db: Session, user: User, branch_id: UUID | None) -> list[TableOut]:
    q = select(DiningTable).where(DiningTable.restaurant_id == user.restaurant_id)
    if branch_id is not None:
        q = q.where(DiningTable.branch_id == branch_id)
    elif user.branch_id is not None:
        q = q.where(DiningTable.branch_id == user.branch_id)
    rows = db.execute(q.order_by(DiningTable.number)).scalars().all()
    return [
        TableOut(id=t.id, number=t.number, status=t.status,
                 branch_id=t.branch_id, is_active=t.is_active)
        for t in rows
    ]


def create_table(db: Session, user: User, number: int, branch_id: UUID | None):
    if branch_id is None:
        branch_id = user.branch_id
    if branch_id is None:
        branches = db.execute(
            select(Branch).where(Branch.restaurant_id == user.restaurant_id)
        ).scalars().all()
        if len(branches) != 1:
            raise ApiError(422, "validation_failed", "branchId is required.", {"field": "branchId"})
        branch_id = branches[0].id
    branch = db.get(Branch, branch_id)
    if branch is None or branch.restaurant_id != user.restaurant_id:
        raise ApiError(404, "not_found", "Branch not found.")
    existing = db.execute(
        select(DiningTable).where(
            DiningTable.restaurant_id == user.restaurant_id,
            DiningTable.branch_id == branch_id,
            DiningTable.number == number,
        )
    ).scalars().first()
    if existing is not None:
        raise ApiError(409, "conflict", "A table with this number already exists for this branch.")
    row = DiningTable(
        restaurant_id=user.restaurant_id, branch_id=branch_id,
        number=number, status="available", is_active=True,
    )
    db.add(row)
    db.flush()
    write_audit(
        db, restaurant_id=user.restaurant_id, action="settings.table_created",
        actor_user_id=user.id, entity_type="table", entity_id=row.id,
        details={"number": number},
    )
    db.commit()
    db.refresh(row)
    return TableOut(id=row.id, number=row.number, status=row.status,
                    branch_id=row.branch_id, is_active=row.is_active)


def get_tax_config(db: Session, user: User):
    current = active_tax_config(db, user.restaurant_id)
    history = db.execute(
        select(TaxConfiguration)
        .where(
            TaxConfiguration.restaurant_id == user.restaurant_id,
            TaxConfiguration.branch_id.is_(None),
        )
        .order_by(TaxConfiguration.effective_from.desc())
    ).scalars().all()
    from app.schemas.admin import TaxConfigResponse

    def _out(c: TaxConfiguration | None) -> TaxConfigOut | None:
        if c is None:
            return None
        return TaxConfigOut(
            id=c.id, enabled=c.enabled, rate_bps=c.rate_bps, mode=c.mode,
            label=c.label, effective_from=c.effective_from, created_at=c.created_at,
        )

    return TaxConfigResponse(current=_out(current), history=[_out(c) for c in history if _out(c)])


def create_tax_config(
    db: Session, user: User, enabled: bool, rate_bps: int | None,
    mode: str, label: str | None, effective_from: datetime,
):
    if enabled and rate_bps is None:
        raise ApiError(
            422, "validation_failed",
            "rateBps is required when tax is enabled.",
            {"field": "rateBps"},
        )
    now = _now()
    eff = effective_from
    if eff.tzinfo is None:
        eff = eff.replace(tzinfo=timezone.utc)
    if eff < now - timedelta(minutes=1):
        raise ApiError(
            422, "validation_failed",
            "effectiveFrom must be now or in the future.",
            {"field": "effectiveFrom"},
        )
    row = TaxConfiguration(
        restaurant_id=user.restaurant_id, branch_id=None, enabled=enabled,
        rate_bps=rate_bps if enabled else None, mode=mode, label=label,
        effective_from=eff, created_by=user.id,
    )
    db.add(row)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise ApiError(
            409, "conflict",
            "A tax configuration already exists for this effective time.",
            {"field": "effectiveFrom"},
        )
    write_audit(
        db, restaurant_id=user.restaurant_id, action="tax.config_created",
        actor_user_id=user.id, entity_type="tax_configuration", entity_id=row.id,
        details={
            "enabled": enabled,
            "rate_bps": rate_bps if enabled else None,
            "mode": mode,
            "label": label,
            "effective_from": eff.isoformat(),
        },
    )
    db.commit()
    db.refresh(row)
    return get_tax_config(db, user)


# ----------------------------------------------------------------------
# Audit reads
# ----------------------------------------------------------------------

def list_audit_logs(
    db: Session, user: User, restaurant_id: UUID | None, branch_id: UUID | None,
    action: str | None, entity_type: str | None,
    from_date: date | None, to_date: date | None, page: int, per_page: int,
):
    restaurant = resolve_admin_restaurant(db, user, restaurant_id)
    branch = resolve_admin_branch(db, user, restaurant.id, branch_id)
    q = select(AuditLog).where(AuditLog.restaurant_id == restaurant.id)
    if branch is not None:
        q = q.where(AuditLog.branch_id == branch.id)
    if action:
        q = q.where(AuditLog.action == action)
    if entity_type:
        q = q.where(AuditLog.entity_type == entity_type)
    if from_date:
        q = q.where(AuditLog.created_at >= datetime.combine(from_date, datetime.min.time(), tzinfo=timezone.utc))
    if to_date:
        q = q.where(AuditLog.created_at < datetime.combine(to_date + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc))
    total = db.execute(select(func.count()).select_from(q.subquery())).scalar_one()
    rows = db.execute(
        q.order_by(AuditLog.created_at.desc()).offset((page - 1) * per_page).limit(per_page)
    ).scalars().all()
    from app.schemas.admin import AuditLogListResponse
    return AuditLogListResponse(
        data=[
            AuditLogOut(id=a.id, action=a.action, entity_type=a.entity_type,
                        entity_id=a.entity_id, details=a.details, created_at=a.created_at)
            for a in rows
        ],
        pagination=_paginate(total, page, per_page),
    )
