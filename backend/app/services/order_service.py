"""Order service: placement (server-priced, idempotent), reads, cancel.

Pricing rule: the backend recomputes every price from the menu.
unit = base + variant delta + add-ons; line total = unit x quantity.
No price/total from the client is ever read — the request schema
rejects price-like fields outright (extra='forbid').
Tax stays 0 (decision D1 undecided).
"""
import hashlib
import json
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import ApiError
from app.models import (
    DiningTable,
    IdempotencyKey,
    ItemAddon,
    ItemVariant,
    MenuItem,
    Order,
    OrderItem,
    OrderItemAddon,
    Restaurant,
)
from app.schemas.common import Pagination
from app.schemas.guest import (
    OrderDataResponse,
    OrderItemSnapshotOut,
    OrderListResponse,
    OrderOut,
    OrderPlacementRequest,
    OrderPlacementResponse,
)
from app.services.table_service import GuestContext, _now

ORDER_ENDPOINT = "POST /api/v1/orders"
IDEMPOTENCY_TTL = timedelta(hours=24)


def _canonical_hash(body: dict) -> str:
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _order_to_out(db: Session, order: Order) -> OrderOut:
    items = (
        db.execute(
            select(OrderItem)
            .where(OrderItem.order_id == order.id)
            .order_by(OrderItem.created_at)
        )
        .scalars()
        .all()
    )
    out_items = []
    for item in items:
        add_ons = (
            db.execute(
                select(OrderItemAddon).where(
                    OrderItemAddon.order_item_id == item.id
                )
            )
            .scalars()
            .all()
        )
        out_items.append(
            OrderItemSnapshotOut(
                id=item.id,
                menu_item_id=item.menu_item_id,
                name=item.name_snapshot,
                variant_name=item.variant_name_snapshot,
                add_on_names=[a.name_snapshot for a in add_ons],
                quantity=item.quantity,
                unit_price_paise=item.unit_price_paise,
                total_paise=item.total_paise,
                special_instructions=item.special_instructions,
                status=item.status,
            )
        )
    return OrderOut(
        id=order.id,
        order_number=order.order_number,
        table_session_id=order.table_session_id,
        status=order.status,
        items=out_items,
        subtotal_paise=order.subtotal_paise,
        tax_paise=order.tax_paise,
        total_paise=order.total_paise,
        placed_at=order.placed_at,
        accepted_at=order.accepted_at,
        ready_at=order.ready_at,
        served_at=order.served_at,
        cancelled_at=order.cancelled_at,
    )


def _next_order_number(db: Session, restaurant: Restaurant) -> str:
    """Per-restaurant running number, allocated under a lock on the
    restaurant row so concurrent placements serialise. Format:
    ``ZAIKA-0001`` (slug upper-cased + zero-padded sequence)."""
    locked = (
        db.execute(
            select(Restaurant)
            .where(Restaurant.id == restaurant.id)
            .with_for_update()
        )
        .scalars()
        .first()
    )
    prefix = (locked.slug if locked else restaurant.slug).upper()
    count = db.execute(
        select(func.count()).select_from(Order).where(
            Order.restaurant_id == restaurant.id
        )
    ).scalar_one()
    return f"{prefix}-{count + 1:04d}"


def _find_idempotency(
    db: Session, restaurant_id: UUID, key: str
) -> IdempotencyKey | None:
    row = (
        db.execute(
            select(IdempotencyKey).where(
                IdempotencyKey.restaurant_id == restaurant_id,
                IdempotencyKey.endpoint == ORDER_ENDPOINT,
                IdempotencyKey.key == key,
            )
        )
        .scalars()
        .first()
    )
    if row is None:
        return None
    if row.expires_at is not None:
        expires = row.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if expires <= _now():
            db.delete(row)
            db.commit()
            return None
    return row


def place_order(
    db: Session,
    ctx: GuestContext,
    request: OrderPlacementRequest,
    idempotency_key: str,
) -> tuple[dict, int, bool]:
    """Place an order. Returns (response_body, status, replayed).

    Idempotency is checked first: same key + same canonical body
    returns the stored response verbatim with replayed=True; same key
    + different body is a 422. The idempotency record is stored AFTER
    the order transaction commits.
    """
    body = request.model_dump(by_alias=True, mode="json")
    request_hash = _canonical_hash(body)

    existing = _find_idempotency(db, ctx.restaurant.id, idempotency_key)
    if existing is not None:
        if existing.request_hash != request_hash:
            raise ApiError(
                422,
                "idempotency_key_reused",
                "This Idempotency-Key was already used with a different request body.",
                {"field": "Idempotency-Key"},
            )
        return existing.response_body, existing.response_status or 201, True

    if ctx.session.status != "active":
        raise ApiError(
            409, "conflict", "This table session is not active.",
            {"session_status": ctx.session.status},
        )

    # --- Validate + price every line (all from the menu) ---------------
    lines: list[dict] = []
    for idx, line in enumerate(request.items):
        item = db.get(MenuItem, line.menu_item_id)
        if item is None or item.restaurant_id != ctx.restaurant.id:
            raise ApiError(422, "validation_failed",
                           f"Menu item not found: {line.menu_item_id}",
                           {"field": f"items[{idx}].menuItemId", "code": "invalid_menu_item"})
        if not item.is_available:
            raise ApiError(422, "validation_failed",
                           f"'{item.name}' is not available right now.",
                           {"field": f"items[{idx}].menuItemId", "code": "item_unavailable"})
        variant = None
        if line.variant_id is not None:
            variant = db.get(ItemVariant, line.variant_id)
            if variant is None or variant.menu_item_id != item.id:
                raise ApiError(422, "validation_failed",
                               "Variant does not belong to this item.",
                               {"field": f"items[{idx}].variantId", "code": "invalid_variant"})
            if not variant.is_available:
                raise ApiError(422, "validation_failed",
                               "Variant is not available.",
                               {"field": f"items[{idx}].variantId", "code": "invalid_variant"})
        add_ons = []
        for add_on_id in line.add_on_ids:
            add_on = db.get(ItemAddon, add_on_id)
            if add_on is None or add_on.menu_item_id != item.id:
                raise ApiError(422, "validation_failed",
                               "Add-on does not belong to this item.",
                               {"field": f"items[{idx}].addOnIds", "code": "invalid_add_on"})
            if not add_on.is_available:
                raise ApiError(422, "validation_failed",
                               "Add-on is not available.",
                               {"field": f"items[{idx}].addOnIds", "code": "invalid_add_on"})
            add_ons.append(add_on)
        unit = item.price_paise
        unit += variant.price_delta_paise if variant else 0
        unit += sum(a.price_paise for a in add_ons)
        if unit < 0:
            raise ApiError(422, "validation_failed",
                           "Computed unit price is negative.",
                           {"field": f"items[{idx}]", "code": "negative_price"})
        lines.append({
            "item": item,
            "variant": variant,
            "add_ons": add_ons,
            "quantity": line.quantity,
            "special_instructions": line.special_instructions,
            "unit": unit,
            "line_total": unit * line.quantity,
        })

    subtotal = sum(line["line_total"] for line in lines)
    tax_paise = 0  # D1 undecided — tax stays 0.
    total = subtotal + tax_paise

    # --- Create order + items + snapshots in ONE transaction -----------
    order_number = _next_order_number(db, ctx.restaurant)
    order = Order(
        restaurant_id=ctx.restaurant.id,
        branch_id=ctx.session.branch_id,
        table_session_id=ctx.session.id,
        table_id=ctx.session.table_id,
        order_number=order_number,
        status="placed",
        subtotal_paise=subtotal,
        tax_paise=tax_paise,
        total_paise=total,
        placed_by="guest",
        placed_at=_now(),
    )
    db.add(order)
    db.flush()
    for line in lines:
        item_row = OrderItem(
            order_id=order.id,
            menu_item_id=line["item"].id,
            variant_id=line["variant"].id if line["variant"] else None,
            name_snapshot=line["item"].name,
            variant_name_snapshot=line["variant"].name if line["variant"] else None,
            unit_price_paise=line["unit"],
            quantity=line["quantity"],
            total_paise=line["line_total"],
            special_instructions=line["special_instructions"],
            status="queued",
        )
        db.add(item_row)
        db.flush()
        for add_on in line["add_ons"]:
            db.add(OrderItemAddon(
                order_item_id=item_row.id,
                add_on_id=add_on.id,
                name_snapshot=add_on.name,
                price_paise=add_on.price_paise,
            ))
    table = db.get(DiningTable, ctx.session.table_id)
    if table is not None and table.status == "available":
        table.status = "occupied"
    db.commit()
    db.refresh(order)

    response = OrderPlacementResponse(order=_order_to_out(db, order))
    response_body = response.model_dump(by_alias=True, mode="json")

    # --- Store the idempotency record AFTER commit ----------------------
    try:
        db.add(IdempotencyKey(
            restaurant_id=ctx.restaurant.id,
            endpoint=ORDER_ENDPOINT,
            key=idempotency_key,
            request_hash=request_hash,
            response_status=201,
            response_body=response_body,
            order_id=order.id,
            expires_at=_now() + IDEMPOTENCY_TTL,
        ))
        db.commit()
    except IntegrityError:
        # A concurrent duplicate won the race; the order still stands
        # and the other record will serve replays.
        db.rollback()
    return response_body, 201, False


def list_orders(
    db: Session, ctx: GuestContext, page: int, per_page: int
) -> OrderListResponse:
    total = db.execute(
        select(func.count()).select_from(Order).where(
            Order.table_session_id == ctx.session.id
        )
    ).scalar_one()
    total_pages = max(1, (total + per_page - 1) // per_page)
    orders = (
        db.execute(
            select(Order)
            .where(Order.table_session_id == ctx.session.id)
            .order_by(Order.placed_at.desc(), Order.created_at.desc())
            .offset((page - 1) * per_page)
            .limit(per_page)
        )
        .scalars()
        .all()
    )
    return OrderListResponse(
        data=[_order_to_out(db, o) for o in orders],
        pagination=Pagination(
            page=page, per_page=per_page, total=total, total_pages=total_pages
        ),
    )


def get_order(db: Session, ctx: GuestContext, order_id: UUID) -> OrderDataResponse:
    """One order of this session — another session's order is a 404,
    not a 403 (api-plan.md § 11: no existence leakage)."""
    order = db.get(Order, order_id)
    if order is None or order.table_session_id != ctx.session.id:
        raise ApiError(404, "not_found", "Order not found.")
    return OrderDataResponse(data=_order_to_out(db, order))


def cancel_order(
    db: Session, ctx: GuestContext, order_id: UUID, reason: str | None
) -> OrderDataResponse:
    """Guest cancel: only while the order is still 'placed' (D8 still
    open — recorded in api-plan.md). Cancels the order and its items
    in the same transaction."""
    order = db.get(Order, order_id)
    if order is None or order.table_session_id != ctx.session.id:
        raise ApiError(404, "not_found", "Order not found.")
    if order.status != "placed":
        raise ApiError(
            409,
            "conflict",
            f"Only a placed order can be cancelled (current status: {order.status}).",
            {"status": order.status},
        )
    order.status = "cancelled"
    order.cancelled_at = _now()
    order.cancel_reason = reason
    order.version += 1
    for item in order.items:
        item.status = "cancelled"
    db.commit()
    db.refresh(order)
    return OrderDataResponse(data=_order_to_out(db, order))
