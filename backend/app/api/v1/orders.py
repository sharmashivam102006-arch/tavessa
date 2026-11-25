"""Order placement + guest cancel (api-plan.md § 4).

POST /orders requires an Idempotency-Key header (missing -> 422
idempotency_key_required). The response is returned as a plain
JSONResponse so the idempotency replay header can be set exactly:
replays carry ``Idempotency-Replayed: true`` and the original status.
"""
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.core.deps import get_db, get_guest_context
from app.core.errors import ApiError
from app.core.rate_limit import ORDER_PLACE_LIMIT, limiter
from app.schemas.guest import CancelOrderRequest, OrderPlacementRequest
from app.realtime.manager import manager
from app.services import order_service
from app.services.table_service import GuestContext

router = APIRouter(tags=["orders"])


@router.post("/orders", status_code=201)
@limiter.limit(ORDER_PLACE_LIMIT)
async def place_order(
    request: Request,
    body: OrderPlacementRequest,
    ctx: GuestContext = Depends(get_guest_context),
    db: Session = Depends(get_db),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> JSONResponse:
    if not idempotency_key:
        raise ApiError(
            422,
            "idempotency_key_required",
            "Idempotency-Key header is required for order placement.",
            {"field": "Idempotency-Key"},
        )
    response_body, status, replayed = order_service.place_order(
        db, ctx, body, idempotency_key
    )
    if not replayed:
        # Broadcast AFTER the placement transaction committed (the
        # service commits internally before returning). Replays are
        # NOT broadcast — no new order exists. The service stays
        # broadcast-free so tests can call it without a WebSocket.
        placed = response_body["order"]
        order_payload = {
            "order_id": placed["id"],
            "order_number": placed["orderNumber"],
            "status": placed["status"],
            "table_number": ctx.table.number,
        }
        await manager.broadcast(
            ctx.restaurant.id,
            "order.created",
            ctx.table.id,
            order_payload,
        )
        # Stage 8: the placing table's own guest sockets get the
        # same event, session-scoped — no other table sees it.
        await manager.broadcast_guest(
            ctx.session.id,
            "order.created",
            ctx.restaurant.id,
            ctx.table.id,
            order_payload,
        )
    headers = {"Idempotency-Replayed": "true"} if replayed else {}
    return JSONResponse(content=response_body, status_code=status, headers=headers)


@router.post("/orders/{order_id}/cancel", status_code=200)
async def cancel_order(
    order_id: UUID,
    body: CancelOrderRequest | None = None,
    ctx: GuestContext = Depends(get_guest_context),
    db: Session = Depends(get_db),
) -> JSONResponse:
    result = order_service.cancel_order(
        db, ctx, order_id, body.reason if body else None
    )
    # Stage 8 (NEW — cancel broadcast nothing before): the kitchen
    # must drop the ticket and the waiter must stop watching it, so
    # ``order.cancelled`` goes to the restaurant channel; the
    # cancelling table's guest sockets get it session-scoped.
    # Broadcast AFTER the cancel transaction committed (the service
    # commits internally before returning).
    payload = {
        "order_id": str(order_id),
        "order_number": result.data.order_number,
        "status": "cancelled",
        "table_number": ctx.table.number,
    }
    await manager.broadcast(
        ctx.restaurant.id, "order.cancelled", ctx.table.id, payload
    )
    await manager.broadcast_guest(
        ctx.session.id,
        "order.cancelled",
        ctx.restaurant.id,
        ctx.table.id,
        payload,
    )
    return JSONResponse(
        content=result.model_dump(by_alias=True, mode="json"), status_code=200
    )
