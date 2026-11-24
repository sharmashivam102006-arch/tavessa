"""SQLAlchemy models. Business models land in Stage 3.

Importing a model module here registers it on ``Base.metadata`` — that
is what Alembic autogenerate reads. Keep this list in sync with the
models that exist; do not import modules that do not exist yet.
"""
from app.models.restaurant import Branch, Restaurant  # noqa: F401
from app.models.table import DiningTable, TableSession, TableToken  # noqa: F401
from app.models.menu import ItemAddon, ItemVariant, MenuCategory, MenuItem  # noqa: F401
from app.models.order import Order, OrderItem, OrderItemAddon  # noqa: F401
from app.models.support import HelpCall  # noqa: F401
from app.models.idempotency import IdempotencyKey  # noqa: F401
from app.models.billing import Bill, BillItem, BillSplit, Payment  # noqa: F401
from app.auth.models import (  # noqa: F401
    AuditLog,
    RefreshSession,
    Refund,
    TaxConfiguration,
    User,
    WsTicket,
)

__all__ = [
    "Branch",
    "Restaurant",
    "DiningTable",
    "TableSession",
    "TableToken",
    "MenuCategory",
    "MenuItem",
    "ItemVariant",
    "ItemAddon",
    "Order",
    "OrderItem",
    "OrderItemAddon",
    "HelpCall",
    "IdempotencyKey",
    "Bill",
    "BillItem",
    "BillSplit",
    "Payment",
    "User",
    "RefreshSession",
    "WsTicket",
    "TaxConfiguration",
    "Refund",
    "AuditLog",
]
