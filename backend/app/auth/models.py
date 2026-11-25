"""Auth models — Stage 7 (database-design.md § 3.27+).

- ``User``: one account per (restaurant, email); email is stored
  lowercased and unique per restaurant. Passwords are Argon2id
  hashes (``core/security.py``) — never plaintext, never logged,
  never returned by any API (see ``app/schemas/auth.py``).
- ``RefreshSession``: one row per refresh-token family member.
  Only the SHA-256 hash of the opaque token is stored.
- ``WsTicket``: single-use, ~60s WebSocket tickets (NOT signed
  JWTs in the URL — single-use requires server state; see
  architecture.md § 6 / api-plan.md § 9).
- ``TaxConfiguration``: history rows, never updated — the active
  config at an instant is the row with the greatest
  ``effective_from`` <= now.
- ``Refund``: cash refunds against a paid bill/payment.
- ``AuditLog``: append-only. There is NO update/delete code path
  for this table anywhere in the app, and no API route mutates it.
"""
import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, TimestampMixin

USER_ROLES = ("owner", "manager", "waiter", "kitchen")


class User(IdMixin, TimestampMixin, Base):
    __tablename__ = "users"

    restaurant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("restaurants.id"), nullable=False, index=True
    )
    # NULL = all branches of the restaurant.
    branch_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("branches.id")
    )
    email: Mapped[str] = mapped_column(Text, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Manager "Configurable" permissions (brief § 2): explicit flags,
    # default False, toggleable by the owner. Owner always has all
    # permissions regardless of these flags.
    can_manage_staff: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    can_manage_settings: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    can_refund: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    can_void: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    must_change_password: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    deactivated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint(
            "restaurant_id", "email", name="uq_users_restaurant_email"
        ),
        CheckConstraint(
            "role IN ('owner', 'manager', 'waiter', 'kitchen')",
            name="user_role_valid",
        ),
        Index("ix_users_restaurant_role", "restaurant_id", "role"),
    )


class RefreshSession(IdMixin, TimestampMixin, Base):
    """One row per issued refresh token. ``token_hash`` is the
    SHA-256 of the opaque token — the raw token is never stored.
    Rotation replaces the token but keeps ``family_id``; presenting
    an already-revoked token from a family revokes the WHOLE family
    (reuse detection)."""

    __tablename__ = "refresh_sessions"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id"), nullable=False, index=True
    )
    family_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ip_address: Mapped[str | None] = mapped_column(Text)
    user_agent: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        Index("ix_refresh_sessions_family", "family_id"),
        Index("ix_refresh_sessions_token_hash", "token_hash"),
    )


class WsTicket(IdMixin, TimestampMixin, Base):
    """Single-use WebSocket ticket. The ticket handed to the client
    is an opaque random string; only its SHA-256 hash is stored
    (``ticket_hash``, unique). Expires ~60s after issue; ``used_at``
    is set on first (and only) use. Never logged.

    Stage 8: guest tickets bind to a table session instead of a
    user — ``user_id`` is NULL and ``table_session_id`` is set for
    channel 'guest'; staff/kitchen tickets keep ``user_id`` set and
    ``table_session_id`` NULL. A ticket is only ever valid for the
    channel it was issued for (validate_ws_ticket enforces this)."""

    __tablename__ = "ws_tickets"

    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id"), nullable=True, index=True
    )
    # Guest tickets only (Stage 8): the exact active table session
    # this ticket is bound to. NULL for staff/kitchen tickets.
    table_session_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("table_sessions.id"), index=True
    )
    restaurant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("restaurants.id"), nullable=False, index=True
    )
    channel: Mapped[str] = mapped_column(Text, nullable=False)
    ticket_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "channel IN ('kitchen', 'staff', 'guest')",
            name="ws_ticket_channel_valid",
        ),
        Index("ix_ws_tickets_hash", "ticket_hash"),
    )


class TaxConfiguration(IdMixin, TimestampMixin, Base):
    """Tax config history (decision D1, Stage 7). Rows are never
    updated — a new config is a new row. The active config at any
    instant is the row with the greatest ``effective_from`` <= now
    for the restaurant (branch-scoped rows are recorded for a
    future multi-branch stage; v1 reads restaurant-level rows,
    ``branch_id`` NULL). ``enabled=False`` with no rate is the
    "unconfigured" state and is DISTINCT from an explicit 0% rate
    (``enabled=True`` + ``rate_bps=0``)."""

    __tablename__ = "tax_configurations"

    restaurant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("restaurants.id"), nullable=False, index=True
    )
    branch_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("branches.id")
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    rate_bps: Mapped[int | None] = mapped_column(Integer)
    mode: Mapped[str] = mapped_column(Text, nullable=False, default="exclusive")
    label: Mapped[str | None] = mapped_column(Text)
    effective_from: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id")
    )

    __table_args__ = (
        CheckConstraint(
            "rate_bps IS NULL OR (rate_bps >= 0 AND rate_bps <= 10000)",
            name="tax_rate_bps_range",
        ),
        CheckConstraint(
            "mode IN ('inclusive', 'exclusive')", name="tax_mode_valid"
        ),
        CheckConstraint(
            "enabled = false OR rate_bps IS NOT NULL",
            name="tax_enabled_requires_rate",
        ),
        UniqueConstraint(
            "restaurant_id", "branch_id", "effective_from",
            name="uq_tax_config_restaurant_branch_effective",
        ),
        Index("ix_tax_config_restaurant_effective", "restaurant_id", "effective_from"),
    )


class Refund(IdMixin, TimestampMixin, Base):
    """Cash refund against a confirmed payment. The payment row is
    NEVER deleted or overwritten — the refund is a separate row and
    the payment/bill statuses move to refunded /
    partially_refunded. Reference ``RFD-YYYY-MM-DD-NNN`` is unique
    per restaurant."""

    __tablename__ = "refunds"

    restaurant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("restaurants.id"), nullable=False, index=True
    )
    bill_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("bills.id"), nullable=False, index=True
    )
    payment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("payments.id"), nullable=False, index=True
    )
    amount_paise: Mapped[int] = mapped_column(BigInteger, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    refund_reference: Mapped[str] = mapped_column(Text, nullable=False)
    refunded_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id")
    )
    refunded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        CheckConstraint("amount_paise > 0", name="refund_amount_positive"),
        UniqueConstraint(
            "restaurant_id", "refund_reference",
            name="uq_refunds_restaurant_reference",
        ),
        UniqueConstraint(
            "restaurant_id", "bill_id", "idempotency_key",
            name="uq_refunds_restaurant_bill_key",
        ),
        Index("ix_refunds_restaurant_refunded", "restaurant_id", "refunded_at"),
    )


class AuditLog(IdMixin, Base):
    """Append-only audit trail. No update/delete methods exist for
    this table anywhere in the app and no API route mutates it.
    ``details`` must contain only safe fields — never a password,
    token, or dev key (enforced by ``audit_service.write_audit``,
    which drops forbidden keys)."""

    __tablename__ = "audit_logs"

    restaurant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("restaurants.id"), nullable=False, index=True
    )
    branch_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("branches.id")
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id")
    )
    action: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    entity_type: Mapped[str | None] = mapped_column(Text)
    entity_id: Mapped[uuid.UUID | None] = mapped_column()
    details: Mapped[dict | None] = mapped_column(JSON)
    correlation_id: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    __table_args__ = (
        Index("ix_audit_logs_restaurant_created", "restaurant_id", "created_at"),
    )
