"""Order service: placement (server-priced, idempotent), reads, cancel.

Pricing rule: the backend recomputes every price from the menu.
unit = base + variant delta + add-ons; line total = unit x quantity.
No price/total from the client is ever read — the request schema
rejects price-like fields outright (extra='forbid').
Tax stays 0 (decision D1 undecided).

Idempotency (Stage 3.1, api-plan.md § 10): claim-before-create.
The claim row is inserted FIRST (response NULL) and its own commit
is the atomic claim — the DB unique constraint on
(restaurant_id, table_session_id, endpoint, key) serialises
concurrent claimants. The order transaction then sets the claim's
order_id, and the stored response is filled in afterwards. A
competing request waits for the owner (up to 10 s) and replays the
stored response, or 409s with ``idempotency_in_progress`` if the
owner is still working. A failed order creation deletes the claim,
so a corrected retry can proceed.
"""
import hashlib
import json
import logging
import time
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

logger = logging.getLogger(__name__)

ORDER_ENDPOINT = "POST /api/v1/orders"
IDEMPOTENCY_TTL = timedelta(hours=24)
# How long a competing request waits for the claim owner to finish.
IDEMPOTENCY_WAIT_SECONDS = 10.0
IDEMPOTENCY_WAIT_POLL_SECONDS = 0.05
# Claim attempts before giving up (a retry only follows an expired
# claim being deleted).
IDEMPOTENCY_MAX_CLAIM_ATTEMPTS = 2
# Retries for filling in the stored response after the order commits.
IDEMPOTENCY_FILL_ATTEMPTS = 3


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


def _is_expired(row: IdempotencyKey) -> bool:
    """True when the claim's TTL has passed (SQLite returns naive
    datetimes; Postgres returns aware ones — normalise first)."""
    if row.expires_at is None:
        return False
    expires = row.expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    return expires <= _now()


def _get_claim(
    db: Session, restaurant_id: UUID, session_id: UUID, key: str
) -> IdempotencyKey | None:
    """Fresh read of a claim row (callers expire the session first so
    a concurrent owner's commit is visible)."""
    return (
        db.execute(
            select(IdempotencyKey).where(
                IdempotencyKey.restaurant_id == restaurant_id,
                IdempotencyKey.table_session_id == session_id,
                IdempotencyKey.endpoint == ORDER_ENDPOINT,
                IdempotencyKey.key == key,
            )
        )
        .scalars()
        .first()
    )


def _try_claim(
    db: Session,
    restaurant_id: UUID,
    session_id: UUID,
    key: str,
    request_hash: str,
) -> IdempotencyKey | None:
    """Atomically claim the scoped key: insert the pending row and
    commit only that insert. Returns the row on success, None when
    the unique constraint says another request already owns the key.
    The insert is the serialisation point — two concurrent claimants
    cannot both succeed."""
    row = IdempotencyKey(
        restaurant_id=restaurant_id,
        table_session_id=session_id,
        endpoint=ORDER_ENDPOINT,
        key=key,
        request_hash=request_hash,
        response_status=None,
        response_body=None,
        order_id=None,
        expires_at=_now() + IDEMPOTENCY_TTL,
    )
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return None
    return row


def _delete_claim(db: Session, claim: IdempotencyKey) -> None:
    """Best-effort delete of a claim row in its own commit (used when
    the claim expired, or when order creation failed after claiming).
    Never raises — a stuck claim is worse than a logged warning."""
    try:
        db.rollback()
        row = db.get(IdempotencyKey, claim.id)
        if row is not None:
            db.delete(row)
            db.commit()
    except Exception:
        db.rollback()
        logger.exception("Failed to delete idempotency claim %s", claim.id)


def _rebuild_replay(db: Session, order_id: UUID) -> tuple[dict, int] | None:
    """Rebuild the stored response from an order whose claim has
    order_id set but no response_body yet (owner committed the order
    but has not filled the response in). Returns None if the order
    cannot be loaded."""
    order = db.get(Order, order_id)
    if order is None:
        return None
    response = OrderPlacementResponse(order=_order_to_out(db, order))
    return response.model_dump(by_alias=True, mode="json"), 201


def _wait_for_owner(
    db: Session,
    restaurant_id: UUID,
    session_id: UUID,
    key: str,
    request_hash: str,
) -> tuple[dict, int, bool] | str:
    """Wait for the request that owns the claim to finish (up to
    IDEMPOTENCY_WAIT_SECONDS, polling the row fresh each time).
    Returns a (body, status, replayed=True) tuple to return to the
    client, or the string "retry" when the claim was expired and
    deleted (caller should attempt the claim again). Raises ApiError
    for a body mismatch (422) or a still-pending owner (409)."""
    deadline = time.monotonic() + IDEMPOTENCY_WAIT_SECONDS
    while True:
        db.expire_all()
        row = _get_claim(db, restaurant_id, session_id, key)
        if row is None:
            # Owner deleted it (expired cleanup) — claim again.
            return "retry"
        if _is_expired(row):
            _delete_claim(db, row)
            return "retry"
        if row.request_hash != request_hash:
            raise ApiError(
                422,
                "idempotency_key_reused",
                "This Idempotency-Key was already used with a different request body.",
                {"field": "Idempotency-Key"},
            )
        if row.response_body is not None:
            return row.response_body, row.response_status or 201, True
        if time.monotonic() >= deadline:
            if row.order_id is not None:
                rebuilt = _rebuild_replay(db, row.order_id)
                if rebuilt is not None:
                    body, status = rebuilt
                    return body, status, True
            raise ApiError(
                409,
                "idempotency_in_progress",
                "An order with this Idempotency-Key is already being placed. "
                "Retry with the same key.",
                {"field": "Idempotency-Key"},
            )
        time.sleep(IDEMPOTENCY_WAIT_POLL_SECONDS)


def _acquire_claim(
    db: Session,
    restaurant_id: UUID,
    session_id: UUID,
    key: str,
    request_hash: str,
) -> tuple[IdempotencyKey | None, tuple[dict, int, bool] | None]:
    """Claim the scoped key, waiting for a competing owner when the
    claim already exists. Returns (claim_row, None) when this request
    owns the claim, or (None, (body, status, replayed=True)) when the
    owner already finished and this request is a replay. Raises
    ApiError for a body mismatch (422) or a still-pending owner
    (409 idempotency_in_progress) after the claim attempts run out."""
    for _ in range(IDEMPOTENCY_MAX_CLAIM_ATTEMPTS):
        row = _try_claim(db, restaurant_id, session_id, key, request_hash)
        if row is not None:
            return row, None
        outcome = _wait_for_owner(
            db, restaurant_id, session_id, key, request_hash
        )
        if outcome == "retry":
            continue
        return None, outcome
    raise ApiError(
        409,
        "idempotency_in_progress",
        "An order with this Idempotency-Key is already being placed. "
        "Retry with the same key.",
        {"field": "Idempotency-Key"},
    )


def place_order(
    db: Session,
    ctx: GuestContext,
    request: OrderPlacementRequest,
    idempotency_key: str,
) -> tuple[dict, int, bool]:
    """Place an order. Returns (response_body, status, replayed).

    Claim-before-create (Stage 3.1): the scoped key is claimed
    first — the claim insert's commit is the atomic serialisation
    point. A replay returns the stored response verbatim with
    replayed=True; same key + different body is a 422. If order
    creation fails after claiming, the claim is deleted so a
    corrected retry can proceed.
    """
    body = request.model_dump(by_alias=True, mode="json")
    request_hash = _canonical_hash(body)

    claim, replay = _acquire_claim(
        db, ctx.restaurant.id, ctx.session.id, idempotency_key, request_hash
    )
    if replay is not None:
        return replay

    try:
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
        claim.order_id = order.id  # atomic with the order transaction
        table = db.get(DiningTable, ctx.session.table_id)
        if table is not None and table.status == "available":
            table.status = "occupied"
        db.commit()
        db.refresh(order)
    except Exception:
        _delete_claim(db, claim)
        raise


    response = OrderPlacementResponse(order=_order_to_out(db, order))
    response_body = response.model_dump(by_alias=True, mode="json")

    # --- Fill the claim with the stored response --------------------
    # The order exists and the claim's order_id is set, so even if
    # this fill keeps failing, a waiter can rebuild the response
    # from the order. Log loudly, but still return the order.
    filled = False
    for _ in range(IDEMPOTENCY_FILL_ATTEMPTS):
        try:
            row = db.get(IdempotencyKey, claim.id)
            if row is None:
                filled = True
                break
            row.response_status = 201
            row.response_body = response_body
            db.commit()
            filled = True
            break
        except Exception:
            db.rollback()
            time.sleep(IDEMPOTENCY_WAIT_POLL_SECONDS)
    if not filled:
        logger.error(
            "Failed to fill idempotency response for key %s order %s "
            "after %d attempts; claim keeps order_id for rebuild.",
            idempotency_key, order.id, IDEMPOTENCY_FILL_ATTEMPTS,
        )
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
