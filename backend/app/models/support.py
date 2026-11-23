"""Help calls (database-design.md § 3.21).

One open/acknowledged call per table session — partial unique index
(PostgreSQL) plus a service-layer check. Wire type values are lowercase:
waiter | water | plates | order_help. ``plates_cutlery`` is accepted as
an alias for ``plates`` on input and is always returned as ``plates``.
"""
import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.common import IdMixin, TimestampMixin

HELP_CALL_TYPES = ("waiter", "water", "plates", "order_help")
HELP_CALL_STATUSES = ("open", "acknowledged", "resolved", "cancelled")


class HelpCall(IdMixin, TimestampMixin, Base):
    __tablename__ = "help_calls"

    restaurant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("restaurants.id"), nullable=False, index=True
    )
    table_session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("table_sessions.id"), nullable=False, index=True
    )
    table_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("dining_tables.id"), nullable=False, index=True
    )
    type: Mapped[str] = mapped_column("type", Text, nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="open")
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "type IN ('waiter', 'water', 'plates', 'order_help')",
            name="help_call_type_valid",
        ),
        CheckConstraint(
            "status IN ('open', 'acknowledged', 'resolved', 'cancelled')",
            name="help_call_status_valid",
        ),
        Index(
            "uq_help_calls_one_open_per_session",
            "table_session_id",
            unique=True,
            postgresql_where=text("status IN ('open', 'acknowledged')"),
            sqlite_where=text("status IN ('open', 'acknowledged')"),
        ),
        Index("ix_help_calls_restaurant_status", "restaurant_id", "status"),
    )
