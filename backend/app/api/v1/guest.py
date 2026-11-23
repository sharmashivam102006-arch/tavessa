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
from app.schemas.guest import (
    GuestMenuResponse,
    GuestSessionResponse,
    HelpCallDataResponse,
    HelpCallRequest,
    OrderDataResponse,
    OrderListResponse,
)
from app.services import help_service, order_service, table_service
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
def create_help_call(
    body: HelpCallRequest,
    ctx: GuestContext = Depends(get_guest_context),
    db: Session = Depends(get_db),
) -> HelpCallDataResponse:
    return help_service.create_help_call(db, ctx, body)
