"""Admin routes — POST /api/v1/admin/* (Stage 7).

Every route is protected server-side by ``require_permission``;
frontend route-hiding is display-only. ``/admin`` has NO demo
mode — admin data is real by definition (unlike /kitchen and
/staff, which keep a dev ``?demo=1`` preview).
"""
import csv
import io
from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query
from fastapi.responses import JSONResponse, Response
from sqlalchemy.orm import Session

from app.auth.deps import get_current_user, require_permission
from app.auth.models import User
from app.core.deps import get_db
from app.core.errors import ApiError
from app.models import Bill
from app.realtime.manager import manager
from app.schemas.admin import (
    AddOnCreateRequest,
    AddOnDataResponse,
    AddOnUpdateRequest,
    AdminBillListResponse,
    AdminOrderListResponse,
    AdminPaymentListResponse,
    AdminRefundListResponse,
    AuditLogListResponse,
    BillDetailResponse,
    BranchDataResponse,
    BranchListResponse,
    BranchUpdateRequest,
    CategoryCreateRequest,
    CategoryDataResponse,
    CategoryListResponse,
    CategoryReorderRequest,
    CategoryUpdateRequest,
    DailyCashResponse,
    DashboardResponse,
    MenuItemCreateRequest,
    MenuItemDataResponse,
    MenuItemListResponse,
    MenuItemUpdateRequest,
    RefundCreateRequest,
    RefundDataResponse,
    RestaurantDataResponse,
    RestaurantUpdateRequest,
    TableCreateRequest,
    TableDataResponse,
    TableListResponse,
    TaxConfigCreateRequest,
    TaxConfigResponse,
    VariantCreateRequest,
    VariantDataResponse,
    VariantUpdateRequest,
    VoidBillRequest,
)
from app.schemas.auth import (
    UserCreateRequest,
    UserDataResponse,
    UserListResponse,
    UserResetPasswordRequest,
    UserUpdateRequest,
)
from app.services import admin_service
from app.services.billing_service import _bill_out

router = APIRouter(prefix="/admin", tags=["admin"])

_view_dashboard = require_permission("view_dashboard")
_manage_menu = require_permission("manage_menu")
_manage_staff = require_permission("manage_staff")
_manage_settings = require_permission("manage_settings")
_view_financials = require_permission("view_financials")
_configure_tax = require_permission("configure_tax")
_issue_refunds = require_permission("issue_refunds")
_void_bills = require_permission("void_bills")


def _page(page: int, per_page: int) -> tuple[int, int]:
    return page, per_page


# --- Dashboard + history ---------------------------------------------------

@router.get("/dashboard/overview", response_model=DashboardResponse)
def dashboard_overview(
    restaurant_id: UUID | None = Query(default=None),
    branch_id: UUID | None = Query(default=None),
    preset: str = Query(default="today"),
    from_date: date | None = Query(default=None, alias="from"),
    to_date: date | None = Query(default=None, alias="to"),
    db: Session = Depends(get_db),
    user: User = Depends(_view_dashboard),
):
    return DashboardResponse(
        data=admin_service.dashboard_overview(
            db, user, restaurant_id, branch_id, preset, from_date, to_date
        )
    )


@router.get("/orders", response_model=AdminOrderListResponse)
def admin_orders(
    restaurant_id: UUID | None = Query(default=None),
    branch_id: UUID | None = Query(default=None),
    status: str | None = Query(default=None),
    table_id: UUID | None = Query(default=None),
    from_date: date | None = Query(default=None, alias="from"),
    to_date: date | None = Query(default=None, alias="to"),
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    user: User = Depends(_view_dashboard),
):
    return admin_service.list_admin_orders(
        db, user, restaurant_id, branch_id, status, table_id,
        from_date, to_date, page, per_page,
    )


@router.get("/bills", response_model=AdminBillListResponse)
def admin_bills(
    restaurant_id: UUID | None = Query(default=None),
    branch_id: UUID | None = Query(default=None),
    status: str | None = Query(default=None),
    table_id: UUID | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    user: User = Depends(_view_dashboard),
):
    return admin_service.list_admin_bills(
        db, user, restaurant_id, branch_id, status, table_id, page, per_page
    )


@router.get("/bills/{bill_id}", response_model=BillDetailResponse)
def admin_bill_detail(
    bill_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(_view_dashboard),
):
    from app.services.admin_service import _get_bill_scoped

    bill = _get_bill_scoped(db, user.restaurant_id, bill_id)
    return BillDetailResponse(data=_bill_out(db, bill))


@router.get("/payments", response_model=AdminPaymentListResponse)
def admin_payments(
    restaurant_id: UUID | None = Query(default=None),
    branch_id: UUID | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    user: User = Depends(_view_financials),
):
    return admin_service.list_admin_payments(
        db, user, restaurant_id, branch_id, page, per_page
    )


@router.get("/refunds", response_model=AdminRefundListResponse)
def admin_refunds(
    restaurant_id: UUID | None = Query(default=None),
    branch_id: UUID | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    user: User = Depends(_view_financials),
):
    return admin_service.list_admin_refunds(
        db, user, restaurant_id, branch_id, page, per_page
    )


@router.get("/reports/daily-cash")
def daily_cash_report(
    date_: date = Query(alias="date"),
    restaurant_id: UUID | None = Query(default=None),
    branch_id: UUID | None = Query(default=None),
    format: str = Query(default="json"),
    db: Session = Depends(get_db),
    user: User = Depends(_view_financials),
):
    report = admin_service.daily_cash_report(db, user, restaurant_id, branch_id, date_)
    if format == "csv":
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow([
            "date", "timezone", "gross_cash_collected_paise",
            "cash_refunds_paise", "net_cash_paise", "outstanding_paise",
            "bill_count", "payment_count", "refund_count",
        ])
        writer.writerow([
            report.date, report.timezone, report.gross_cash_collected_paise,
            report.cash_refunds_paise, report.net_cash_paise,
            report.outstanding_paise, report.bill_count,
            report.payment_count, report.refund_count,
        ])
        return JSONResponse(
            content={"data": {"csv": buf.getvalue()}},
            headers={"Content-Type": "text/csv"},
        )
    return DailyCashResponse(data=report)


# --- Bills: void + refunds ---------------------------------------------------

async def _broadcast_bill_updated(db: Session, bill_id: UUID) -> None:
    """Stage 8: a bill whose status changed (void / refund) tells
    the table's guest sockets via ``bill.updated`` — the Bill page
    shows the voided/refunded state live instead of waiting for a
    refetch. The bill row is re-read after the service committed;
    a bill with no session (or an already-closed one) has no guest
    sockets to tell."""
    bill = db.get(Bill, bill_id)
    if bill is None or bill.table_session_id is None:
        return
    await manager.broadcast_guest(
        bill.table_session_id,
        "bill.updated",
        bill.restaurant_id,
        bill.table_id,
        {
            "bill_id": str(bill.id),
            "bill_number": bill.bill_number,
            "status": bill.status,
            "total_paise": bill.total_paise,
            "outstanding_paise": max(
                0, bill.total_paise - bill.amount_paid_paise
            ),
        },
    )


@router.post("/bills/{bill_id}/void", response_model=BillDetailResponse)
async def void_bill(
    bill_id: UUID,
    body: VoidBillRequest,
    db: Session = Depends(get_db),
    user: User = Depends(_void_bills),
):
    result = BillDetailResponse(
        data=admin_service.void_bill(db, user, bill_id, body.reason)
    )
    # Stage 8 (NEW — void broadcast nothing before): the guest
    # sees the void live and can request a new bill.
    await _broadcast_bill_updated(db, bill_id)
    return result


@router.post("/bills/{bill_id}/refunds", status_code=201)
async def create_refund(
    bill_id: UUID,
    body: RefundCreateRequest,
    db: Session = Depends(get_db),
    user: User = Depends(_issue_refunds),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> JSONResponse:
    if not idempotency_key:
        raise ApiError(
            422, "idempotency_key_required",
            "Idempotency-Key header is required for refunds.",
            {"field": "Idempotency-Key"},
        )
    response_body, status, replayed = admin_service.create_refund(
        db, user, bill_id, body.payment_id, body.amount_paise,
        body.reason, idempotency_key,
    )
    # Stage 8 (NEW — refunds broadcast nothing before): a real
    # refund (not an idempotent replay) tells the table's guest
    # sockets the bill changed.
    if not replayed:
        await _broadcast_bill_updated(db, bill_id)
    headers = {"Idempotency-Replayed": "true"} if replayed else {}
    return JSONResponse(content=response_body, status_code=status, headers=headers)


# --- Menu ----------------------------------------------------------------------

@router.get("/menu/categories", response_model=CategoryListResponse)
def list_categories(
    db: Session = Depends(get_db), user: User = Depends(_manage_menu)
):
    return CategoryListResponse(data=admin_service.list_categories(db, user))


@router.post("/menu/categories", response_model=CategoryDataResponse, status_code=201)
def create_category(
    body: CategoryCreateRequest,
    db: Session = Depends(get_db), user: User = Depends(_manage_menu),
):
    return CategoryDataResponse(
        data=admin_service.create_category(
            db, user, body.name, body.description, body.sort_order
        )
    )


@router.post("/menu/categories/reorder", response_model=CategoryListResponse)
def reorder_categories(
    body: CategoryReorderRequest,
    db: Session = Depends(get_db), user: User = Depends(_manage_menu),
):
    return CategoryListResponse(
        data=admin_service.reorder_categories(db, user, body.category_ids)
    )


@router.patch("/menu/categories/{category_id}", response_model=CategoryDataResponse)
def update_category(
    category_id: UUID,
    body: CategoryUpdateRequest,
    db: Session = Depends(get_db), user: User = Depends(_manage_menu),
):
    return CategoryDataResponse(
        data=admin_service.update_category(
            db, user, category_id, **body.model_dump(exclude_unset=True)
        )
    )


@router.get("/menu/items", response_model=MenuItemListResponse)
def list_menu_items(
    category_id: UUID | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db), user: User = Depends(_manage_menu),
):
    return admin_service.list_menu_items(db, user, category_id, page, per_page)


@router.post("/menu/items", response_model=MenuItemDataResponse, status_code=201)
def create_menu_item(
    body: MenuItemCreateRequest,
    db: Session = Depends(get_db), user: User = Depends(_manage_menu),
):
    return MenuItemDataResponse(
        data=admin_service.create_menu_item(db, user, **body.model_dump())
    )


@router.patch("/menu/items/{item_id}", response_model=MenuItemDataResponse)
def update_menu_item(
    item_id: UUID,
    body: MenuItemUpdateRequest,
    db: Session = Depends(get_db), user: User = Depends(_manage_menu),
):
    return MenuItemDataResponse(
        data=admin_service.update_menu_item(
            db, user, item_id, **body.model_dump(exclude_unset=True)
        )
    )


@router.post("/menu/items/{item_id}/variants", response_model=VariantDataResponse, status_code=201)
def create_variant(
    item_id: UUID,
    body: VariantCreateRequest,
    db: Session = Depends(get_db), user: User = Depends(_manage_menu),
):
    return VariantDataResponse(
        data=admin_service.create_variant(
            db, user, item_id, body.name, body.price_delta_paise,
            body.sort_order, body.is_available,
        )
    )


@router.patch("/menu/items/{item_id}/variants/{variant_id}", response_model=VariantDataResponse)
def update_variant(
    item_id: UUID,
    variant_id: UUID,
    body: VariantUpdateRequest,
    db: Session = Depends(get_db), user: User = Depends(_manage_menu),
):
    return VariantDataResponse(
        data=admin_service.update_variant(
            db, user, item_id, variant_id, **body.model_dump(exclude_unset=True)
        )
    )


@router.delete("/menu/items/{item_id}/variants/{variant_id}", status_code=204)
def delete_variant(
    item_id: UUID,
    variant_id: UUID,
    db: Session = Depends(get_db), user: User = Depends(_manage_menu),
):
    admin_service.delete_variant(db, user, item_id, variant_id)
    return Response(status_code=204)


@router.post("/menu/items/{item_id}/add-ons", response_model=AddOnDataResponse, status_code=201)
def create_add_on(
    item_id: UUID,
    body: AddOnCreateRequest,
    db: Session = Depends(get_db), user: User = Depends(_manage_menu),
):
    return AddOnDataResponse(
        data=admin_service.create_add_on(
            db, user, item_id, body.name, body.price_paise, body.is_available
        )
    )


@router.patch("/menu/items/{item_id}/add-ons/{add_on_id}", response_model=AddOnDataResponse)
def update_add_on(
    item_id: UUID,
    add_on_id: UUID,
    body: AddOnUpdateRequest,
    db: Session = Depends(get_db), user: User = Depends(_manage_menu),
):
    return AddOnDataResponse(
        data=admin_service.update_add_on(
            db, user, item_id, add_on_id, **body.model_dump(exclude_unset=True)
        )
    )


@router.delete("/menu/items/{item_id}/add-ons/{add_on_id}", status_code=204)
def delete_add_on(
    item_id: UUID,
    add_on_id: UUID,
    db: Session = Depends(get_db), user: User = Depends(_manage_menu),
):
    admin_service.delete_add_on(db, user, item_id, add_on_id)
    return Response(status_code=204)


# --- Users ---------------------------------------------------------------------

@router.get("/users", response_model=UserListResponse)
def list_users(
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db), user: User = Depends(_manage_staff),
):
    return admin_service.list_users(db, user, page, per_page)


@router.post("/users", response_model=UserDataResponse, status_code=201)
def create_user(
    body: UserCreateRequest,
    db: Session = Depends(get_db), user: User = Depends(_manage_staff),
):
    flags = {
        "can_manage_staff": body.can_manage_staff,
        "can_manage_settings": body.can_manage_settings,
        "can_refund": body.can_refund,
        "can_void": body.can_void,
    }
    return UserDataResponse(
        data=admin_service.create_user(
            db, user, body.email, body.password, body.role,
            body.branch_id, flags,
        )
    )


@router.patch("/users/{user_id}", response_model=UserDataResponse)
def update_user(
    user_id: UUID,
    body: UserUpdateRequest,
    db: Session = Depends(get_db), user: User = Depends(_manage_staff),
):
    return UserDataResponse(
        data=admin_service.update_user(
            db, user, user_id, **body.model_dump(exclude_unset=True)
        )
    )


@router.post("/users/{user_id}/deactivate", response_model=UserDataResponse)
def deactivate_user(
    user_id: UUID,
    db: Session = Depends(get_db), user: User = Depends(_manage_staff),
):
    return UserDataResponse(data=admin_service.deactivate_user(db, user, user_id))


@router.post("/users/{user_id}/reset-password", response_model=UserDataResponse)
def reset_user_password(
    user_id: UUID,
    body: UserResetPasswordRequest,
    db: Session = Depends(get_db), user: User = Depends(_manage_staff),
):
    return UserDataResponse(
        data=admin_service.reset_user_password(db, user, user_id, body.password)
    )


# --- Settings + tax -------------------------------------------------------------

@router.get("/settings/restaurant", response_model=RestaurantDataResponse)
def get_restaurant_settings(
    db: Session = Depends(get_db), user: User = Depends(_manage_settings),
):
    return RestaurantDataResponse(data=admin_service.get_restaurant_settings(db, user))


@router.patch("/settings/restaurant", response_model=RestaurantDataResponse)
def update_restaurant_settings(
    body: RestaurantUpdateRequest,
    db: Session = Depends(get_db), user: User = Depends(_manage_settings),
):
    return RestaurantDataResponse(
        data=admin_service.update_restaurant_settings(
            db, user, **body.model_dump(exclude_unset=True)
        )
    )


@router.get("/settings/branches", response_model=BranchListResponse)
def list_branches(
    db: Session = Depends(get_db), user: User = Depends(_manage_settings),
):
    return BranchListResponse(data=admin_service.list_branches(db, user))


@router.patch("/settings/branches/{branch_id}", response_model=BranchDataResponse)
def update_branch_settings(
    branch_id: UUID,
    body: BranchUpdateRequest,
    db: Session = Depends(get_db), user: User = Depends(_manage_settings),
):
    return BranchDataResponse(
        data=admin_service.update_branch_settings(
            db, user, branch_id, **body.model_dump(exclude_unset=True)
        )
    )


@router.get("/settings/tables", response_model=TableListResponse)
def list_tables(
    branch_id: UUID | None = Query(default=None),
    db: Session = Depends(get_db), user: User = Depends(_manage_settings),
):
    return TableListResponse(data=admin_service.list_tables(db, user, branch_id))


@router.post("/settings/tables", response_model=TableDataResponse, status_code=201)
def create_table(
    body: TableCreateRequest,
    db: Session = Depends(get_db), user: User = Depends(_manage_settings),
):
    return TableDataResponse(
        data=admin_service.create_table(db, user, body.number, body.branch_id)
    )


@router.get("/settings/tax", response_model=TaxConfigResponse)
def get_tax(
    db: Session = Depends(get_db), user: User = Depends(_configure_tax),
):
    return admin_service.get_tax_config(db, user)


@router.post("/settings/tax", response_model=TaxConfigResponse, status_code=201)
def create_tax(
    body: TaxConfigCreateRequest,
    db: Session = Depends(get_db), user: User = Depends(_configure_tax),
):
    return admin_service.create_tax_config(
        db, user, body.enabled, body.rate_bps, body.mode,
        body.label, body.effective_from,
    )


# --- Audit log (read-only) ------------------------------------------------------

@router.get("/audit-logs", response_model=AuditLogListResponse)
def list_audit_logs(
    restaurant_id: UUID | None = Query(default=None),
    branch_id: UUID | None = Query(default=None),
    action: str | None = Query(default=None),
    entity_type: str | None = Query(default=None),
    from_date: date | None = Query(default=None, alias="from"),
    to_date: date | None = Query(default=None, alias="to"),
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    user: User = Depends(_view_dashboard),
):
    return admin_service.list_audit_logs(
        db, user, restaurant_id, branch_id, action, entity_type,
        from_date, to_date, page, per_page,
    )
