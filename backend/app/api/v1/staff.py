"""Staff routes — the waiter dashboard API (api-plan.md §§ 4.2, 5
as built in Stage 5).

ACCESS (Stage 7): ``require_staff_access`` — production requires
a JWT (waiter/owner/manager); non-production also accepts the dev
key. A guest table token grants nothing here, and a kitchen dev key
does not grant staff access either. Guest token security is
untouched by this module: guest endpoints are unchanged.

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

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.auth.deps import require_staff_access
from app.auth.models import User
from app.core.deps import get_db
from app.core.errors import ApiError
from app.core.rate_limit import STAFF_PATCH_LIMIT, limiter
from app.realtime.manager import manager
from app.schemas.billing import (
    BillDataResponse,
    PaymentCreateRequest,
    ReceiptDataResponse,
    StaffBillListResponse,
    StaffPaymentListResponse,
)
from app.schemas.staff import (
    StaffHelpCallDataResponse,
    StaffHelpCallListResponse,
    StaffOrderDataResponse,
    StaffOrderListResponse,
    StaffRestaurantsResponse,
    StaffTableDetailResponse,
    StaffTableListResponse,
)
from app.services import billing_service, staff_service
from app.services.kitchen_service import resolve_restaurant

router = APIRouter(
    tags=["staff"], dependencies=[Depends(require_staff_access)]
)


def _resolve_restaurant(db: Session, user: User | None, restaurant_id: UUID | None):
    """Resolve + scope: a JWT user may only access their own restaurant."""
    restaurant = resolve_restaurant(db, restaurant_id)
    if user is not None and restaurant.id != user.restaurant_id:
        raise ApiError(404, "not_found", "Restaurant not found.")
    return restaurant


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
    user: User | None = Depends(require_staff_access),
):
    """The floor grid: every table with its live operational state
    (session, order counts, ready flag, open help-call flag)."""
    restaurant = _resolve_restaurant(db, user, restaurant_id)
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
    user: User | None = Depends(require_staff_access),
):
    """One table: its floor-grid row, its active session's orders
    (full snapshots, most recent first), and its open help calls.
    Unknown table or wrong restaurant -> 404."""
    restaurant = _resolve_restaurant(db, user, restaurant_id)
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
    user: User | None = Depends(require_staff_access),
):
    """Active orders across the branch, oldest first. ``status``
    takes a single active order status (placed / accepted /
    preparing / ready); no filter returns all active orders."""
    restaurant = _resolve_restaurant(db, user, restaurant_id)
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
    user: User | None = Depends(require_staff_access),
):
    """Mark a ready order served. Only ready -> served; anything else
    -> 409 with state unchanged. Serving does NOT close the session
    and does NOT change the table status. ``order.status_changed``
    is broadcast only after the commit."""
    restaurant = _resolve_restaurant(db, user, restaurant_id)
    out, order = staff_service.serve_order(db, restaurant.id, order_id)
    status_payload = {
        "order_id": str(order.id),
        "order_number": order.order_number,
        "status": order.status,
        "table_number": out.table_number,
    }
    await manager.broadcast(
        order.restaurant_id,
        "order.status_changed",
        order.table_id,
        status_payload,
    )
    # Stage 8: the waiter serving the order reaches the guest live,
    # session-scoped.
    await manager.broadcast_guest(
        order.table_session_id,
        "order.status_changed",
        order.restaurant_id,
        order.table_id,
        status_payload,
    )
    return StaffOrderDataResponse(data=out)


@router.get("/staff/help-calls", response_model=StaffHelpCallListResponse)
def list_staff_help_calls(
    restaurant_id: UUID | None = Query(default=None),
    status: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    user: User | None = Depends(require_staff_access),
):
    """Help calls for the restaurant, oldest first. Default (no
    filter) = unresolved only (open + acknowledged)."""
    restaurant = _resolve_restaurant(db, user, restaurant_id)
    return staff_service.list_help_calls(
        db, restaurant.id, status, page, per_page
    )


# --- Billing (Stage 6, api-plan.md § 7 as built) ------------------------
# Same dev-only gate as the rest of this router (production -> 503).
# Cash only: there is no guest endpoint that creates a payment, and
# a guest table token on these paths is a 401 (no staff key) — the
# dev gate runs before any billing logic. Broadcast-after-commit:
# payment.recorded on every new payment, bill.paid as well when the
# bill is fully paid. A replay broadcasts NOTHING (no new payment).


@router.get("/staff/bills", response_model=StaffBillListResponse)
def list_staff_bills(
    restaurant_id: UUID | None = Query(default=None),
    status: str | None = Query(default=None),
    table_id: UUID | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    user: User | None = Depends(require_staff_access),
):
    """Bill queue: payment_requested first, then issued, newest
    first. Each row carries table, bill number, totals, outstanding
    and request time."""
    restaurant = _resolve_restaurant(db, user, restaurant_id)
    return billing_service.list_bills(
        db, restaurant.id, status, table_id, page, per_page
    )


@router.get("/staff/bills/{bill_id}", response_model=BillDataResponse)
def get_staff_bill(
    bill_id: UUID,
    restaurant_id: UUID | None = Query(default=None),
    db: Session = Depends(get_db),
    user: User | None = Depends(require_staff_access),
):
    """Full bill: items, splits, payment history, totals,
    outstanding, table/session info. Unknown id or wrong
    restaurant -> 404."""
    restaurant = _resolve_restaurant(db, user, restaurant_id)
    return BillDataResponse(
        data=billing_service.get_staff_bill(db, restaurant.id, bill_id)
    )


@router.post("/staff/bills/{bill_id}/payments", status_code=201)
@limiter.limit(STAFF_PATCH_LIMIT)
async def record_staff_payment(
    request: Request,
    bill_id: UUID,
    body: PaymentCreateRequest,
    restaurant_id: UUID | None = Query(default=None),
    db: Session = Depends(get_db),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    user: User | None = Depends(require_staff_access),
) -> JSONResponse:
    """Confirm cash collection for a bill (or one split). Cash only:
    ``method`` is not accepted from the client except as 'cash'
    (anything else is a 422 at the schema). ``Idempotency-Key`` is
    REQUIRED (missing -> 422). Same key + same body replays the
    original payment (``Idempotency-Replayed: true``); same key +
    different body -> 422; a second confirmation for the same split
    with a NEW key -> 409 with the existing payment."""
    if not idempotency_key:
        raise ApiError(
            422,
            "idempotency_key_required",
            "Idempotency-Key header is required for payment confirmation.",
            {"field": "Idempotency-Key"},
        )
    restaurant = _resolve_restaurant(db, user, restaurant_id)
    response_body, status, replayed, event = billing_service.record_payment(
        db, restaurant.id, bill_id, body, idempotency_key,
        collected_by=user.id if user is not None else None,
    )
    if event is not None:
        payment_payload = {
            "bill_id": event["bill_id"],
            "bill_number": event["bill_number"],
            "payment_id": event["payment_id"],
            "receipt_number": event["receipt_number"],
            "amount_paise": event["amount_paise"],
            "bill_status": event["bill_status"],
        }
        await manager.broadcast(
            restaurant.id,
            "payment.recorded",
            event["table_id"],
            payment_payload,
        )
        # Stage 8: the paying table's guest sockets see the payment
        # live (outstanding updates, split progress), session-scoped.
        await manager.broadcast_guest(
            UUID(event["table_session_id"]),
            "payment.recorded",
            restaurant.id,
            UUID(event["table_id"]),
            payment_payload,
        )
        if event["fully_paid"]:
            paid_payload = {
                "bill_id": event["bill_id"],
                "bill_number": event["bill_number"],
                "receipt_number": event["receipt_number"],
                "amount_paise": event["amount_paise"],
            }
            await manager.broadcast(
                restaurant.id,
                "bill.paid",
                event["table_id"],
                paid_payload,
            )
            await manager.broadcast_guest(
                UUID(event["table_session_id"]),
                "bill.paid",
                restaurant.id,
                UUID(event["table_id"]),
                paid_payload,
            )
    headers = {"Idempotency-Replayed": "true"} if replayed else {}
    return JSONResponse(content=response_body, status_code=status, headers=headers)


@router.get(
    "/staff/bills/{bill_id}/payments", response_model=StaffPaymentListResponse
)
def list_staff_bill_payments(
    bill_id: UUID,
    restaurant_id: UUID | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    user: User | None = Depends(require_staff_access),
):
    """Payment history for a bill, newest first."""
    restaurant = _resolve_restaurant(db, user, restaurant_id)
    return billing_service.list_staff_payments(
        db, restaurant.id, bill_id, page, per_page
    )


@router.get("/staff/bills/{bill_id}/receipt", response_model=ReceiptDataResponse)
def get_staff_bill_receipt(
    bill_id: UUID,
    restaurant_id: UUID | None = Query(default=None),
    db: Session = Depends(get_db),
    user: User | None = Depends(require_staff_access),
):
    """Receipt data — only after the bill is paid, else 409
    ``receipt_not_available``."""
    restaurant = _resolve_restaurant(db, user, restaurant_id)
    return ReceiptDataResponse(
        data=billing_service.get_staff_receipt(db, restaurant.id, bill_id)
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
    user: User | None = Depends(require_staff_access),
):
    """Acknowledge a help call: open -> acknowledged ONLY. Anything
    else -> 409 with state unchanged. ``help_call.acknowledged`` is
    broadcast only after the commit."""
    restaurant = _resolve_restaurant(db, user, restaurant_id)
    out, call = staff_service.acknowledge_help_call(
        db, restaurant.id, call_id
    )
    ack_payload = {
        "help_call_id": str(call.id),
        "table_number": out.table_number,
        "type": call.type,
        "status": call.status,
    }
    await manager.broadcast(
        call.restaurant_id,
        "help_call.acknowledged",
        call.table_id,
        ack_payload,
    )
    # Stage 8: the guest who asked for help sees the acknowledgement
    # live, session-scoped.
    await manager.broadcast_guest(
        call.table_session_id,
        "help_call.acknowledged",
        call.restaurant_id,
        call.table_id,
        ack_payload,
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
    user: User | None = Depends(require_staff_access),
):
    """Resolve a help call: acknowledged -> resolved ONLY (a waiter
    must acknowledge first). Anything else -> 409 with state
    unchanged. ``help_call.resolved`` is broadcast only after the
    commit."""
    restaurant = _resolve_restaurant(db, user, restaurant_id)
    out, call = staff_service.resolve_help_call(db, restaurant.id, call_id)
    resolve_payload = {
        "help_call_id": str(call.id),
        "table_number": out.table_number,
        "type": call.type,
        "status": call.status,
    }
    await manager.broadcast(
        call.restaurant_id,
        "help_call.resolved",
        call.table_id,
        resolve_payload,
    )
    # Stage 8: the guest sees the resolution live, session-scoped.
    await manager.broadcast_guest(
        call.table_session_id,
        "help_call.resolved",
        call.restaurant_id,
        call.table_id,
        resolve_payload,
    )
    return StaffHelpCallDataResponse(data=out)
