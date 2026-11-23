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
from app.services import order_service
from app.services.table_service import GuestContext

router = APIRouter(tags=["orders"])


@router.post("/orders", status_code=201)
@limiter.limit(ORDER_PLACE_LIMIT)
def place_order(
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
    headers = {"Idempotency-Replayed": "true"} if replayed else {}
    return JSONResponse(content=response_body, status_code=status, headers=headers)


@router.post("/orders/{order_id}/cancel", status_code=200)
def cancel_order(
    order_id: UUID,
    body: CancelOrderRequest | None = None,
    ctx: GuestContext = Depends(get_guest_context),
    db: Session = Depends(get_db),
) -> JSONResponse:
    result = order_service.cancel_order(
        db, ctx, order_id, body.reason if body else None
    )
    return JSONResponse(
        content=result.model_dump(by_alias=True, mode="json"), status_code=200
    )
