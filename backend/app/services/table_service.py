"""Guest table service: QR tokens, sessions, menu reads.

Every guest query is scoped by the session resolved from the QR token —
the restaurant/table are derived from the token, never from client input.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import ApiError
from app.core.security import generate_table_token, hash_table_token
from app.models import (
    DiningTable,
    ItemAddon,
    ItemVariant,
    MenuCategory,
    MenuItem,
    Restaurant,
    TableSession,
    TableToken,
)
from app.schemas.guest import (
    AddOnOut,
    GuestMenuResponse,
    GuestSessionOut,
    GuestSessionResponse,
    ItemVariantOut,
    MenuCategoryOut,
    MenuItemOut,
    RestaurantInfoOut,
    TableOut,
)

# Demo tokens expire in 30 days — PROVISIONAL pending decision D5
# (session/token expiry). Recorded in api-plan.md § 3.
DEMO_TOKEN_EXPIRY_DAYS = 30


@dataclass
class GuestContext:
    """Everything a guest request is scoped to, resolved from the token."""

    token: TableToken
    session: TableSession
    restaurant: Restaurant
    table: DiningTable
    raw_token: str


def _now() -> datetime:
    return datetime.now(timezone.utc)


def resolve_table_token(db: Session, raw_token: str) -> TableToken:
    """Hash-lookup of a raw QR token.

    Unknown token -> 401 invalid_table_token; revoked/expired -> 410
    session_expired (Stage 3 reconciliation decision). The raw token
    is never logged.
    """
    if not raw_token:
        raise ApiError(401, "invalid_table_token", "A valid table token is required.")
    token = (
        db.execute(
            select(TableToken).where(TableToken.token_hash == hash_table_token(raw_token))
        )
        .scalars()
        .first()
    )
    if token is None:
        raise ApiError(401, "invalid_table_token", "This QR code isn't valid.")
    if token.status == "revoked" or token.is_expired:
        raise ApiError(410, "session_expired", "This table session has expired.")
    if token.status != "active":
        raise ApiError(401, "invalid_table_token", "This QR code isn't valid.")
    token.last_used_at = _now()
    db.commit()
    return token


def get_or_create_session(db: Session, token: TableToken) -> TableSession:
    """Resume the table's active session, or open a new one."""
    existing = (
        db.execute(
            select(TableSession).where(
                TableSession.table_id == token.table_id,
                TableSession.status == "active",
            )
        )
        .scalars()
        .first()
    )
    if existing is not None:
        return existing
    session = TableSession(
        restaurant_id=token.restaurant_id,
        branch_id=db.get(DiningTable, token.table_id).branch_id,
        table_id=token.table_id,
        token_id=token.id,
        status="active",
        opened_at=_now(),
    )
    db.add(session)
    table = db.get(DiningTable, token.table_id)
    if table is not None and table.status == "available":
        table.status = "occupied"
    try:
        db.commit()
    except IntegrityError:
        # Lost a race with another open for the same table: resume it.
        db.rollback()
        existing = (
            db.execute(
                select(TableSession).where(
                    TableSession.table_id == token.table_id,
                    TableSession.status == "active",
                )
            )
            .scalars()
            .first()
        )
        if existing is None:
            raise
        return existing
    db.refresh(session)
    return session


def build_session_response(
    db: Session, session: TableSession, raw_token: str
) -> GuestSessionResponse:
    restaurant = db.get(Restaurant, session.restaurant_id)
    table = db.get(DiningTable, session.table_id)
    return GuestSessionResponse(
        session=GuestSessionOut(
            id=session.id,
            restaurant_id=session.restaurant_id,
            table_id=session.table_id,
            table_number=table.number,
            status=session.status,
            opened_at=session.opened_at,
        ),
        restaurant=RestaurantInfoOut(
            id=restaurant.id,
            name=restaurant.name,
            tagline=restaurant.tagline,
            cuisine=restaurant.cuisine,
            address=restaurant.address,
            opening_hours=restaurant.opening_hours,
            phone=restaurant.phone,
            rating=restaurant.rating,
        ),
        table=TableOut(number=table.number),
        menu_url=f"/t/{raw_token}",
    )


def open_guest_session(db: Session, raw_token: str) -> GuestSessionResponse:
    token = resolve_table_token(db, raw_token)
    session = get_or_create_session(db, token)
    return build_session_response(db, session, raw_token)


def resolve_guest_context(db: Session, raw_token: str) -> GuestContext:
    """Resolve X-Table-Token (or a path token) to a full guest context."""
    token = resolve_table_token(db, raw_token)
    session = get_or_create_session(db, token)
    restaurant = db.get(Restaurant, session.restaurant_id)
    table = db.get(DiningTable, session.table_id)
    return GuestContext(
        token=token,
        session=session,
        restaurant=restaurant,
        table=table,
        raw_token=raw_token,
    )


def create_table_token(
    db: Session, table: DiningTable, expires_in_days: int | None = DEMO_TOKEN_EXPIRY_DAYS
) -> tuple[str, TableToken]:
    """Create a fresh token for a table, revoking any active one first.

    Returns (raw_token, row). The raw token is returned to the caller
    exactly once — for QR printing — and is never stored.
    """
    for old in db.execute(
        select(TableToken).where(
            TableToken.table_id == table.id, TableToken.status == "active"
        )
    ).scalars():
        old.status = "revoked"
    raw = generate_table_token()
    row = TableToken(
        restaurant_id=table.restaurant_id,
        table_id=table.id,
        token_hash=hash_table_token(raw),
        status="active",
        expires_at=_now() + timedelta(days=expires_in_days) if expires_in_days else None,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return raw, row


def get_menu(
    db: Session,
    restaurant_id: UUID,
    category_id: UUID | None = None,
    search: str | None = None,
) -> GuestMenuResponse:
    """Menu for a restaurant: only is_active categories; unavailable
    items are returned with isAvailable=false (sold-out display)."""
    categories = (
        db.execute(
            select(MenuCategory)
            .where(
                MenuCategory.restaurant_id == restaurant_id,
                MenuCategory.is_active.is_(True),
            )
            .order_by(MenuCategory.sort_order, MenuCategory.name)
        )
        .scalars()
        .all()
    )
    item_q = (
        select(MenuItem)
        .join(MenuCategory, MenuItem.category_id == MenuCategory.id)
        .where(
            MenuItem.restaurant_id == restaurant_id,
            MenuCategory.is_active.is_(True),
        )
    )
    if category_id is not None:
        item_q = item_q.where(MenuItem.category_id == category_id)
    if search:
        needle = f"%{search.strip().lower()}%"
        item_q = item_q.where(
            func.lower(MenuItem.name).like(needle)
            | func.lower(MenuItem.description).like(needle)
        )
    item_q = item_q.order_by(
        MenuCategory.sort_order, MenuItem.sort_order, MenuItem.name
    )
    items = db.execute(item_q).scalars().all()

    variants_by_item: dict[UUID, list[ItemVariantOut]] = {}
    add_ons_by_item: dict[UUID, list[AddOnOut]] = {}
    for item in items:
        variants = (
            db.execute(
                select(ItemVariant)
                .where(ItemVariant.menu_item_id == item.id)
                .order_by(ItemVariant.sort_order, ItemVariant.name)
            )
            .scalars()
            .all()
        )
        variants_by_item[item.id] = [
            ItemVariantOut(
                id=v.id,
                name=v.name,
                price_delta_paise=v.price_delta_paise,
                is_available=v.is_available,
            )
            for v in variants
        ]
        add_ons = (
            db.execute(
                select(ItemAddon)
                .where(ItemAddon.menu_item_id == item.id)
                .order_by(ItemAddon.name)
            )
            .scalars()
            .all()
        )
        add_ons_by_item[item.id] = [
            AddOnOut(
                id=a.id,
                name=a.name,
                price_paise=a.price_paise,
                is_available=a.is_available,
            )
            for a in add_ons
        ]

    return GuestMenuResponse(
        categories=[
            MenuCategoryOut(
                id=c.id,
                name=c.name,
                description=c.description,
                sort_order=c.sort_order,
                is_active=c.is_active,
            )
            for c in categories
        ],
        items=[
            MenuItemOut(
                id=i.id,
                category_id=i.category_id,
                name=i.name,
                description=i.description,
                price_paise=i.price_paise,
                is_veg=i.is_veg,
                is_available=i.is_available,
                is_popular=i.is_popular,
                image_url=i.image_url,
                station=i.station,
                variant_group_label=i.variant_group_label,
                variants=variants_by_item.get(i.id, []),
                add_ons=add_ons_by_item.get(i.id, []),
            )
            for i in items
        ],
    )
