"""Billing models — bills, bill items, splits, cash payments
(database-design.md §§ 3.16–3.19, as built in Stage 6).

Stage 6 recorded decisions (see development-roadmap.md Stage 6):
- NO refunds table, NO audit_logs table, NO bill void in this stage
  (owner auth does not exist yet — deferred to Stage 7).
- NO voided/refunded statuses: bill status is issued |
  payment_requested | paid; payment status is confirmed only.
- ``collected_by`` is NULLABLE and always NULL in this stage: there
  is no users system yet and the brief forbids inventing a staff
  identity. It becomes a real FK when staff auth lands.
- Tax / service charge / discount are 0 (decision D1 undecided):
  the bill shows tax as "not configured", never "0% GST".
- ``change_due_paise`` is stored as 0 always (decision D9: no
  change handling in v1 — a payment must be <= the amount due).
- ``payment_requested_at`` is an explicit column (not derived from
  updated_at) so the staff queue can sort/show request time.
- Receipt numbers are unique PER RESTAURANT
  (``uq_payments_restaurant_receipt``), not globally: the format
  ``R-YYYY-MM-DD-NNN`` restarts per restaurant per day, so two
  restaurants legitimately issue the same receipt number. Same
  for bill numbers (``uq_bills_restaurant_number``).
- One open bill per session: partial unique index on
  ``table_session_id`` WHERE status IN ('issued',
  'payment_requested') — a paid bill never blocks the next bill
  for later orders (see billing_service for the regeneration /
  new-bill rules).
- Bill items snapshot the order-item snapshot again at bill time
  (name / variant / add-on names / unit price), so a bill is
  self-contained even if the order is later amended. Add-on names
  are a JSON list (the order_items pattern stores add-ons as rows;
  a bill line only needs the names for display).
"""
import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.common import IdMixin, TimestampMixin

BILL_STATUSES = ("issued", "payment_requested", "paid", "voided", "refunded", "partially_refunded")
BILL_SPLIT_STATUSES = ("unpaid", "payment_requested", "paid")
PAYMENT_STATUSES = ("confirmed", "refunded", "partially_refunded")


class Bill(IdMixin, TimestampMixin, Base):
    __tablename__ = "bills"

    restaurant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("restaurants.id"), nullable=False, index=True
    )
    branch_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("branches.id"))
    table_session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("table_sessions.id"), nullable=False, index=True
    )
    # Denormalised for staff lists, like orders/help_calls (the
    # session is the source of truth; a mismatch is a bug).
    table_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("dining_tables.id"), nullable=False, index=True
    )
    bill_number: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="issued")
    subtotal_paise: Mapped[int] = mapped_column(BigInteger, nullable=False)
    tax_paise: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    service_charge_paise: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0
    )
    discount_paise: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    total_paise: Mapped[int] = mapped_column(BigInteger, nullable=False)
    amount_paid_paise: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0
    )
    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    payment_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # Stage 7 — void + tax freeze point. A voided bill keeps its
    # rows (it is never deleted) and its items become billable
    # again (see billing_service._billable_order_items).
    voided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    void_reason: Mapped[str | None] = mapped_column(Text)
    voided_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    # Tax snapshot, taken at the freeze point (issued ->
    # payment_requested, or at payment when a bill is paid without
    # a request). NULL = tax was unconfigured at freeze. Frozen /
    # paid bills are preserved exactly — later tax config changes
    # never alter them.
    tax_rate_bps_snapshot: Mapped[int | None] = mapped_column(Integer)
    tax_mode_snapshot: Mapped[str | None] = mapped_column(Text)
    tax_label_snapshot: Mapped[str | None] = mapped_column(Text)

    items: Mapped[list["BillItem"]] = relationship(
        "BillItem", back_populates="bill", cascade="all, delete-orphan"
    )
    splits: Mapped[list["BillSplit"]] = relationship(
        "BillSplit", back_populates="bill", cascade="all, delete-orphan"
    )
    payments: Mapped[list["Payment"]] = relationship(
        "Payment", back_populates="bill"
    )

    __table_args__ = (
        UniqueConstraint(
            "restaurant_id", "bill_number", name="uq_bills_restaurant_number"
        ),
        CheckConstraint(
            "status IN ('issued', 'payment_requested', 'paid', 'voided', 'refunded', 'partially_refunded')",
            name="bill_status_valid",
        ),
        CheckConstraint("subtotal_paise >= 0", name="bill_subtotal_non_negative"),
        CheckConstraint("tax_paise >= 0", name="bill_tax_non_negative"),
        CheckConstraint(
            "service_charge_paise >= 0", name="bill_service_charge_non_negative"
        ),
        CheckConstraint("discount_paise >= 0", name="bill_discount_non_negative"),
        CheckConstraint("total_paise >= 0", name="bill_total_non_negative"),
        CheckConstraint(
            "amount_paid_paise >= 0", name="bill_amount_paid_non_negative"
        ),
        # One open bill per session (PostgreSQL + SQLite partial index).
        Index(
            "uq_bills_one_open_per_session",
            "table_session_id",
            unique=True,
            postgresql_where=text("status IN ('issued', 'payment_requested')"),
            sqlite_where=text("status IN ('issued', 'payment_requested')"),
        ),
        Index("ix_bills_restaurant_status", "restaurant_id", "status"),
        Index("ix_bills_restaurant_issued", "restaurant_id", "issued_at"),
    )


class BillItem(IdMixin, TimestampMixin, Base):
    __tablename__ = "bill_items"

    bill_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("bills.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Nullable only for manual adjustments — none exist in this
    # stage, so this is always set. Kept nullable per the blueprint.
    order_item_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("order_items.id")
    )
    order_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("orders.id"))
    item_name_snapshot: Mapped[str] = mapped_column(Text, nullable=False)
    variant_name_snapshot: Mapped[str | None] = mapped_column(Text)
    add_on_names_snapshot: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    unit_price_paise: Mapped[int] = mapped_column(BigInteger, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    line_total_paise: Mapped[int] = mapped_column(BigInteger, nullable=False)

    bill: Mapped["Bill"] = relationship("Bill", back_populates="items")

    __table_args__ = (
        CheckConstraint("quantity >= 1", name="bill_item_quantity_positive"),
        CheckConstraint(
            "unit_price_paise >= 0", name="bill_item_unit_price_non_negative"
        ),
        CheckConstraint(
            "line_total_paise >= 0", name="bill_item_line_total_non_negative"
        ),
        Index("ix_bill_items_bill", "bill_id"),
    )


class BillSplit(IdMixin, TimestampMixin, Base):
    __tablename__ = "bill_splits"

    bill_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("bills.id", ondelete="CASCADE"), nullable=False, index=True
    )
    label: Mapped[str] = mapped_column(Text, nullable=False)
    amount_paise: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="unpaid")
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # 'equal' | 'custom' — how this split set was created (Stage 8).
    # NULL = created before Stage 8 (unknown); treated as 'custom'
    # at freeze time — never silently re-derived. The mode is what
    # makes the freeze reconciliation safe: equal splits are
    # re-derived from the frozen total, custom splits block the
    # freeze when they no longer sum to it (billing_service).
    mode: Mapped[str | None] = mapped_column(Text)

    bill: Mapped["Bill"] = relationship("Bill", back_populates="splits")
    payments: Mapped[list["Payment"]] = relationship(
        "Payment", back_populates="bill_split"
    )

    __table_args__ = (
        CheckConstraint("amount_paise > 0", name="bill_split_amount_positive"),
        CheckConstraint(
            "mode IS NULL OR mode IN ('equal', 'custom')",
            name="bill_split_mode_valid",
        ),
        CheckConstraint(
            "status IN ('unpaid', 'payment_requested', 'paid')",
            name="bill_split_status_valid",
        ),
        Index("ix_bill_splits_bill", "bill_id"),
    )


class Payment(IdMixin, TimestampMixin, Base):
    """The only record that money was collected — append-only.

    A row is created ONLY by staff confirming cash collection.
    There is no update/delete code path for this table anywhere in
    the app (the audit trail in this stage IS this table: created
    timestamps, amounts, receipt numbers, idempotency keys). Status
    is 'confirmed' only — refunded/voided states are deferred with
    refunds (Stage 7, owner auth). ``method`` is CHECK-constrained
    to 'cash' so adding a method is a migration + decision.
    ``collected_by`` is NULLABLE and always NULL until staff auth
    lands (Stage 6 decision 5). ``note`` is an optional waiter note
    recorded at confirmation.
    """

    __tablename__ = "payments"

    restaurant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("restaurants.id"), nullable=False, index=True
    )
    bill_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("bills.id"), nullable=False, index=True
    )
    bill_split_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("bill_splits.id")
    )
    method: Mapped[str] = mapped_column(Text, nullable=False, default="cash")
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    amount_paise: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # Nullable until staff auth exists (Stage 6 decision 5).
    collected_by: Mapped[uuid.UUID | None] = mapped_column()
    collected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    change_due_paise: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, default="confirmed")
    receipt_number: Mapped[str] = mapped_column(Text, nullable=False)
    note: Mapped[str | None] = mapped_column(Text)

    bill: Mapped["Bill"] = relationship("Bill", back_populates="payments")
    bill_split: Mapped["BillSplit | None"] = relationship(
        "BillSplit", back_populates="payments"
    )

    __table_args__ = (
        CheckConstraint("method = 'cash'", name="payment_method_cash_only"),
        CheckConstraint("amount_paise > 0", name="payment_amount_positive"),
        CheckConstraint(
            "change_due_paise >= 0", name="payment_change_due_non_negative"
        ),
        CheckConstraint(
            "status IN ('confirmed', 'refunded', 'partially_refunded')",
            name="payment_status_valid",
        ),
        UniqueConstraint(
            "restaurant_id", "receipt_number",
            name="uq_payments_restaurant_receipt",
        ),
        Index("ix_payments_bill", "bill_id"),
        Index("ix_payments_restaurant_collected", "restaurant_id", "collected_at"),
    )
