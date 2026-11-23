"""Guest schemas — the wire contract for /guest/* and /orders.

Field-for-field match with frontend/src/shared/api/types.ts (Stage 2).
All wire names are camelCase via CamelModel; money is integer paise.
"""
from datetime import datetime
from uuid import UUID

from pydantic import Field, field_validator

from app.schemas.common import CamelModel, Pagination


# --- Session ---------------------------------------------------------------

class GuestSessionOut(CamelModel):
    id: UUID
    restaurant_id: UUID
    table_id: UUID
    table_number: int
    status: str
    opened_at: datetime


class RestaurantInfoOut(CamelModel):
    id: UUID
    name: str
    tagline: str | None = None
    cuisine: str | None = None
    address: str | None = None
    opening_hours: str | None = None
    phone: str | None = None
    rating: float | None = None


class TableOut(CamelModel):
    number: int


class GuestSessionResponse(CamelModel):
    session: GuestSessionOut
    restaurant: RestaurantInfoOut
    table: TableOut
    menu_url: str


# --- Menu ------------------------------------------------------------------

class MenuCategoryOut(CamelModel):
    id: UUID
    name: str
    description: str | None = None
    sort_order: int
    is_active: bool


class ItemVariantOut(CamelModel):
    id: UUID
    name: str
    price_delta_paise: int
    is_available: bool


class AddOnOut(CamelModel):
    id: UUID
    name: str
    price_paise: int
    is_available: bool


class MenuItemOut(CamelModel):
    id: UUID
    category_id: UUID
    name: str
    description: str | None = None
    price_paise: int
    is_veg: bool
    is_available: bool
    is_popular: bool
    image_url: str | None = None
    station: str
    variant_group_label: str | None = None
    variants: list[ItemVariantOut] = []
    add_ons: list[AddOnOut] = []


class GuestMenuResponse(CamelModel):
    categories: list[MenuCategoryOut]
    items: list[MenuItemOut]


# --- Orders ----------------------------------------------------------------

class OrderPlacementItemIn(CamelModel):
    """One line of a placement request. No price fields are accepted —
    extra fields are rejected (model_config extra='forbid'), so a
    tampered price/total in the request is a 422, not ignored data."""

    model_config = CamelModel.model_config | {"extra": "forbid"}

    menu_item_id: UUID
    variant_id: UUID | None = None
    add_on_ids: list[UUID] = Field(default_factory=list)
    quantity: int = Field(ge=1, le=20)
    special_instructions: str | None = Field(default=None, max_length=500)


class OrderPlacementRequest(CamelModel):
    model_config = CamelModel.model_config | {"extra": "forbid"}

    items: list[OrderPlacementItemIn] = Field(min_length=1, max_length=30)


class OrderItemSnapshotOut(CamelModel):
    id: UUID
    menu_item_id: UUID
    name: str
    variant_name: str | None = None
    add_on_names: list[str] = []
    quantity: int
    unit_price_paise: int
    total_paise: int
    special_instructions: str | None = None
    status: str


class OrderOut(CamelModel):
    id: UUID
    order_number: str
    table_session_id: UUID
    status: str
    items: list[OrderItemSnapshotOut] = []
    subtotal_paise: int
    tax_paise: int
    total_paise: int
    placed_at: datetime
    accepted_at: datetime | None = None
    ready_at: datetime | None = None
    served_at: datetime | None = None
    cancelled_at: datetime | None = None


class OrderPlacementResponse(CamelModel):
    order: OrderOut


class OrderDataResponse(CamelModel):
    data: OrderOut


class OrderListResponse(CamelModel):
    data: list[OrderOut]
    pagination: Pagination


class CancelOrderRequest(CamelModel):
    model_config = CamelModel.model_config | {"extra": "ignore"}

    reason: str | None = None


# --- Help calls ------------------------------------------------------------

class HelpCallRequest(CamelModel):
    model_config = CamelModel.model_config | {"extra": "forbid"}

    type: str
    note: str | None = Field(default=None, max_length=500)

    @field_validator("type", mode="before")
    @classmethod
    def _normalise_type(cls, value):
        if isinstance(value, str):
            value = value.strip().lower()
            if value == "plates_cutlery":
                return "plates"
        return value

    @field_validator("type")
    @classmethod
    def _check_type(cls, value: str) -> str:
        allowed = {"waiter", "water", "plates", "order_help"}
        if value not in allowed:
            raise ValueError(f"type must be one of {sorted(allowed)}")
        return value


class HelpCallOut(CamelModel):
    id: UUID
    table_session_id: UUID
    type: str | None = None
    note: str | None = None
    status: str
    created_at: datetime


class HelpCallDataResponse(CamelModel):
    data: HelpCallOut
