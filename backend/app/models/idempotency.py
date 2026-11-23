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
"""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.common import IdMixin, TimestampMixin


class IdempotencyKey(IdMixin, TimestampMixin, Base):
    __tablename__ = "idempotency_keys"

    restaurant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("restaurants.id"), nullable=False, index=True
    )
    table_session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("table_sessions.id"), nullable=False, index=True
    )
    endpoint: Mapped[str] = mapped_column(Text, nullable=False)
    key: Mapped[str] = mapped_column(Text, nullable=False)
    request_hash: Mapped[str] = mapped_column(Text, nullable=False)
    response_status: Mapped[int | None] = mapped_column(Integer)
    response_body: Mapped[dict | None] = mapped_column(JSON)
    order_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("orders.id"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "restaurant_id", "table_session_id", "endpoint", "key",
            name="uq_idempotency_keys_restaurant_session_endpoint_key",
        ),
    )
