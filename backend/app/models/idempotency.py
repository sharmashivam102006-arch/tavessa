"""Idempotency keys (database-design.md § 3.26, api-plan.md § 10).

Only POST /orders uses this table in v1. Scope is
(restaurant_id, table_session_id, endpoint, key): a key is
per-session, so one session can never replay or reuse another
session's key, and the same key string in a different session is an
independent claim. The stored response is returned verbatim on a
replay of the same key + same canonical body. Keys expire after 24h.

Claim lifecycle (Stage 3.1): a row is inserted FIRST with
response_status/response_body/order_id NULL (the atomic claim —
the DB unique constraint serialises concurrent claimants); the
order-creation transaction then sets order_id, and the stored
response is filled in afterwards. A claim whose order creation
fails is deleted, so a corrected retry can proceed.
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
