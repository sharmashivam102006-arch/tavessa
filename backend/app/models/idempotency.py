"""Idempotency keys (database-design.md § 3.26, api-plan.md § 10).

Only POST /orders uses this table in v1. Keyed by
(restaurant_id, endpoint, key); the stored response is returned verbatim
on a replay of the same key + same canonical body. Keys expire after 24h.
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
    endpoint: Mapped[str] = mapped_column(Text, nullable=False)
    key: Mapped[str] = mapped_column(Text, nullable=False)
    request_hash: Mapped[str] = mapped_column(Text, nullable=False)
    response_status: Mapped[int | None] = mapped_column(Integer)
    response_body: Mapped[dict | None] = mapped_column(JSON)
    order_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("orders.id"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "restaurant_id", "endpoint", "key",
            name="uq_idempotency_keys_restaurant_endpoint_key",
        ),
    )
