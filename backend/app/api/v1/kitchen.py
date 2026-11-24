"""Kitchen routes — the KDS API (api-plan.md § 6 as built in Stage 4).

ACCESS (Stage 7): ``require_kitchen_access`` — production requires
a JWT (kitchen/owner/manager); non-production also accepts the dev
key. Guest token security is untouched by this module: guest
endpoints are unchanged.

Stage 4 deviations from the blueprint (recorded in api-plan.md § 6):
- Workflow is ORDER-LEVEL: ``PATCH /kitchen/orders/:id/status`` moves
  the order AND its non-cancelled items together. There is no
  per-item status endpoint.
- No ``kitchen_tickets`` table, no stations: a "ticket" IS an order
  + its item snapshots. ``GET /kitchen/orders`` IS the ticket queue.

``restaurant_id`` rule: REQUIRED on kitchen REST endpoints EXCEPT
when omitted and exactly one restaurant exists — then that restaurant
is used. Omitted with 0 or 2+ restaurants -> 422 requiring it.

Broadcast-after-commit: the PATCH handler broadcasts
``order.status_changed`` only AFTER the service has committed the
transition (architecture.md § 3.2). A failed PATCH broadcasts
nothing.
"""
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.auth.deps import require_kitchen_access
from app.auth.models import User
from app.core.deps import get_db
from app.core.errors import ApiError
from app.core.rate_limit import KITCHEN_PATCH_LIMIT, limiter
from app.realtime.manager import manager
from app.schemas.kitchen import (
    KitchenOrderDataResponse,
    KitchenOrderListResponse,
    KitchenRestaurantsResponse,
    KitchenStatusUpdateRequest,
)
from app.services import kitchen_service

router = APIRouter(
    tags=["kitchen"], dependencies=[Depends(require_kitchen_access)]
)


def _resolve_restaurant(db: Session, user: User | None, restaurant_id: UUID | None):
    """Resolve + scope: a JWT user may only access their own restaurant."""
    restaurant = kitchen_service.resolve_restaurant(db, restaurant_id)
    if user is not None and restaurant.id != user.restaurant_id:
        raise ApiError(404, "not_found", "Restaurant not found.")
    return restaurant


@router.get("/kitchen/restaurants", response_model=KitchenRestaurantsResponse)
def list_kitchen_restaurants(db: Session = Depends(get_db), user: User | None = Depends(require_kitchen_access)):
    """Dev-only restaurant picker (id + name) so the KDS can pick the
    restaurant without hardcoding one. The frontend defaults to the
    only restaurant when there is exactly one."""
    return kitchen_service.list_restaurants(db)


@router.get("/kitchen/orders", response_model=KitchenOrderListResponse)
def list_kitchen_orders(
    restaurant_id: UUID | None = Query(default=None),
    status: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    user: User | None = Depends(require_kitchen_access),
):
    """The active queue, oldest first. ``status`` uses column names:
    ``queued`` -> placed/accepted, ``preparing`` -> preparing,
    ``ready`` -> ready; unknown values -> 422. No filter returns all
    non-terminal orders (placed/accepted/preparing/ready)."""
    restaurant = _resolve_restaurant(db, user, restaurant_id)
    return kitchen_service.list_kitchen_orders(
        db, restaurant.id, status, page, per_page
    )


@router.get(
    "/kitchen/orders/{order_id}", response_model=KitchenOrderDataResponse
)
def get_kitchen_order(
    order_id: UUID,
    restaurant_id: UUID | None = Query(default=None),
    db: Session = Depends(get_db),
    user: User | None = Depends(require_kitchen_access),
):
    """One order for the KDS. Unknown id or wrong restaurant -> 404."""
    restaurant = _resolve_restaurant(db, user, restaurant_id)
    return kitchen_service.get_kitchen_order(db, restaurant.id, order_id)


@router.patch(
    "/kitchen/orders/{order_id}/status",
    response_model=KitchenOrderDataResponse,
)
@limiter.limit(KITCHEN_PATCH_LIMIT)
async def transition_kitchen_order(
    request: Request,
    order_id: UUID,
    body: KitchenStatusUpdateRequest,
    restaurant_id: UUID | None = Query(default=None),
    db: Session = Depends(get_db),
    user: User | None = Depends(require_kitchen_access),
):
    """Move an order one kitchen step: preparing or ready only (any
    other value -> 422). Allowed: placed/accepted -> preparing,
    preparing -> ready; anything else -> 409 with current +
    requested status. The order and its non-cancelled items move
    together in one transaction; ``order.status_changed`` is
    broadcast only after that commit."""
    restaurant = _resolve_restaurant(db, user, restaurant_id)
    out, order = kitchen_service.transition_order(
        db, restaurant.id, order_id, body.status
    )
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
    return KitchenOrderDataResponse(data=out)
