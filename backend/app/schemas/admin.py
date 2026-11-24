"""Admin schemas (Stage 7). Wire format is camelCase."""
import re
from datetime import datetime
from uuid import UUID

from pydantic import Field, field_validator

from app.schemas.auth import UserOut
from app.schemas.billing import BillOut, PaymentOut
from app.schemas.common import CamelModel, Pagination

# --- Dashboard ------------------------------------------------------------

class OrdersByStatusOut(CamelModel):
    status: str
    count: int


class RecentPaymentOut(CamelModel):
    id: UUID
    bill_id: UUID
    bill_number: str
    table_number: int
    amount_paise: int
    receipt_number: str
    collected_at: datetime
    collected_by_name: str | None = None


class RecentActivityOut(CamelModel):
    id: UUID
    action: str
    entity_type: str | None = None
    entity_id: UUID | None = None
    details: dict | None = None
    created_at: datetime


class BestSellerOut(CamelModel):
    menu_item_id: UUID
    name: str
    quantity: int
    revenue_paise: int


class DashboardOverviewOut(CamelModel):
    restaurant_id: UUID
    branch_id: UUID | None = None
    period_start: datetime
    period_end: datetime
    preset: str
    timezone: str
    # Money definitions (see api-plan.md § 8): gross_sales is the
    # SUM of totals of non-voided bills issued in the period —
    # NOT cash collected. cash_collected is SUM of confirmed
    # payments collected in the period. refunds is SUM of refunds
    # in the period. net_cash = collected - refunds. outstanding
    # is the SUM of outstanding on open bills AS OF NOW (not
    # period-bound).
    gross_sales_paise: int
    cash_collected_paise: int
    refunds_paise: int
    net_cash_paise: int
    outstanding_paise: int
    bill_count: int
    order_count: int
    average_order_value_paise: int
    orders_by_status: list[OrdersByStatusOut] = []
    recent_payments: list[RecentPaymentOut] = []
    recent_activity: list[RecentActivityOut] = []
    best_sellers: list[BestSellerOut] = []


class DashboardResponse(CamelModel):
    data: DashboardOverviewOut


# --- History --------------------------------------------------------------

class AdminOrderOut(CamelModel):
    id: UUID
    order_number: str
    table_number: int
    table_session_id: UUID
    status: str
    subtotal_paise: int
    total_paise: int
    placed_at: datetime
    item_count: int


class AdminOrderListResponse(CamelModel):
    data: list[AdminOrderOut]
    pagination: Pagination


class AdminBillOut(CamelModel):
    id: UUID
    bill_number: str
    table_number: int
    status: str
    total_paise: int
    amount_paid_paise: int
    collected_paise: int
    outstanding_paise: int
    refund_paise: int
    net_collected_paise: int
    issued_at: datetime
    paid_at: datetime | None = None
    voided_at: datetime | None = None
    void_reason: str | None = None


class AdminBillListResponse(CamelModel):
    data: list[AdminBillOut]
    pagination: Pagination


class AdminPaymentOut(CamelModel):
    id: UUID
    bill_id: UUID
    bill_number: str
    receipt_number: str
    amount_paise: int
    method: str
    status: str
    collected_at: datetime
    collected_by_name: str | None = None


class AdminPaymentListResponse(CamelModel):
    data: list[AdminPaymentOut]
    pagination: Pagination


class AdminRefundOut(CamelModel):
    id: UUID
    refund_reference: str
    bill_id: UUID
    bill_number: str
    payment_id: UUID
    receipt_number: str | None = None
    amount_paise: int
    reason: str
    refunded_by_name: str | None = None
    refunded_at: datetime


class AdminRefundListResponse(CamelModel):
    data: list[AdminRefundOut]
    pagination: Pagination


class DailyCashOut(CamelModel):
    date: str
    timezone: str
    period_start: datetime
    period_end: datetime
    gross_cash_collected_paise: int
    cash_refunds_paise: int
    net_cash_paise: int
    outstanding_paise: int
    bill_count: int
    payment_count: int
    refund_count: int


class DailyCashResponse(CamelModel):
    data: DailyCashOut


class VoidBillRequest(CamelModel):
    model_config = CamelModel.model_config | {"extra": "forbid"}
    reason: str = Field(min_length=1, max_length=500)


class RefundCreateRequest(CamelModel):
    model_config = CamelModel.model_config | {"extra": "forbid"}
    payment_id: UUID
    amount_paise: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=500)


class RefundDataResponse(CamelModel):
    data: AdminRefundOut


class BillDetailResponse(CamelModel):
    data: BillOut


# --- Menu -----------------------------------------------------------------

class CategoryCreateRequest(CamelModel):
    model_config = CamelModel.model_config | {"extra": "forbid"}
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=1000)
    sort_order: int = Field(default=0, ge=0)


class CategoryUpdateRequest(CamelModel):
    model_config = CamelModel.model_config | {"extra": "forbid"}
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    sort_order: int | None = Field(default=None, ge=0)
    is_active: bool | None = None


class CategoryReorderRequest(CamelModel):
    model_config = CamelModel.model_config | {"extra": "forbid"}
    category_ids: list[UUID] = Field(min_length=1)


class CategoryOut(CamelModel):
    id: UUID
    name: str
    description: str | None = None
    sort_order: int
    is_active: bool
    item_count: int = 0


class CategoryListResponse(CamelModel):
    data: list[CategoryOut]


class CategoryDataResponse(CamelModel):
    data: CategoryOut


class VariantCreateRequest(CamelModel):
    model_config = CamelModel.model_config | {"extra": "forbid"}
    name: str = Field(min_length=1, max_length=200)
    price_delta_paise: int = 0
    sort_order: int = Field(default=0, ge=0)
    is_available: bool = True


class VariantUpdateRequest(CamelModel):
    model_config = CamelModel.model_config | {"extra": "forbid"}
    name: str | None = Field(default=None, min_length=1, max_length=200)
    price_delta_paise: int | None = None
    sort_order: int | None = Field(default=None, ge=0)
    is_available: bool | None = None


class AddOnCreateRequest(CamelModel):
    model_config = CamelModel.model_config | {"extra": "forbid"}
    name: str = Field(min_length=1, max_length=200)
    price_paise: int = Field(ge=0)
    is_available: bool = True


class AddOnUpdateRequest(CamelModel):
    model_config = CamelModel.model_config | {"extra": "forbid"}
    name: str | None = Field(default=None, min_length=1, max_length=200)
    price_paise: int | None = Field(default=None, ge=0)
    is_available: bool | None = None


class VariantOut(CamelModel):
    id: UUID
    name: str
    price_delta_paise: int
    sort_order: int
    is_available: bool


class AddOnOut(CamelModel):
    id: UUID
    name: str
    price_paise: int
    is_available: bool


class MenuItemCreateRequest(CamelModel):
    model_config = CamelModel.model_config | {"extra": "forbid"}
    category_id: UUID
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=1000)
    price_paise: int = Field(ge=0)
    is_veg: bool = True
    is_available: bool = True
    is_popular: bool = False
    image_url: str | None = None
    station: str = Field(default="Main Kitchen", max_length=100)
    sort_order: int = Field(default=0, ge=0)


class MenuItemUpdateRequest(CamelModel):
    model_config = CamelModel.model_config | {"extra": "forbid"}
    category_id: UUID | None = None
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    price_paise: int | None = Field(default=None, ge=0)
    is_veg: bool | None = None
    is_available: bool | None = None
    is_popular: bool | None = None
    image_url: str | None = None
    station: str | None = Field(default=None, max_length=100)
    sort_order: int | None = Field(default=None, ge=0)


class MenuItemOut(CamelModel):
    id: UUID
    category_id: UUID
    category_name: str | None = None
    name: str
    description: str | None = None
    price_paise: int
    is_veg: bool
    is_available: bool
    is_popular: bool
    image_url: str | None = None
    station: str
    sort_order: int
    variants: list[VariantOut] = []
    add_ons: list[AddOnOut] = []


class MenuItemListResponse(CamelModel):
    data: list[MenuItemOut]
    pagination: Pagination


class MenuItemDataResponse(CamelModel):
    data: MenuItemOut


class VariantDataResponse(CamelModel):
    data: VariantOut


class AddOnDataResponse(CamelModel):
    data: AddOnOut


# --- Settings / tax --------------------------------------------------------

class RestaurantUpdateRequest(CamelModel):
    model_config = CamelModel.model_config | {"extra": "forbid"}
    name: str | None = Field(default=None, min_length=1, max_length=200)
    tagline: str | None = Field(default=None, max_length=500)
    cuisine: str | None = Field(default=None, max_length=200)
    address: str | None = Field(default=None, max_length=500)
    phone: str | None = Field(default=None, max_length=50)
    contact_name: str | None = Field(default=None, max_length=200)
    contact_email: str | None = Field(default=None, max_length=200)
    opening_hours: str | None = Field(default=None, max_length=500)
    currency: str | None = Field(default=None, min_length=3, max_length=3)

    @field_validator("currency")
    @classmethod
    def _check_currency(cls, value: str | None) -> str | None:
        if value is not None and not re.fullmatch(r"[A-Z]{3}", value):
            raise ValueError("currency must be an ISO-4217 code (e.g. INR)")
        return value


class RestaurantOut(CamelModel):
    id: UUID
    name: str
    slug: str
    tagline: str | None = None
    cuisine: str | None = None
    address: str | None = None
    phone: str | None = None
    contact_name: str | None = None
    contact_email: str | None = None
    opening_hours: str | None = None
    currency: str


class RestaurantDataResponse(CamelModel):
    data: RestaurantOut


class BranchUpdateRequest(CamelModel):
    model_config = CamelModel.model_config | {"extra": "forbid"}
    name: str | None = Field(default=None, min_length=1, max_length=200)
    address: str | None = Field(default=None, max_length=500)
    timezone: str | None = None
    contact_name: str | None = Field(default=None, max_length=200)
    contact_phone: str | None = Field(default=None, max_length=50)
    contact_email: str | None = Field(default=None, max_length=200)

    @field_validator("timezone")
    @classmethod
    def _check_timezone(cls, value: str | None) -> str | None:
        if value is not None:
            from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

            try:
                ZoneInfo(value)
            except ZoneInfoNotFoundError:
                raise ValueError(f"unknown timezone: {value}")
        return value


class BranchOut(CamelModel):
    id: UUID
    name: str
    slug: str
    address: str | None = None
    timezone: str
    contact_name: str | None = None
    contact_phone: str | None = None
    contact_email: str | None = None
    is_active: bool


class BranchDataResponse(CamelModel):
    data: BranchOut


class BranchListResponse(CamelModel):
    data: list[BranchOut]


class TableCreateRequest(CamelModel):
    model_config = CamelModel.model_config | {"extra": "forbid"}
    number: int = Field(ge=1, le=999)
    branch_id: UUID | None = None


class TableOut(CamelModel):
    id: UUID
    number: int
    status: str
    branch_id: UUID
    is_active: bool


class TableListResponse(CamelModel):
    data: list[TableOut]


class TableDataResponse(CamelModel):
    data: TableOut


class TaxConfigCreateRequest(CamelModel):
    model_config = CamelModel.model_config | {"extra": "forbid"}
    enabled: bool
    rate_bps: int | None = Field(default=None, ge=0, le=10000)
    mode: str = "exclusive"
    label: str | None = Field(default=None, max_length=100)
    effective_from: datetime

    @field_validator("mode")
    @classmethod
    def _check_mode(cls, value: str) -> str:
        if value not in ("inclusive", "exclusive"):
            raise ValueError("mode must be 'inclusive' or 'exclusive'")
        return value


class TaxConfigOut(CamelModel):
    id: UUID
    enabled: bool
    rate_bps: int | None = None
    mode: str
    label: str | None = None
    effective_from: datetime
    created_at: datetime


class TaxConfigResponse(CamelModel):
    current: TaxConfigOut | None
    history: list[TaxConfigOut] = []


# --- Audit ----------------------------------------------------------------

class AuditLogOut(CamelModel):
    id: UUID
    action: str
    entity_type: str | None = None
    entity_id: UUID | None = None
    details: dict | None = None
    created_at: datetime


class AuditLogListResponse(CamelModel):
    data: list[AuditLogOut]
    pagination: Pagination
