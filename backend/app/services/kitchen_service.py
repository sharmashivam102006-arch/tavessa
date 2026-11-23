"""Kitchen service (Stage 4) — the KDS read + transition surface.

Stage 4 deviations from the blueprint (recorded in api-plan.md § 6
and database-design.md § 6):
- Workflow is ORDER-LEVEL, not per-item. There is no
  ``PATCH /orders/:id/items/:itemId/status``. When an order
  transitions, its non-cancelled items move with it in the SAME
  transaction (placed -> preparing: items queued -> preparing, set
  ``accepted_at`` if null; preparing -> ready: items -> ready, set
  ``ready_at``).
- NO ``kitchen_tickets`` table, NO stations. A "ticket" IS an order
  + its item snapshots. Menu items already carry a ``station`` TEXT
  field for later station grouping/filtering.
- Terminal states (served / completed / cancelled) are NOT in the
  active queue.

Every query filters by ``restaurant_id``; a restaurant that does
not exist is a 404, and an order from another restaurant is a 404
too (api-plan.md § 11: no existence leakage across tenants).

Broadcasting is NOT done here: the service stays broadcast-free so
tests can call it without a WebSocket, and so no event can ever be
published from inside (or before) a transaction. Routes broadcast
after the service returns successfully (architecture.md § 3.2).
"""
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import ApiError
from app.models import DiningTable, Order, Restaurant
from app.schemas.common import Pagination
from app.schemas.guest import OrderOut
from app.schemas.kitchen import (
    KitchenOrderDataResponse,
    KitchenOrderListResponse,
    KitchenOrderOut,
    KitchenRestaurantOut,
    KitchenRestaurantsResponse,
)
from app.services.order_service import _order_to_out
from app.services.table_service import _now

# Column-name status filter values (api-plan.md § 6): "queued" covers
# both order statuses that mean "new" on the board.
KITCHEN_STATUS_FILTERS: dict[str, tuple[str, ...]] = {
    "queued": ("placed", "accepted"),
    "preparing": ("preparing",),
    "ready": ("ready",),
}
# The active queue: everything that is not terminal.
ACTIVE_ORDER_STATUSES = ("placed", "accepted", "preparing", "ready")

# Order-level kitchen transitions ONLY (deviation 1). accepted is the
# kitchen-acknowledged step that the guest tracker already knows.
KITCHEN_TRANSITIONS: dict[str, tuple[str, ...]] = {
    "placed": ("preparing",),
    "accepted": ("preparing",),
    "preparing": ("ready",),
    "ready": (),
    "served": (),
    "completed": (),
    "cancelled": (),
}
# Item status that follows the order on each transition.
_TRANSITION_ITEM_STATUS = {"preparing": "preparing", "ready": "ready"}


def _elapsed_seconds(placed_at: datetime, now: datetime | None = None) -> int:
    """Seconds since placement. SQLite returns naive datetimes;
    Postgres returns aware ones — normalise first, as elsewhere."""
    placed = placed_at
    if placed.tzinfo is None:
        placed = placed.replace(tzinfo=timezone.utc)
    ref = now or _now()
    return max(0, int((ref - placed).total_seconds()))


def _kitchen_out(db: Session, order: Order, now: datetime | None = None) -> KitchenOrderOut:
    """Serialise an order for the KDS: the shared ``_order_to_out``
    plus the table number and server-computed elapsed seconds. The
    serialiser is NOT duplicated here."""
    base: OrderOut = _order_to_out(db, order)
    table = db.get(DiningTable, order.table_id)
    return KitchenOrderOut(
        **base.model_dump(),
        table_number=table.number if table is not None else 0,
        elapsed_seconds=_elapsed_seconds(order.placed_at, now),
    )


def resolve_restaurant(db: Session, restaurant_id: UUID | None) -> Restaurant:
    """Resolve the ``restaurant_id`` query param (api-plan.md § 6).

    - Given: the restaurant must exist, else 404.
    - Omitted and exactly one restaurant exists: use it (the
      single-restaurant demo case — the KDS does not hardcode an id).
    - Omitted and zero or 2+ exist: 422 requiring ``restaurant_id``.
    """
    if restaurant_id is not None:
        restaurant = db.get(Restaurant, restaurant_id)
        if restaurant is None:
            raise ApiError(404, "not_found", "Restaurant not found.")
        return restaurant
    total = db.execute(
        select(func.count()).select_from(Restaurant)
    ).scalar_one()
    if total == 1:
        return db.execute(select(Restaurant)).scalars().first()
    raise ApiError(
        422,
        "validation_failed",
        "restaurant_id is required: there is not exactly one restaurant.",
        {"field": "restaurant_id"},
    )


def list_restaurants(db: Session) -> KitchenRestaurantsResponse:
    """Dev-only restaurant picker for the KDS (id + name only)."""
    rows = (
        db.execute(select(Restaurant).order_by(Restaurant.name))
        .scalars()
        .all()
    )
    return KitchenRestaurantsResponse(
        data=[KitchenRestaurantOut(id=r.id, name=r.name) for r in rows]
    )


def list_kitchen_orders(
    db: Session,
    restaurant_id: UUID,
    status_filter: str | None,
    page: int,
    per_page: int,
) -> KitchenOrderListResponse:
    """The active queue, oldest first (the kitchen works the oldest
    order first). ``status_filter`` uses column names: queued ->
    placed/accepted, preparing -> preparing, ready -> ready. With no
    filter, all non-terminal orders are returned."""
    restaurant = db.get(Restaurant, restaurant_id)
    if restaurant is None:
        raise ApiError(404, "not_found", "Restaurant not found.")
    if status_filter is not None and status_filter not in KITCHEN_STATUS_FILTERS:
        raise ApiError(
            422,
            "validation_failed",
            f"status must be one of {sorted(KITCHEN_STATUS_FILTERS)}.",
            {"field": "status"},
        )
    statuses = (
        KITCHEN_STATUS_FILTERS[status_filter]
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
    return KitchenOrderListResponse(
        data=[_kitchen_out(db, o, now) for o in orders],
        pagination=Pagination(
            page=page, per_page=per_page, total=total, total_pages=total_pages
        ),
    )


def get_kitchen_order(
    db: Session, restaurant_id: UUID, order_id: UUID
) -> KitchenOrderDataResponse:
    """One order for the KDS. Unknown id OR wrong restaurant -> 404
    (no leakage)."""
    restaurant = db.get(Restaurant, restaurant_id)
    if restaurant is None:
        raise ApiError(404, "not_found", "Restaurant not found.")
    order = db.get(Order, order_id)
    if order is None or order.restaurant_id != restaurant_id:
        raise ApiError(404, "not_found", "Order not found.")
    return KitchenOrderDataResponse(data=_kitchen_out(db, order))


def transition_order(
    db: Session, restaurant_id: UUID, order_id: UUID, target: str
) -> tuple[KitchenOrderOut, Order]:
    """Move an order one kitchen step, with its items, in ONE
    transaction. Row-locks the order so two concurrent PATCHes
    serialise (the second sees the new state and 409s).

    Allowed: placed -> preparing, accepted -> preparing,
    preparing -> ready. Everything else -> 409 ``conflict`` naming
    the current + requested status, with state unchanged. Unknown
    order (or wrong restaurant) -> 404.

    On transition: order status + the matching timestamp
    (``accepted_at`` if null on -> preparing, ``ready_at`` on
    -> ready) + all non-cancelled items to the matching item status
    + ``version`` += 1, one commit. Returns (serialised order, ORM
    order) — the router needs the ORM row for the broadcast event.
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
    if target not in KITCHEN_TRANSITIONS.get(order.status, ()):
        raise ApiError(
            409,
            "conflict",
            f"Cannot move an order from {order.status} to {target}.",
            {"current_status": order.status, "requested_status": target},
        )
    item_status = _TRANSITION_ITEM_STATUS[target]
    order.status = target
    if target == "preparing" and order.accepted_at is None:
        order.accepted_at = _now()
    if target == "ready":
        order.ready_at = _now()
    order.version += 1
    for item in order.items:
        if item.status != "cancelled":
            item.status = item_status
    db.commit()
    db.refresh(order)
    return _kitchen_out(db, order), order
