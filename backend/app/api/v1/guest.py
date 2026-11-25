"""Guest routes — session open, current session, menu, guest order
reads, help calls (api-plan.md §§ 3, 4.2, 5).

All of these are scoped by the QR token: the dependency resolves the
token to a session and every query filters by that session. A guest
asking for another session's order gets a 404, not a 403 (§ 11).
"""
from uuid import UUID
from datetime import datetime, timedelta, timezone
import secrets

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.auth.models import WsTicket
from app.auth.router import WS_TICKET_TTL_SECONDS
from app.auth.tokens import hash_ws_ticket
from app.core.deps import get_db, get_guest_context
from app.core.rate_limit import SESSION_OPEN_LIMIT, limiter
from app.realtime.manager import manager
from app.schemas.auth import WsTicketResponse
from app.schemas.billing import (
    BillDataResponse,
    ReceiptDataResponse,
    SplitCreateRequest,
)
from app.schemas.guest import (
    GuestMenuResponse,
    GuestSessionResponse,
    HelpCallDataResponse,
    HelpCallListResponse,
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


@router.get("/guest/help-calls", response_model=HelpCallListResponse)
def list_help_calls(
    ctx: GuestContext = Depends(get_guest_context),
    db: Session = Depends(get_db),
) -> HelpCallListResponse:
    """The session's help calls, newest first — the guest's REST
    source of truth for assistance status (Stage 8)."""
    return help_service.list_help_calls(db, ctx)


@router.post("/guest/ws/tickets", response_model=WsTicketResponse, status_code=201)
def issue_guest_ws_ticket(
    ctx: GuestContext = Depends(get_guest_context),
    db: Session = Depends(get_db),
) -> WsTicketResponse:
    """Issue a single-use WebSocket ticket for the guest channel
    (Stage 8). Authenticated by the existing guest table-token
    mechanism (``X-Table-Token`` header) — the same dependency as
    every other guest endpoint, so an invalid token is a 401 and
    an expired session a 410 before any ticket is minted.

    The ticket is a cryptographically random opaque value
    (``secrets.token_urlsafe``); only its SHA-256 hash is stored.
    Single-use, ~60s expiry (``WS_TICKET_TTL_SECONDS``, the same
    TTL as staff/kitchen tickets). It is bound to the exact
    restaurant, branch, table and active session — the binding
    lives server-side in the ``ws_tickets`` row (``user_id`` NULL,
    ``table_session_id`` set); the client never sees the session
    UUID. The raw ticket is returned exactly once and never
    logged. A guest ticket can never open a staff/kitchen channel
    (channel is checked at connect, 4403)."""
    raw = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(
        seconds=WS_TICKET_TTL_SECONDS
    )
    db.add(
        WsTicket(
            user_id=None,
            restaurant_id=ctx.restaurant.id,
            channel="guest",
            ticket_hash=hash_ws_ticket(raw),
            expires_at=expires_at,
            table_session_id=ctx.session.id,
        )
    )
    db.commit()
    return WsTicketResponse(
        data={"ticket": raw, "channel": "guest", "expires_at": expires_at}
    )


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
    # nothing. Stage 8: the requesting table's guest sockets get
    # it too, session-scoped (their Help page shows "open" live).
    help_payload = {
        "help_call_id": str(result.data.id),
        "table_number": ctx.table.number,
        "type": result.data.type,
        "status": result.data.status,
    }
    await manager.broadcast(
        ctx.restaurant.id,
        "help_call.opened",
        ctx.session.table_id,
        help_payload,
    )
    await manager.broadcast_guest(
        ctx.session.id,
        "help_call.opened",
        ctx.restaurant.id,
        ctx.session.table_id,
        help_payload,
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
        created_payload = {
            "bill_id": str(bill.id),
            "bill_number": bill.bill_number,
            "status": bill.status,
            "table_number": ctx.table.number,
            "total_paise": bill.total_paise,
        }
        await manager.broadcast(
            ctx.restaurant.id,
            "bill.created",
            ctx.session.table_id,
            created_payload,
        )
        # Stage 8: the table's guest sockets see their bill appear,
        # session-scoped.
        await manager.broadcast_guest(
            ctx.session.id,
            "bill.created",
            ctx.restaurant.id,
            ctx.session.table_id,
            created_payload,
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
        requested_payload = {
            "bill_id": str(bill.id),
            "bill_number": bill.bill_number,
            "status": bill.status,
            "table_number": ctx.table.number,
            "total_paise": bill.total_paise,
            "outstanding_paise": bill.outstanding_paise,
        }
        await manager.broadcast(
            ctx.restaurant.id,
            "bill.requested",
            ctx.session.table_id,
            requested_payload,
        )
        # Stage 8: the guest channel carries status changes as
        # ``bill.updated`` (the guest event list has no
        # bill.requested) — the Bill page shows "payment requested"
        # live, session-scoped.
        await manager.broadcast_guest(
            ctx.session.id,
            "bill.updated",
            ctx.restaurant.id,
            ctx.session.table_id,
            requested_payload,
        )
    return BillDataResponse(data=bill)


@router.post("/guest/bill/splits", response_model=BillDataResponse)
async def guest_create_splits(
    body: SplitCreateRequest,
    ctx: GuestContext = Depends(get_guest_context),
    db: Session = Depends(get_db),
) -> BillDataResponse:
    """Equal / custom splits, validated server-side. Replaces
    existing unpaid splits; refused once a split is paid or
    payment_requested (409)."""
    bill = billing_service.create_splits(db, ctx, body)
    # Stage 8 (NEW — splits changed nothing before): the guest
    # channel gets ``bill.updated`` so the Bill page shows the new
    # split amounts live. Broadcast AFTER the splits transaction
    # committed (the service commits internally before returning).
    await manager.broadcast_guest(
        ctx.session.id,
        "bill.updated",
        ctx.restaurant.id,
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


@router.get("/guest/bill/receipt", response_model=ReceiptDataResponse)
def guest_get_receipt(
    ctx: GuestContext = Depends(get_guest_context),
    db: Session = Depends(get_db),
) -> ReceiptDataResponse:
    """Receipt data — only after the bill is paid, else 409
    ``receipt_not_available``."""
    return ReceiptDataResponse(data=billing_service.get_receipt_for_guest(db, ctx))
