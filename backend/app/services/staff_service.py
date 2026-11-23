"""Waiter / Staff service (Stage 5) — floor, orders, serving, help calls.

Scope (api-plan.md §§ 4.2, 5; roadmap Stage 5 as built):
- Floor overview (read), active orders (read), serve (ready -> served),
  help-call list / acknowledge / resolve. NOTHING else: no billing,
  no session close, no table-status transitions, no staff assignments.
  Those are deferred and recorded in the roadmap Stage 5 report.

Every query filters by ``restaurant_id``; an unknown restaurant is a
404 and another restaurant's data is a 404 too (api-plan.md § 11:
no existence leakage across tenants).

Serving: ONLY ready -> served. It sets ``served_at``, bumps
``version``, and moves non-cancelled items to ``served`` — in ONE
transaction under a row lock, so concurrent serves serialise (the
second sees the new state and 409s). Serving does NOT close the
table session and does NOT change the table status. Served orders
leave the kitchen active queue (their status is terminal there).

Help calls: acknowledge is open -> acknowledged ONLY; resolve is
acknowledged -> resolved ONLY (api-plan.md § 5) — a waiter must
acknowledge first. Both set their timestamp, run under a row lock
in one transaction, and 409 on any other state (including a
duplicate action).

Broadcasting is NOT done here: the service stays broadcast-free so
tests can call it without a WebSocket, and so no event can ever be
published from inside (or before) a transaction. Routes broadcast
after the service returns successfully (architecture.md § 3.2).
"""
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import ApiError
from app.models import DiningTable, HelpCall, Order, Restaurant, TableSession
from app.schemas.common import Pagination
from app.schemas.kitchen import KitchenOrderOut
from app.schemas.staff import (
    StaffHelpCallListResponse,
    StaffHelpCallOut,
    StaffOrderListResponse,
    StaffRestaurantOut,
    StaffRestaurantsResponse,
    StaffTableDetailOut,
    StaffTableOut,
)
from app.services.kitchen_service import (
    ACTIVE_ORDER_STATUSES,
    _kitchen_out,
    resolve_restaurant,
)
from app.services.table_service import _now

# Help-call list filter values. Default (None) = unresolved only
# (open + acknowledged), oldest first.
HELP_CALL_FILTERS: dict[str, tuple[str, ...]] = {
    "open": ("open",),
    "acknowledged": ("acknowledged",),
    "resolved": ("resolved",),
    "cancelled": ("cancelled",),
    "unresolved": ("open", "acknowledged"),
}

# Staff order-list filter values: the active order statuses
# directly (placed / accepted / preparing / ready). Default (None)
# = all active orders, oldest first.
STAFF_ORDER_FILTERS: dict[str, tuple[str, ...]] = {
    "placed": ("placed",),
    "accepted": ("accepted",),
    "preparing": ("preparing",),
    "ready": ("ready",),
}


def _active_session_for_table(db: Session, table_id: UUID) -> TableSession | None:
    return (
        db.execute(
            select(TableSession).where(
                TableSession.table_id == table_id,
                TableSession.status == "active",
            )
        )
        .scalars()
        .first()
    )


def _help_call_out(db: Session, call: HelpCall) -> StaffHelpCallOut:
    table = db.get(DiningTable, call.table_id)
    return StaffHelpCallOut(
        id=call.id,
        table_session_id=call.table_session_id,
        type=call.type,
        note=call.note,
        status=call.status,
        created_at=call.created_at,
        table_number=table.number if table is not None else 0,
        acknowledged_at=call.acknowledged_at,
        resolved_at=call.resolved_at,
    )


def _table_out(
    db: Session, table: DiningTable, session: TableSession | None
) -> StaffTableOut:
    """One floor-grid row. All flags derive from live DB state:
    the active session (if any), its orders, and its help calls."""
    active_order_count = 0
    ready_order_count = 0
    latest_order_status: str | None = None
    has_open_help_call = False
    if session is not None:
        orders = (
            db.execute(
                select(Order)
                .where(Order.table_session_id == session.id)
                .order_by(Order.placed_at.desc(), Order.created_at.desc())
            )
            .scalars()
            .all()
        )
        active = [o for o in orders if o.status in ACTIVE_ORDER_STATUSES]
        active_order_count = len(active)
        ready_order_count = len([o for o in active if o.status == "ready"])
        latest_order_status = orders[0].status if orders else None
        has_open_help_call = (
            db.execute(
                select(func.count())
                .select_from(HelpCall)
                .where(
                    HelpCall.table_session_id == session.id,
                    HelpCall.status.in_(("open", "acknowledged")),
                )
            ).scalar_one()
            > 0
        )
    return StaffTableOut(
        id=table.id,
        number=table.number,
        status=table.status,
        session_id=session.id if session is not None else None,
        session_opened_at=session.opened_at if session is not None else None,
        active_order_count=active_order_count,
        ready_order_count=ready_order_count,
        latest_order_status=latest_order_status,
        has_open_help_call=has_open_help_call,
    )


def list_restaurants(db: Session):
    """Dev-only restaurant picker for the staff dashboard (id + name)."""
    rows = (
        db.execute(select(Restaurant).order_by(Restaurant.name))
        .scalars()
        .all()
    )
    return StaffRestaurantsResponse(
        data=[StaffRestaurantOut(id=r.id, name=r.name) for r in rows]
    )


def list_tables(db: Session, restaurant_id: UUID) -> list[StaffTableOut]:
    """The floor grid: every table for the restaurant, in number
    order, with its live operational state."""
    restaurant = db.get(Restaurant, restaurant_id)
    if restaurant is None:
        raise ApiError(404, "not_found", "Restaurant not found.")
    tables = (
        db.execute(
            select(DiningTable)
            .where(DiningTable.restaurant_id == restaurant_id)
            .order_by(DiningTable.number.asc())
        )
        .scalars()
        .all()
    )
    return [
        _table_out(db, table, _active_session_for_table(db, table.id))
        for table in tables
    ]


def get_table_detail(
    db: Session, restaurant_id: UUID, table_id: UUID
) -> StaffTableDetailOut:
    """A selected table: its floor-grid row, its active session's
    orders (full snapshots, most recent first), and its
    open/acknowledged help calls. Unknown table or wrong restaurant
    -> 404."""
    restaurant = db.get(Restaurant, restaurant_id)
    if restaurant is None:
        raise ApiError(404, "not_found", "Restaurant not found.")
    table = db.get(DiningTable, table_id)
    if table is None or table.restaurant_id != restaurant_id:
        raise ApiError(404, "not_found", "Table not found.")
    session = _active_session_for_table(db, table.id)
    orders: list[KitchenOrderOut] = []
    help_calls: list[StaffHelpCallOut] = []
    if session is not None:
        rows = (
            db.execute(
                select(Order)
                .where(Order.table_session_id == session.id)
                .order_by(Order.placed_at.desc(), Order.created_at.desc())
            )
            .scalars()
            .all()
        )
        now = _now()
        orders = [_kitchen_out(db, o, now) for o in rows]
        calls = (
            db.execute(
                select(HelpCall)
                .where(
                    HelpCall.table_session_id == session.id,
                    HelpCall.status.in_(("open", "acknowledged")),
                )
                .order_by(HelpCall.created_at.asc())
            )
            .scalars()
            .all()
        )
        help_calls = [_help_call_out(db, c) for c in calls]
    return StaffTableDetailOut(
        table=_table_out(db, table, session),
        orders=orders,
        help_calls=help_calls,
    )


def list_staff_orders(
    db: Session,
    restaurant_id: UUID,
    status_filter: str | None,
    page: int,
    per_page: int,
) -> StaffOrderListResponse:
    """Active orders across the branch, oldest first. ``status``
    takes a single active order status (placed / accepted /
    preparing / ready); with no filter, all active orders."""
    restaurant = db.get(Restaurant, restaurant_id)
    if restaurant is None:
        raise ApiError(404, "not_found", "Restaurant not found.")
    if status_filter is not None and status_filter not in STAFF_ORDER_FILTERS:
        raise ApiError(
            422,
            "validation_failed",
            f"status must be one of {sorted(STAFF_ORDER_FILTERS)}.",
            {"field": "status"},
        )
    statuses = (
        STAFF_ORDER_FILTERS[status_filter]
        if status_filter is not None
        else ACTIVE_ORDER_STATUSES
    )
    base_q = select(Order).where(
        Order.restaurant_id == restaurant_id,
        Order.status.in_(statuses),
    )
    total = db.execute(
        select(func.count()).select_from(base_q.subquery())
    ).scalar_one()
    total_pages = max(1, (total + per_page - 1) // per_page)
    orders = (
        db.execute(
            base_q.order_by(Order.placed_at.asc(), Order.created_at.asc())
            .offset((page - 1) * per_page)
            .limit(per_page)
        )
        .scalars()
        .all()
    )
    now = _now()
    return StaffOrderListResponse(
        data=[_kitchen_out(db, o, now) for o in orders],
        pagination=Pagination(
            page=page, per_page=per_page, total=total, total_pages=total_pages
        ),
    )


def serve_order(
    db: Session, restaurant_id: UUID, order_id: UUID
) -> tuple[KitchenOrderOut, Order]:
    """Mark a ready order served, with its items, in ONE transaction.
    Row-locks the order so concurrent serves serialise (the second
    sees the new state and 409s).

    Allowed: ready -> served ONLY. Everything else -> 409
    ``conflict`` naming the current + requested status, with state
    unchanged. Unknown order (or wrong restaurant) -> 404.

    On serve: order status + ``served_at`` + all non-cancelled items
    to ``served`` + ``version`` += 1, one commit. Serving does NOT
    close the table session and does NOT change the table status.
    Returns (serialised order, ORM order) — the router needs the ORM
    row for the broadcast event.
    """
    restaurant = db.get(Restaurant, restaurant_id)
    if restaurant is None:
        raise ApiError(404, "not_found", "Restaurant not found.")
    order = (
        db.execute(
            select(Order)
            .where(Order.id == order_id, Order.restaurant_id == restaurant_id)
            .with_for_update()
        )
        .scalars()
        .first()
    )
    if order is None:
        raise ApiError(404, "not_found", "Order not found.")
    if order.status != "ready":
        raise ApiError(
            409,
            "conflict",
            "Only a ready order can be marked served "
            f"(current status: {order.status}).",
            {"current_status": order.status, "requested_status": "served"},
        )
    order.status = "served"
    order.served_at = _now()
    order.version += 1
    for item in order.items:
        if item.status != "cancelled":
            item.status = "served"
    db.commit()
    db.refresh(order)
    return _kitchen_out(db, order), order


def list_help_calls(
    db: Session,
    restaurant_id: UUID,
    status_filter: str | None,
    page: int,
    per_page: int,
) -> StaffHelpCallListResponse:
    """Help calls for the restaurant, oldest first. Default (no
    filter) = unresolved only (open + acknowledged)."""
    restaurant = db.get(Restaurant, restaurant_id)
    if restaurant is None:
        raise ApiError(404, "not_found", "Restaurant not found.")
    if status_filter is not None and status_filter not in HELP_CALL_FILTERS:
        raise ApiError(
            422,
            "validation_failed",
            f"status must be one of {sorted(HELP_CALL_FILTERS)}.",
            {"field": "status"},
        )
    statuses = (
        HELP_CALL_FILTERS[status_filter]
        if status_filter is not None
        else HELP_CALL_FILTERS["unresolved"]
    )
    base_q = select(HelpCall).where(
        HelpCall.restaurant_id == restaurant_id,
        HelpCall.status.in_(statuses),
    )
    total = db.execute(
        select(func.count()).select_from(base_q.subquery())
    ).scalar_one()
    total_pages = max(1, (total + per_page - 1) // per_page)
    calls = (
        db.execute(
            base_q.order_by(HelpCall.created_at.asc())
            .offset((page - 1) * per_page)
            .limit(per_page)
        )
        .scalars()
        .all()
    )
    return StaffHelpCallListResponse(
        data=[_help_call_out(db, c) for c in calls],
        pagination=Pagination(
            page=page, per_page=per_page, total=total, total_pages=total_pages
        ),
    )


def _get_help_call(db: Session, restaurant_id: UUID, call_id: UUID) -> HelpCall:
    """Row-locked help-call fetch. Unknown id or wrong restaurant
    -> 404 (no leakage)."""
    call = (
        db.execute(
            select(HelpCall)
            .where(
                HelpCall.id == call_id,
                HelpCall.restaurant_id == restaurant_id,
            )
            .with_for_update()
        )
        .scalars()
        .first()
    )
    if call is None:
        raise ApiError(404, "not_found", "Help call not found.")
    return call


def acknowledge_help_call(
    db: Session, restaurant_id: UUID, call_id: UUID
) -> tuple[StaffHelpCallOut, HelpCall]:
    """open -> acknowledged ONLY. Any other state -> 409 with state
    unchanged (including a duplicate acknowledge)."""
    restaurant = db.get(Restaurant, restaurant_id)
    if restaurant is None:
        raise ApiError(404, "not_found", "Restaurant not found.")
    call = _get_help_call(db, restaurant_id, call_id)
    if call.status != "open":
        raise ApiError(
            409,
            "conflict",
            "Only an open help call can be acknowledged "
            f"(current status: {call.status}).",
            {"current_status": call.status, "requested_status": "acknowledged"},
        )
    call.status = "acknowledged"
    call.acknowledged_at = _now()
    db.commit()
    db.refresh(call)
    return _help_call_out(db, call), call


def resolve_help_call(
    db: Session, restaurant_id: UUID, call_id: UUID
) -> tuple[StaffHelpCallOut, HelpCall]:
    """acknowledged -> resolved ONLY (api-plan.md § 5) — a waiter must
    acknowledge first; open -> resolved is rejected. Any other state
    -> 409 with state unchanged (including a duplicate resolve)."""
    restaurant = db.get(Restaurant, restaurant_id)
    if restaurant is None:
        raise ApiError(404, "not_found", "Restaurant not found.")
    call = _get_help_call(db, restaurant_id, call_id)
    if call.status != "acknowledged":
        raise ApiError(
            409,
            "conflict",
            "Only an acknowledged help call can be resolved "
            f"(current status: {call.status}).",
            {"current_status": call.status, "requested_status": "resolved"},
        )
    call.status = "resolved"
    call.resolved_at = _now()
    db.commit()
    db.refresh(call)
    return _help_call_out(db, call), call
