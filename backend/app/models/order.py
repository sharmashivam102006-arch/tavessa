"""Orders, order items (+ per-item add-on snapshots).

Order numbers: per-restaurant running numbers, format ``<SLUG>-0001``
(e.g. ``ZAIKA-0001``), allocated under a row lock on the restaurant so
two concurrent placements cannot take the same number. Unique per
restaurant via ``uq_orders_restaurant_number``. The number is a
human display number only; it is not date-scoped in Stage 3 (the
blueprint's daily-reset idea is deferred — the counter never resets,
so the uniqueness constraint is the source of truth).

Status values on the wire are lowercase (api-plan.md § 1, Stage 3
reconciliation): placed / accepted / preparing / ready / served /
completed / cancelled. Item statuses: queued / preparing / ready /
served / cancelled.
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
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.common import IdMixin, TimestampMixin

ORDER_STATUSES = (
    "placed", "accepted", "preparing", "ready", "served", "completed", "cancelled",
)
ORDER_ITEM_STATUSES = ("queued", "preparing", "ready", "served", "cancelled")

# Forward-only transitions (guest cancel is placed -> cancelled only).
ORDER_TRANSITIONS: dict[str, tuple[str, ...]] = {
    "placed": ("accepted", "cancelled"),
    "accepted": ("preparing", "cancelled"),
    "preparing": ("ready", "cancelled"),
    "ready": ("served",),
    "served": ("completed",),
    "completed": (),
    "cancelled": (),
}


class Order(IdMixin, TimestampMixin, Base):
    __tablename__ = "orders"

    restaurant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("restaurants.id"), nullable=False, index=True
    )
    branch_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("branches.id"))
    table_session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("table_sessions.id"), nullable=False, index=True
    )
    table_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("dining_tables.id"), nullable=False, index=True
    )
    order_number: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="placed")
    subtotal_paise: Mapped[int] = mapped_column(BigInteger, nullable=False)
    tax_paise: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    total_paise: Mapped[int] = mapped_column(BigInteger, nullable=False)
    placed_by: Mapped[str] = mapped_column(Text, nullable=False, default="guest")
    placed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    served_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_reason: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    table_session: Mapped["TableSession"] = relationship(
        "TableSession", back_populates="orders"
    )
    items: Mapped[list["OrderItem"]] = relationship(
        "OrderItem", back_populates="order", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("restaurant_id", "order_number", name="uq_orders_restaurant_number"),
        CheckConstraint(
            "status IN ('placed', 'accepted', 'preparing', 'ready', 'served', 'completed', 'cancelled')",
            name="order_status_valid",
        ),
        CheckConstraint("subtotal_paise >= 0", name="order_subtotal_non_negative"),
        CheckConstraint("tax_paise >= 0", name="order_tax_non_negative"),
        CheckConstraint("total_paise >= 0", name="order_total_non_negative"),
        Index("ix_orders_restaurant_status_placed", "restaurant_id", "status", "placed_at"),
        Index("ix_orders_session_placed", "table_session_id", "placed_at"),
    )


class OrderItem(IdMixin, TimestampMixin, Base):
    __tablename__ = "order_items"

    order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    menu_item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("menu_items.id"), nullable=False, index=True
    )
    variant_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("item_variants.id")
    )
    # Snapshots: the billing truth, frozen at order time.
    name_snapshot: Mapped[str] = mapped_column(Text, nullable=False)
    variant_name_snapshot: Mapped[str | None] = mapped_column(Text)
    # Per-unit price INCLUDING variant delta + add-ons (base + delta + add-ons).
    unit_price_paise: Mapped[int] = mapped_column(BigInteger, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    total_paise: Mapped[int] = mapped_column(BigInteger, nullable=False)
    special_instructions: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="queued")

    order: Mapped["Order"] = relationship("Order", back_populates="items")
    add_ons: Mapped[list["OrderItemAddon"]] = relationship(
        "OrderItemAddon", back_populates="order_item", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint("quantity >= 1 AND quantity <= 20", name="order_item_quantity_range"),
        CheckConstraint("unit_price_paise >= 0", name="order_item_unit_price_non_negative"),
        CheckConstraint("total_paise >= 0", name="order_item_total_non_negative"),
        CheckConstraint(
            # 'accepted' is a legal wire value per the Stage 3 mapping
            # (brief ACCEPTED); items in practice start at 'queued' and
            # the kitchen moves them queued -> preparing -> ready.
            "status IN ('queued', 'accepted', 'preparing', 'ready', 'served', 'cancelled')",
            name="order_item_status_valid",
        ),
        CheckConstraint(
            "special_instructions IS NULL OR length(special_instructions) <= 500",
            name="order_item_instructions_max_500",
        ),
    )


class OrderItemAddon(IdMixin, TimestampMixin, Base):
    __tablename__ = "order_item_add_ons"

    order_item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("order_items.id", ondelete="CASCADE"), nullable=False, index=True
    )
    add_on_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("item_add_ons.id"), nullable=False, index=True
    )
    name_snapshot: Mapped[str] = mapped_column(Text, nullable=False)
    price_paise: Mapped[int] = mapped_column(BigInteger, nullable=False)

    order_item: Mapped["OrderItem"] = relationship("OrderItem", back_populates="add_ons")

    __table_args__ = (
        CheckConstraint("price_paise >= 0", name="order_item_add_on_price_non_negative"),
    )
