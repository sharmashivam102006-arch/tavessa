"""Idempotency keys (database-design.md § 3.26, api-plan.md § 10).

Only POST /orders uses this table in v1. Scope is
(restaurant_id, table_session_id, endpoint, key): a key is
per-session, so one session can never replay or reuse another
session's key, and the same key string in a different session is an
independent claim. The stored response is returned verbatim on a
replay of the same key + same canonical body. Keys expire after 24h.

Claim lifecycle (Stage 3.2): claim + order + stored response are
created in ONE transaction — the claim row is inserted (flush, no
commit), the order is created with order_id set on the claim, the
stored response is filled in, and a single commit lands all three.
Any failure rolls the whole transaction back, so in steady state a
row is NEVER visible with response NULL: a committed row is always
a completed claim. Rows with response NULL can only be leftovers
from Stage 3.1's three-transaction design (claim committed
separately); the conflict path in order_service.py reclaims them
(rebuild from order_id, or delete a stale row older than 60 s).
A claim whose placement fails leaves no row at all, so a corrected
retry with the same key proceeds.

Stage 6 extends this table to PAYMENT claims (billing_service):
a payment claim has ``table_session_id`` NULL and ``bill_id`` set,
scoped by (restaurant_id, bill_id, endpoint, key) via a partial
unique index. The same single-transaction pattern applies — claim
+ payment + stored response in ONE transaction — and the order
scope (table_session_id NOT NULL in practice, same unique
constraint) is unchanged, so order behaviour is untouched.
"""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, JSON, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.common import IdMixin, TimestampMixin


class IdempotencyKey(IdMixin, TimestampMixin, Base):
    __tablename__ = "idempotency_keys"

    restaurant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("restaurants.id"), nullable=False, index=True
    )
    # Nullable since Stage 6: order claims are per-session (always
    # set), but PAYMENT claims are scoped by bill instead — a payment
    # has no table session of its own in the claim scope, so its
    # table_session_id is NULL and its bill_id is set. The order
    # unique constraint is unchanged and order behaviour is
    # untouched; payment claims are covered by the partial unique
    # index on (restaurant_id, bill_id, endpoint, key) WHERE
    # bill_id IS NOT NULL (see the Stage 6 migration).
    table_session_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("table_sessions.id"), index=True
    )
    endpoint: Mapped[str] = mapped_column(Text, nullable=False)
    key: Mapped[str] = mapped_column(Text, nullable=False)
    request_hash: Mapped[str] = mapped_column(Text, nullable=False)
    response_status: Mapped[int | None] = mapped_column(Integer)
    response_body: Mapped[dict | None] = mapped_column(JSON)
    order_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("orders.id"))
    # Stage 6: payment-claim scope. Set only for payment claims
    # (endpoint POST /api/v1/staff/bills/payments); NULL for orders.
    bill_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("bills.id"))
    payment_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("payments.id")
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "restaurant_id", "table_session_id", "endpoint", "key",
            name="uq_idempotency_keys_restaurant_session_endpoint_key",
        ),
        # Payment-claim scope (Stage 6): concurrent payment claims
        # for the same bill + key serialise on this index. The
        # partial index is what makes the claim INSERT block/raise
        # for a duplicate — without it a replay would sail through
        # to the status guard and 409 instead of replaying.
        Index(
            "uq_idempotency_keys_payment_scope",
            "restaurant_id", "bill_id", "endpoint", "key",
            unique=True,
            postgresql_where=text("bill_id IS NOT NULL"),
            sqlite_where=text("bill_id IS NOT NULL"),
        ),
    )
