"""Staff routes — the waiter dashboard API (api-plan.md §§ 4.2, 5
as built in Stage 5).

DEV-ONLY ACCESS: every route here requires ``require_staff_dev_access``
(see ``core/deps.py``) — a SEPARATE dev key from the kitchen key, in
production these routes return 503 ``staff_auth_not_configured``
until staff auth lands. A guest table token grants nothing here, and
a kitchen dev key does not grant staff access either. Guest token
security is untouched by this module: guest endpoints are unchanged.

Scope (Stage 5): floor overview (read), active orders (read), serve
(ready -> served), help-call list / acknowledge / resolve. No billing,
no session close, no table-status transitions, no staff assignments —
those are deferred (roadmap Stage 5 report).

``restaurant_id`` rule (same as kitchen): REQUIRED on staff REST
endpoints EXCEPT when omitted and exactly one restaurant exists —
then that restaurant is used. Omitted with 0 or 2+ restaurants ->
422 requiring it.

Broadcast-after-commit: the serve / acknowledge / resolve handlers
broadcast only AFTER the service has committed (architecture.md
§ 3.2). A failed request broadcasts nothing.
"""
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.core.deps import get_db, require_staff_dev_access
from app.core.rate_limit import STAFF_PATCH_LIMIT, limiter
from app.realtime.manager import manager
from app.schemas.staff import (
    StaffHelpCallDataResponse,
    StaffHelpCallListResponse,
    StaffOrderDataResponse,
    StaffOrderListResponse,
    StaffRestaurantsResponse,
    StaffTableDetailResponse,
    StaffTableListResponse,
)
from app.services import staff_service
from app.services.kitchen_service import resolve_restaurant

router = APIRouter(
    tags=["staff"], dependencies=[Depends(require_staff_dev_access)]
)


@router.get("/staff/restaurants", response_model=StaffRestaurantsResponse)
def list_staff_restaurants(db: Session = Depends(get_db)):
    """Dev-only restaurant picker (id + name) so the dashboard can
    pick the restaurant without hardcoding one. The frontend defaults
    to the only restaurant when there is exactly one."""
    return staff_service.list_restaurants(db)


@router.get("/staff/tables", response_model=StaffTableListResponse)
def list_staff_tables(
    restaurant_id: UUID | None = Query(default=None),
    db: Session = Depends(get_db),
):
    """The floor grid: every table with its live operational state
    (session, order counts, ready flag, open help-call flag)."""
    restaurant = resolve_restaurant(db, restaurant_id)
    return StaffTableListResponse(
        data=staff_service.list_tables(db, restaurant.id)
    )


@router.get(
    "/staff/tables/{table_id}", response_model=StaffTableDetailResponse
)
def get_staff_table(
    table_id: UUID,
    restaurant_id: UUID | None = Query(default=None),
    db: Session = Depends(get_db),
):
    """One table: its floor-grid row, its active session's orders
    (full snapshots, most recent first), and its open help calls.
    Unknown table or wrong restaurant -> 404."""
    restaurant = resolve_restaurant(db, restaurant_id)
    return StaffTableDetailResponse(
        data=staff_service.get_table_detail(db, restaurant.id, table_id)
    )


@router.get("/staff/orders", response_model=StaffOrderListResponse)
def list_staff_orders(
    restaurant_id: UUID | None = Query(default=None),
    status: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """Active orders across the branch, oldest first. ``status``
    takes a single active order status (placed / accepted /
    preparing / ready); no filter returns all active orders."""
    restaurant = resolve_restaurant(db, restaurant_id)
    return staff_service.list_staff_orders(
        db, restaurant.id, status, page, per_page
    )


@router.patch("/staff/orders/{order_id}/serve", response_model=StaffOrderDataResponse)
@limiter.limit(STAFF_PATCH_LIMIT)
async def serve_staff_order(
    request: Request,
    order_id: UUID,
    restaurant_id: UUID | None = Query(default=None),
    db: Session = Depends(get_db),
):
    """Mark a ready order served. Only ready -> served; anything else
    -> 409 with state unchanged. Serving does NOT close the session
    and does NOT change the table status. ``order.status_changed``
    is broadcast only after the commit."""
    restaurant = resolve_restaurant(db, restaurant_id)
    out, order = staff_service.serve_order(db, restaurant.id, order_id)
    await manager.broadcast(
        order.restaurant_id,
        "order.status_changed",
        order.table_id,
        {
            "order_id": str(order.id),
            "order_number": order.order_number,
            "status": order.status,
            "table_number": out.table_number,
        },
    )
    return StaffOrderDataResponse(data=out)


@router.get("/staff/help-calls", response_model=StaffHelpCallListResponse)
def list_staff_help_calls(
    restaurant_id: UUID | None = Query(default=None),
    status: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """Help calls for the restaurant, oldest first. Default (no
    filter) = unresolved only (open + acknowledged)."""
    restaurant = resolve_restaurant(db, restaurant_id)
    return staff_service.list_help_calls(
        db, restaurant.id, status, page, per_page
    )


@router.patch(
    "/staff/help-calls/{call_id}/acknowledge",
    response_model=StaffHelpCallDataResponse,
)
@limiter.limit(STAFF_PATCH_LIMIT)
async def acknowledge_staff_help_call(
    request: Request,
    call_id: UUID,
    restaurant_id: UUID | None = Query(default=None),
    db: Session = Depends(get_db),
):
    """Acknowledge a help call: open -> acknowledged ONLY. Anything
    else -> 409 with state unchanged. ``help_call.acknowledged`` is
    broadcast only after the commit."""
    restaurant = resolve_restaurant(db, restaurant_id)
    out, call = staff_service.acknowledge_help_call(
        db, restaurant.id, call_id
    )
    await manager.broadcast(
        call.restaurant_id,
        "help_call.acknowledged",
        call.table_id,
        {
            "help_call_id": str(call.id),
            "table_number": out.table_number,
            "type": call.type,
            "status": call.status,
        },
    )
    return StaffHelpCallDataResponse(data=out)


@router.patch(
    "/staff/help-calls/{call_id}/resolve",
    response_model=StaffHelpCallDataResponse,
)
@limiter.limit(STAFF_PATCH_LIMIT)
async def resolve_staff_help_call(
    request: Request,
    call_id: UUID,
    restaurant_id: UUID | None = Query(default=None),
    db: Session = Depends(get_db),
):
    """Resolve a help call: acknowledged -> resolved ONLY (a waiter
    must acknowledge first). Anything else -> 409 with state
    unchanged. ``help_call.resolved`` is broadcast only after the
    commit."""
    restaurant = resolve_restaurant(db, restaurant_id)
    out, call = staff_service.resolve_help_call(db, restaurant.id, call_id)
    await manager.broadcast(
        call.restaurant_id,
        "help_call.resolved",
        call.table_id,
        {
            "help_call_id": str(call.id),
            "table_number": out.table_number,
            "type": call.type,
            "status": call.status,
        },
    )
    return StaffHelpCallDataResponse(data=out)
