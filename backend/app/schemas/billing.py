"""Billing schemas — api-plan.md § 7 as built in Stage 6.

Wire format is camelCase (CamelModel), money is integer paise.
Bill statuses: issued | payment_requested | paid (no voided /
refunded states in this stage). Split statuses: unpaid |
payment_requested | paid. Payment method is cash only — the
request schema accepts an optional ``method`` field but rejects
anything other than 'cash' with a 422, and rejects every other
extra field (so a client-supplied price/total is a 422, not
ignored data).
"""
from datetime import datetime
from uuid import UUID

from pydantic import Field, field_validator

from app.schemas.common import CamelModel, Pagination


class BillItemOut(CamelModel):
    id: UUID
    order_id: UUID | None = None
    order_item_id: UUID | None = None
    name: str
    variant_name: str | None = None
    add_on_names: list[str] = []
    quantity: int
    unit_price_paise: int
    total_paise: int


class BillSplitOut(CamelModel):
    id: UUID
    label: str
    amount_paise: int
    status: str
    paid_at: datetime | None = None
    # 'equal' | 'custom' — how the split set was created (Stage 8);
    # None for splits created before Stage 8 (treated as 'custom').
    mode: str | None = None


class PaymentOut(CamelModel):
    id: UUID
    bill_id: UUID
    bill_split_id: UUID | None = None
    method: str
    amount_paise: int
    change_due_paise: int
    collected_by: UUID | None = None
    collected_at: datetime
    receipt_number: str
    status: str
    note: str | None = None


class BillOut(CamelModel):
    id: UUID
    bill_number: str
    table_session_id: UUID
    table_id: UUID
    table_number: int
    status: str
    items: list[BillItemOut] = []
    splits: list[BillSplitOut] = []
    payments: list[PaymentOut] = []
    order_numbers: list[str] = []
    subtotal_paise: int
    tax_paise: int
    service_charge_paise: int
    discount_paise: int
    total_paise: int
    amount_paid_paise: int
    outstanding_paise: int
    issued_at: datetime
    payment_requested_at: datetime | None = None
    paid_at: datetime | None = None
    version: int
    voided_at: datetime | None = None
    void_reason: str | None = None
    tax_rate_bps_snapshot: int | None = None
    tax_mode_snapshot: str | None = None
    tax_label_snapshot: str | None = None


class BillDataResponse(CamelModel):
    data: BillOut


class StaffBillListOut(CamelModel):
    """One row of GET /staff/bills: the queue needs the table, the
    bill number, money, request time and order count — not the full
    item list (that comes from GET /staff/bills/:id)."""

    id: UUID
    bill_number: str
    table_id: UUID
    table_number: int
    table_session_id: UUID
    status: str
    total_paise: int
    amount_paid_paise: int
    outstanding_paise: int
    order_count: int
    issued_at: datetime
    payment_requested_at: datetime | None = None


class StaffBillListResponse(CamelModel):
    data: list[StaffBillListOut]
    pagination: Pagination


class StaffBillSummaryOut(CamelModel):
    """The open-bill summary embedded in GET /staff/tables/:id."""

    id: UUID
    bill_number: str
    status: str
    total_paise: int
    amount_paid_paise: int
    outstanding_paise: int
    issued_at: datetime
    payment_requested_at: datetime | None = None


class SplitCreateRequest(CamelModel):
    """Equal: parts >= 2. Custom: amounts (paise) must sum exactly
    to the bill total, each > 0. No other fields are accepted."""

    model_config = CamelModel.model_config | {"extra": "forbid"}

    mode: str
    parts: int | None = Field(default=None, ge=2)
    amounts_paise: list[int] | None = Field(default=None)

    @field_validator("mode")
    @classmethod
    def _check_mode(cls, value: str) -> str:
        if value not in ("equal", "custom"):
            raise ValueError("mode must be 'equal' or 'custom'")
        return value


class PaymentCreateRequest(CamelModel):
    """Cash confirmation. ``method`` is NOT a free choice: omitted
    means cash; anything other than 'cash' is a 422. No price/total
    fields are accepted (extra='forbid')."""

    model_config = CamelModel.model_config | {"extra": "forbid"}

    amount_paise: int = Field(ge=1)
    bill_split_id: UUID | None = None
    method: str | None = None
    note: str | None = Field(default=None, max_length=500)

    @field_validator("method")
    @classmethod
    def _check_method(cls, value: str | None) -> str | None:
        if value is not None and value != "cash":
            raise ValueError("method must be 'cash' — Tavessa is cash-only in v1")
        return value


class ReceiptOut(CamelModel):
    """Receipt data (JSON only — no PDF in this stage). A receipt is
    NEVER issued before the backend confirms a cash payment: both
    receipt endpoints 409 until the bill is paid."""

    restaurant_name: str
    branch_name: str | None = None
    bill_number: str
    receipt_number: str
    receipt_numbers: list[str] = []
    table_number: int
    order_numbers: list[str] = []
    items: list[BillItemOut] = []
    subtotal_paise: int
    tax_paise: int
    tax_status: str = "Tax: not configured — no tax rate has been set for this restaurant"
    tax_rate_bps_snapshot: int | None = None
    tax_mode_snapshot: str | None = None
    tax_label_snapshot: str | None = None
    total_paise: int
    amount_received_paise: int
    outstanding_paise: int
    payment_method: str = "Cash"
    payment_timestamps: list[datetime] = []
    receipt_timestamp: datetime | None = None
    bill_id: UUID


class ReceiptDataResponse(CamelModel):
    data: ReceiptOut


class PaymentResultOut(CamelModel):
    payment: PaymentOut
    bill: BillOut
    receipt: ReceiptOut | None = None


class PaymentDataResponse(CamelModel):
    """POST /staff/bills/:id/payments response: the payment, the
    updated bill and the receipt data (once the bill is paid the
    receipt is complete; for a partial payment the receipt fields
    reflect the amounts so far)."""

    data: PaymentResultOut


class StaffPaymentListResponse(CamelModel):
    data: list[PaymentOut]
    pagination: Pagination
