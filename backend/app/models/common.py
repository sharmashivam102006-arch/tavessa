"""Shared model mixins: UUID primary key + timestamps.

Re-exported from ``app.db.base`` (the canonical home — see the
note there — so ``app.auth.models`` can use them without a
circular import through this package)."""
import uuid
from datetime import datetime

from app.db.base import IdMixin, TimestampMixin

__all__ = ["IdMixin", "TimestampMixin"]
