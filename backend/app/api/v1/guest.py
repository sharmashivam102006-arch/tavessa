"""Guest routes — session open, current session, menu, guest order
reads, help calls (api-plan.md §§ 3, 4.2, 5).

All of these are scoped by the QR token: the dependency resolves the
token to a session and every query filters by that session. A guest
asking for another session's order gets a 404, not a 403 (§ 11).
"""
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.core.deps import get_db, get_guest_context
from app.core.rate_limit import SESSION_OPEN_LIMIT, limiter
from app.realtime.manager import manager
from app.schemas.billing import (
    BillDataResponse,
    ReceiptDataResponse,
    SplitCreateRequest,
)
from app.schemas.guest import (
    GuestMenuResponse,
    GuestSessionResponse,
    HelpCallDataResponse,
    HelpCallRequest,
    OrderDataResponse,
    OrderListResponse,
)
from app.services import billing_service, help_service, order_service, table_service
from app.services.table_service import GuestContext

router = APIRouter(tags=["guest"])


@router.get("/guest/t/{table_token}", response_model=GuestSessionResponse)
@limiter.limit(SESSION_OPEN_LIMIT)
def open_session(
    request: Request,
    table_token: str,
    db: Session = Depends(get_db),
) -> GuestSessionResponse:
    """Session open — validates the token, creates/resumes the session."""
    return table_service.open_guest_session(db, table_token)


@router.get("/guest/session", response_model=GuestSessionResponse)
def current_session(
    ctx: GuestContext = Depends(get_guest_context),
    db: Session = Depends(get_db),
) -> GuestSessionResponse:
    return table_service.build_session_response(db, ctx.session, ctx.raw_token)


@router.get("/guest/menu", response_model=GuestMenuResponse)
def guest_menu(
    category_id: UUID | None = Query(default=None, alias="categoryId"),
    search: str | None = Query(default=None),
    ctx: GuestContext = Depends(get_guest_context),
    db: Session = Depends(get_db),
) -> GuestMenuResponse:
    return table_service.get_menu(db, ctx.restaurant.id, category_id, search)


@router.get("/guest/orders", response_model=OrderListResponse)
def guest_orders(
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=100),
    ctx: GuestContext = Depends(get_guest_context),
    db: Session = Depends(get_db),
) -> OrderListResponse:
    return order_service.list_orders(db, ctx, page, per_page)


@router.get("/guest/orders/{order_id}", response_model=OrderDataResponse)
def guest_order(
    order_id: UUID,
    ctx: GuestContext = Depends(get_guest_context),
    db: Session = Depends(get_db),
) -> OrderDataResponse:
    return order_service.get_order(db, ctx, order_id)


@router.post("/guest/help-calls", response_model=HelpCallDataResponse, status_code=201)
async def create_help_call(
    body: HelpCallRequest,
    ctx: GuestContext = Depends(get_guest_context),
    db: Session = Depends(get_db),
) -> HelpCallDataResponse:
    result = help_service.create_help_call(db, ctx, body)
    # Broadcast AFTER the help-call transaction committed (the
    # service commits internally before returning). Staff on the
    # same restaurant channel see it; a failed creation broadcasts
    # nothing.
    await manager.broadcast(
        ctx.restaurant.id,
        "help_call.opened",
        ctx.session.table_id,
        {
            "help_call_id": str(result.data.id),
            "table_number": ctx.table.number,
            "type": result.data.type,
            "status": result.data.status,
        },
    )
    return result


# --- Billing (Stage 6, api-plan.md § 7 as built) ------------------------
# Guest bill endpoints are scoped by the QR token like every other
# guest endpoint: another session's bill is a 404, not a 403.
# Broadcast-after-commit (architecture.md § 3.2): bill.created only
# when a NEW bill was generated, bill.requested only on the actual
# issued -> payment_requested transition — an idempotent repeat
# broadcasts nothing. No guest endpoint creates a payment row.


@router.post("/guest/bill", response_model=BillDataResponse, status_code=200)
async def guest_create_bill(
    ctx: GuestContext = Depends(get_guest_context),
    db: Session = Depends(get_db),
) -> BillDataResponse:
    """Create-or-retrieve the session's bill. A new bill broadcasts
    ``bill.created`` after commit; a regenerated / returned open
    bill does not (it already exists)."""
    bill, created = billing_service.get_or_create_bill(db, ctx)
    if created:
        await manager.broadcast(
            ctx.restaurant.id,
            "bill.created",
            ctx.session.table_id,
            {
                "bill_id": str(bill.id),
                "bill_number": bill.bill_number,
                "status": bill.status,
                "table_number": ctx.table.number,
                "total_paise": bill.total_paise,
            },
        )
    return BillDataResponse(data=bill)


@router.get("/guest/bill", response_model=BillDataResponse)
def guest_get_bill(
    ctx: GuestContext = Depends(get_guest_context),
    db: Session = Depends(get_db),
) -> BillDataResponse:
    """The session's open bill (regenerated if issued), else the most
    recent paid bill; none -> 404 ``bill_not_found``."""
    return BillDataResponse(data=billing_service.get_bill_for_guest(db, ctx))


@router.patch("/guest/bill/request-payment", response_model=BillDataResponse)
async def guest_request_payment(
    ctx: GuestContext = Depends(get_guest_context),
    db: Session = Depends(get_db),
) -> BillDataResponse:
    """Guest taps "Pay by Cash": issued -> payment_requested. This
    creates NO payment row. Already payment_requested -> 200
    idempotent return; paid -> 409."""
    bill, transitioned = billing_service.request_payment(db, ctx)
    if transitioned:
        await manager.broadcast(
            ctx.restaurant.id,
            "bill.requested",
            ctx.session.table_id,
            {
                "bill_id": str(bill.id),
                "bill_number": bill.bill_number,
                "status": bill.status,
                "table_number": ctx.table.number,
                "total_paise": bill.total_paise,
                "outstanding_paise": bill.outstanding_paise,
            },
        )
    return BillDataResponse(data=bill)


@router.post("/guest/bill/splits", response_model=BillDataResponse)
def guest_create_splits(
    body: SplitCreateRequest,
    ctx: GuestContext = Depends(get_guest_context),
    db: Session = Depends(get_db),
) -> BillDataResponse:
    """Equal / custom splits, validated server-side. Replaces
    existing unpaid splits; refused once a split is paid or
    payment_requested (409)."""
    return BillDataResponse(data=billing_service.create_splits(db, ctx, body))


@router.get("/guest/bill/receipt", response_model=ReceiptDataResponse)
def guest_get_receipt(
    ctx: GuestContext = Depends(get_guest_context),
    db: Session = Depends(get_db),
) -> ReceiptDataResponse:
    """Receipt data — only after the bill is paid, else 409
    ``receipt_not_available``."""
    return ReceiptDataResponse(data=billing_service.get_receipt_for_guest(db, ctx))
