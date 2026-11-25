"""Order service: placement (server-priced, idempotent), reads, cancel.

Pricing rule: the backend recomputes every price from the menu.
unit = base + variant delta + add-ons; line total = unit x quantity.
No price/total from the client is ever read — the request schema
rejects price-like fields outright (extra='forbid').
Tax stays 0 (decision D1 undecided).

Idempotency (Stage 3.2, api-plan.md § 10): SINGLE TRANSACTION.
Claim + order + stored response are created in ONE transaction:
insert the claim row (flush, no commit), validate and price the
lines, create the order (claim.order_id set in the same transaction),
fill the stored response, commit once. Any failure rolls the whole
thing back — no claim, no partial order, nothing for a later retry
to trip over.

Concurrency is PostgreSQL's unique-index speculative insertion: a
concurrent INSERT of the same scoped key BLOCKS until the owner's
transaction commits or aborts. A commit surfaces as a unique
violation for the waiter, which then reads the committed row and
replays it; an abort — including a process crash, which Postgres
treats as an abort when the connection drops — lets the waiter's
INSERT proceed and it becomes the owner. There is no separately
committed claim for a crash to orphan, so there is no lease, no
polling, and no fencing to get wrong.

Two leftovers are handled explicitly, not by the steady state:
(a) pre-3.2 (Stage 3.1) rows committed with response NULL — a
recovery path in the conflict handler (rebuild from order_id, or
reclaim a stale row older than 60 s); (b) a lock timeout, bounded
by SET LOCAL lock_timeout (PostgreSQL only), mapped to 409
``idempotency_in_progress`` — retry with the same key.
"""
import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.core.errors import ApiError
from app.models import (
    Bill,
    BillItem,
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
# Bound for how long one placement transaction waits on a lock
# (PostgreSQL only — see _set_lock_timeout). The speculative-insert
# wait on a concurrent owner's claim and the restaurant row lock in
# _next_order_number both honour it; a timeout surfaces as 409
# idempotency_in_progress, and the client retries with the same key.
IDEMPOTENCY_LOCK_TIMEOUT_SECONDS = 10
# A pre-3.2 (Stage 3.1) pending claim older than this is abandoned:
# its owner can never come back, because the separate claim commit
# is gone. A newer pending row cannot exist under the
# single-transaction design, so it 409s instead of being reclaimed.
LEGACY_CLAIM_MAX_AGE = timedelta(seconds=60)
# Claim attempts before giving up (a retry only follows a stale or
# expired legacy row being deleted in the conflict path).
IDEMPOTENCY_MAX_CLAIM_ATTEMPTS = 2


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


def _is_lock_timeout(exc: BaseException) -> bool:
    """True for a PostgreSQL lock_timeout failure (SQLSTATE 55P03).
    SQLAlchemy surfaces it as OperationalError wrapping the psycopg
    error; SQLite has no equivalent, so this is always False there."""
    orig = getattr(exc, "orig", None)
    return getattr(orig, "sqlstate", None) == "55P03"


def _set_lock_timeout(db: Session) -> None:
    """Bound lock waits for this transaction (PostgreSQL only).
    Must run first, inside the transaction: it covers the
    speculative-insert wait on a concurrent owner's claim and the
    restaurant row lock in _next_order_number. A timeout raises
    LockNotAvailable (55P03), mapped to 409 idempotency_in_progress
    by the caller. The SQLite test suite never sees this statement."""
    if db.get_bind().dialect.name == "postgresql":
        db.execute(
            text(f"SET LOCAL lock_timeout = '{IDEMPOTENCY_LOCK_TIMEOUT_SECONDS}s'")
        )


def _claim_age(row: IdempotencyKey) -> timedelta:
    """Age of a claim row (SQLite returns naive datetimes; Postgres
    returns aware ones — normalise first, as in _is_expired)."""
    created = row.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    return _now() - created


def _in_progress_error() -> ApiError:
    return ApiError(
        409,
        "idempotency_in_progress",
        "An order with this Idempotency-Key is already being placed. "
        "Retry with the same key.",
        {"field": "Idempotency-Key"},
    )


def _delete_claim_row(db: Session, row: IdempotencyKey) -> None:
    """Delete a legacy/expired claim row in its own commit. Uses a
    Core DELETE (no rowcount check): a concurrent recovery may have
    deleted the row first, and 0 matched rows is a fine outcome —
    the ORM delete would warn on exactly that race. Never raises —
    a stuck row is worse than a logged warning, and the caller
    retries the claim either way."""
    try:
        db.execute(delete(IdempotencyKey).where(IdempotencyKey.id == row.id))
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Failed to delete idempotency claim %s", row.id)


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


def _handle_claim_conflict(
    db: Session,
    restaurant_id: UUID,
    session_id: UUID,
    key: str,
    request_hash: str,
) -> tuple[dict, int, bool] | None:
    """Called after this request's claim INSERT raised a unique
    violation, which under PostgreSQL means a COMMITTED row with the
    scoped key exists (an in-flight owner would have made the INSERT
    block instead). Returns (body, status, replayed=True) for a
    replay, or None when the caller should attempt the claim again
    (the row was stale/expired and deleted, or vanished). Raises
    ApiError for a body mismatch (422) or a fresh pending legacy row
    (409 idempotency_in_progress).

    Steady state (Stage 3.2) can only produce the first two cases:
    the row is committed with claim + order + response together, so
    response_body is always set. The remaining cases are the one-time
    Stage 3.1 compatibility path for rows committed with response
    NULL by the old three-transaction design.
    """
    db.expire_all()
    row = _get_claim(db, restaurant_id, session_id, key)
    if row is None:
        # Vanished between the violation and the read (a concurrent
        # recovery deleted it) — claim again.
        return None
    if _is_expired(row):
        _delete_claim_row(db, row)
        return None
    if row.request_hash != request_hash:
        raise ApiError(
            422,
            "idempotency_key_reused",
            "This Idempotency-Key was already used with a different request body.",
            {"field": "Idempotency-Key"},
        )
    if row.response_body is not None:
        return row.response_body, row.response_status or 201, True
    # Legacy (pre-3.2) pending row only: response was never filled.
    if row.order_id is not None:
        rebuilt = _rebuild_replay(db, row.order_id)
        if rebuilt is not None:
            body, status = rebuilt
            return body, status, True
        # order_id set but the order is gone — impossible via the FK;
        # treat the row as corrupt and reclaim it.
        _delete_claim_row(db, row)
        return None
    if _claim_age(row) > LEGACY_CLAIM_MAX_AGE:
        # Abandoned: no order, no response, owner long gone.
        _delete_claim_row(db, row)
        return None
    raise _in_progress_error()


def place_order(
    db: Session,
    ctx: GuestContext,
    request: OrderPlacementRequest,
    idempotency_key: str,
) -> tuple[dict, int, bool]:
    """Place an order. Returns (response_body, status, replayed).

    Single transaction (Stage 3.2): the claim INSERT, validation,
    order creation and response fill all happen in ONE transaction
    with ONE commit at the end. Any failure rolls everything back —
    no claim row survives a failed placement, so a corrected retry
    with the same key always proceeds. A replay returns the stored
    response verbatim with replayed=True; same key + different body
    is a 422.
    """
    body = request.model_dump(by_alias=True, mode="json")
    request_hash = _canonical_hash(body)

    for _ in range(IDEMPOTENCY_MAX_CLAIM_ATTEMPTS):
        outcome = _place_order_once(
            db, ctx, request, request_hash, idempotency_key
        )
        if outcome is not None:
            return outcome
    raise _in_progress_error()


def _place_order_once(
    db: Session,
    ctx: GuestContext,
    request: OrderPlacementRequest,
    request_hash: str,
    idempotency_key: str,
) -> tuple[dict, int, bool] | None:
    """One placement attempt, entirely in ONE transaction. Returns
    (body, status, replayed) — replayed=True only for a stored
    replay — or None when the claim conflict handler deleted a stale
    legacy row and the caller should attempt the claim again."""
    _set_lock_timeout(db)

    # --- Claim: INSERT + flush only (no commit) ---------------------
    # Under PostgreSQL this INSERT blocks on a concurrent owner's
    # uncommitted claim (unique-index speculative insertion) until
    # that transaction commits or aborts — a process crash aborts
    # the owner's transaction when its connection drops, so the
    # waiter then proceeds as owner. A commit by the owner surfaces
    # here as a unique violation instead.
    claim = IdempotencyKey(
        restaurant_id=ctx.restaurant.id,
        table_session_id=ctx.session.id,
        endpoint=ORDER_ENDPOINT,
        key=idempotency_key,
        request_hash=request_hash,
        response_status=None,
        response_body=None,
        order_id=None,
        expires_at=_now() + IDEMPOTENCY_TTL,
    )
    db.add(claim)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        return _handle_claim_conflict(
            db, ctx.restaurant.id, ctx.session.id, idempotency_key, request_hash
        )
    except OperationalError as exc:
        db.rollback()
        if _is_lock_timeout(exc):
            raise _in_progress_error()
        raise

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
        claim.order_id = order.id  # same transaction as the order
        table = db.get(DiningTable, ctx.session.table_id)
        if table is not None and table.status == "available":
            table.status = "occupied"
        # --- Fill the stored response, still in the same tx ----------
        # Built before commit so claim, order and stored response land
        # atomically: a later replay reads them all, never a half.
        response = OrderPlacementResponse(order=_order_to_out(db, order))
        response_body = response.model_dump(by_alias=True, mode="json")
        claim.response_status = 201
        claim.response_body = response_body
        db.commit()
    except ApiError:
        # Validation / inactive session: roll back claim + order.
        db.rollback()
        raise
    except OperationalError as exc:
        db.rollback()
        if _is_lock_timeout(exc):
            raise _in_progress_error()
        raise
    except Exception:
        db.rollback()
        raise
    db.refresh(order)
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
    in the same transaction.

    Stage 9: also blocked once the order is on a FROZEN bill
    (payment_requested / paid / refunded) or on an issued bill
    that already has cash against it. A frozen bill never changes
    (Stage 6 rule), so letting the guest cancel would leave them
    paying for food they cancelled — the only honest outcomes are
    staff void (unpaid) or refund (paid). The 409 names the bill
    so staff can find it. Cancelling while the bill is still an
    unpaid ``issued`` bill is fine: the bill regenerates without
    the cancelled items (Stage 6 decision 8).
    """
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
    frozen_bill = (
        db.execute(
            select(Bill)
            .join(BillItem, BillItem.bill_id == Bill.id)
            .where(
                BillItem.order_id == order.id,
                Bill.table_session_id == ctx.session.id,
                (
                    Bill.status.in_((
                        "payment_requested", "paid",
                        "partially_refunded", "refunded",
                    ))
                    | (
                        (Bill.status == "issued")
                        & (Bill.amount_paid_paise > 0)
                    )
                ),
            )
        )
        .scalars()
        .first()
    )
    if frozen_bill is not None:
        raise ApiError(
            409,
            "conflict",
            "This order is already on a bill that has been requested "
            "or paid, so it can no longer be cancelled from the app — "
            "please ask a member of staff (they can void or refund it).",
            {
                "status": order.status,
                "bill_id": str(frozen_bill.id),
                "bill_number": frozen_bill.bill_number,
                "bill_status": frozen_bill.status,
            },
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
