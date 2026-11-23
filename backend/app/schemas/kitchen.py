"""Kitchen (KDS) schemas — api-plan.md § 6 as built in Stage 4.

Stage 4 deviations from the blueprint (recorded in api-plan.md § 6):
- Workflow is ORDER-LEVEL, not per-item: no item-status endpoint.
- No ``kitchen_tickets`` table and no stations: a "ticket" IS an
  order + its item snapshots.
- The wire shape reuses ``OrderOut`` / ``OrderItemSnapshotOut`` from
  ``schemas/guest.py`` and adds only the two fields the KDS needs:
  ``tableNumber`` and ``elapsedSeconds`` (server-computed).
"""
from datetime import datetime
from uuid import UUID

from pydantic import Field

from app.schemas.common import CamelModel, Pagination
from app.schemas.guest import OrderOut


class KitchenOrderOut(OrderOut):
    """An order as the KDS sees it: the guest ``OrderOut`` plus the
    table number (for the ticket header) and the elapsed seconds
    since placement (server-computed from ``placed_at``)."""

    table_number: int
    elapsed_seconds: int


class KitchenOrderDataResponse(CamelModel):
    data: KitchenOrderOut


class KitchenOrderListResponse(CamelModel):
    data: list[KitchenOrderOut]
    pagination: Pagination


class KitchenStatusUpdateRequest(CamelModel):
    """Only the two kitchen transitions exist: placed/accepted ->
    preparing, preparing -> ready. Anything else is a 422."""

    model_config = CamelModel.model_config | {"extra": "forbid"}

    status: str = Field(pattern="^(preparing|ready)$")


class KitchenRestaurantOut(CamelModel):
    id: UUID
    name: str


class KitchenRestaurantsResponse(CamelModel):
    data: list[KitchenRestaurantOut]
