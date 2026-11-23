"""Dining tables, QR tokens, table sessions (database-design.md §§ 3.4–3.6).

QR tokens: only the SHA-256 hex of the raw token is stored (never the raw
token). A token is expired when its status is ``expired`` or ``expires_at``
has passed. At most one *active* token per table and at most one *active*
session per table — enforced by partial unique indexes (PostgreSQL) plus a
service-layer check (which is what the SQLite test suite exercises).
"""
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.common import IdMixin, TimestampMixin

TABLE_STATUSES = ("available", "occupied", "cleaning", "out_of_service")
TOKEN_STATUSES = ("active", "revoked", "expired")
SESSION_STATUSES = ("active", "billing_requested", "closed", "expired")


class DiningTable(IdMixin, TimestampMixin, Base):
    __tablename__ = "dining_tables"

    restaurant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("restaurants.id"), nullable=False, index=True
    )
    branch_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("branches.id"), nullable=False, index=True
    )
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="available")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    restaurant: Mapped["Restaurant"] = relationship("Restaurant", back_populates="tables")
    branch: Mapped["Branch"] = relationship("Branch")
    tokens: Mapped[list["TableToken"]] = relationship(
        "TableToken", back_populates="table", cascade="all, delete-orphan"
    )
    sessions: Mapped[list["TableSession"]] = relationship(
        "TableSession", back_populates="table"
    )

    __table_args__ = (
        UniqueConstraint(
            "restaurant_id", "branch_id", "number",
            name="uq_dining_tables_restaurant_branch_number",
        ),
        CheckConstraint(
            "status IN ('available', 'occupied', 'cleaning', 'out_of_service')",
            name="table_status_valid",
        ),
        Index("ix_dining_tables_restaurant_status", "restaurant_id", "status"),
    )


class TableToken(IdMixin, TimestampMixin, Base):
    __tablename__ = "table_tokens"

    restaurant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("restaurants.id"), nullable=False, index=True
    )
    table_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("dining_tables.id"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="active")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    table: Mapped["DiningTable"] = relationship("DiningTable", back_populates="tokens")

    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'revoked', 'expired')", name="token_status_valid"
        ),
        # At most one active token per table (PostgreSQL partial index).
        Index(
            "uq_table_tokens_one_active_per_table",
            "table_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
            sqlite_where=text("status = 'active'"),
        ),
    )

    @property
    def is_expired(self) -> bool:
        if self.status == "expired":
            return True
        if self.expires_at is not None:
            from datetime import timezone

            now = datetime.now(timezone.utc)
            expires = self.expires_at
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
            return expires <= now
        return False


class TableSession(IdMixin, TimestampMixin, Base):
    __tablename__ = "table_sessions"

    restaurant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("restaurants.id"), nullable=False, index=True
    )
    branch_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("branches.id"), nullable=False, index=True
    )
    table_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("dining_tables.id"), nullable=False, index=True
    )
    token_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("table_tokens.id")
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, default="active")
    opened_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    table: Mapped["DiningTable"] = relationship("DiningTable", back_populates="sessions")
    token: Mapped["TableToken | None"] = relationship("TableToken")
    orders: Mapped[list["Order"]] = relationship("Order", back_populates="table_session")

    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'billing_requested', 'closed', 'expired')",
            name="session_status_valid",
        ),
        # At most one active session per table (PostgreSQL partial index).
        Index(
            "uq_table_sessions_one_active_per_table",
            "table_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
            sqlite_where=text("status = 'active'"),
        ),
        Index("ix_table_sessions_table_status", "table_id", "status"),
    )
