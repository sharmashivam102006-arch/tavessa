"""Waiter / Staff dashboard schemas — api-plan.md §§ 4.2, 5 as built
in Stage 5.

The staff surface is READ + serve + help-call ack/resolve only. No
billing, no session close, no table-status transitions, no staff
assignments (those are deferred — see the roadmap Stage 5 report).

Shapes reuse the existing wire types instead of duplicating them:
staff orders ARE ``KitchenOrderOut`` (Order + tableNumber +
elapsedSeconds), and the restaurant picker reuses
``KitchenRestaurantOut``. Help calls extend the guest
``HelpCallOut`` with the table number and the ack/resolve
timestamps the staff queue needs.
"""
from datetime import datetime
from uuid import UUID

from app.schemas.common import CamelModel, Pagination
from app.schemas.guest import HelpCallOut
from app.schemas.kitchen import KitchenOrderOut, KitchenRestaurantOut


class StaffRestaurantOut(KitchenRestaurantOut):
    """Dev-only restaurant picker for the staff dashboard (id + name)."""


class StaffRestaurantsResponse(CamelModel):
    data: list[StaffRestaurantOut]


class StaffTableOut(CamelModel):
    """One table on the floor grid. ``status`` is the stored table
    status (available / occupied / cleaning / out_of_service); the
    operational flags are derived from live DB state, never invented:
    an active session, its active-order / ready-order counts, the
    latest order status, and whether a help call is open."""

    id: UUID
    number: int
    status: str
    session_id: UUID | None = None
    session_opened_at: datetime | None = None
    active_order_count: int
    ready_order_count: int
    latest_order_status: str | None = None
    has_open_help_call: bool


class StaffTableListResponse(CamelModel):
    data: list[StaffTableOut]


class StaffTableDetailOut(CamelModel):
    """A selected table: its floor-grid row, its active session's
    orders (full snapshots, most recent first), and its
    open/acknowledged help calls."""

    table: StaffTableOut
    orders: list[KitchenOrderOut]
    help_calls: list["StaffHelpCallOut"]


class StaffTableDetailResponse(CamelModel):
    data: StaffTableDetailOut


class StaffOrderListResponse(CamelModel):
    data: list[KitchenOrderOut]
    pagination: Pagination


class StaffOrderDataResponse(CamelModel):
    data: KitchenOrderOut


class StaffHelpCallOut(HelpCallOut):
    """A help call as the staff queue sees it: the guest shape plus
    the table number (for the queue) and the ack/resolve timestamps."""

    table_number: int
    acknowledged_at: datetime | None = None
    resolved_at: datetime | None = None


class StaffHelpCallDataResponse(CamelModel):
    data: StaffHelpCallOut


class StaffHelpCallListResponse(CamelModel):
    data: list[StaffHelpCallOut]
    pagination: Pagination
