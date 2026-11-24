"""Zaika seed — idempotent, rerunnable, no hardcoded DB ids.

Upserts by natural keys: restaurant slug ``zaika``, branch slug
``zaika-mg-road``, category slugs, item slugs (the frontend menu ids),
variant/add-on slugs per item. Running it twice changes nothing.
No QR tokens are created by seeding (see ``demo_tokens``).

Run from ``backend/``:
    DATABASE_URL=... python -m app.seed.zaika
"""
import logging
import sys

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models import (
    Branch,
    DiningTable,
    ItemAddon,
    ItemVariant,
    MenuCategory,
    MenuItem,
    Restaurant,
)
from app.seed.zaika_data import ZAIKA_DATA

logger = logging.getLogger(__name__)

RESTAURANT_SLUG = "zaika"
BRANCH_SLUG = "zaika-mg-road"
TABLE_NUMBERS = list(range(1, 11))


def seed_zaika(db: Session) -> dict:
    """Seed (or re-seed) Zaika. Returns counts of each entity."""
    data = ZAIKA_DATA
    rest_data = data["restaurant"]

    restaurant = (
        db.execute(select(Restaurant).where(Restaurant.slug == RESTAURANT_SLUG))
        .scalars()
        .first()
    )
    if restaurant is None:
        restaurant = Restaurant(
            slug=RESTAURANT_SLUG,
            name=rest_data["name"],
            tagline=rest_data["tagline"],
            cuisine=rest_data["cuisine"],
            address=rest_data["address"],
            opening_hours=rest_data["openingHours"],
            phone=rest_data["phone"],
            rating=rest_data["rating"],
        )
        db.add(restaurant)
        db.flush()
    else:
        # Refresh mutable display fields; never touch ids.
        restaurant.name = rest_data["name"]
        restaurant.tagline = rest_data["tagline"]
        restaurant.cuisine = rest_data["cuisine"]
        restaurant.address = rest_data["address"]
        restaurant.opening_hours = rest_data["openingHours"]
        restaurant.phone = rest_data["phone"]
        restaurant.rating = rest_data["rating"]

    branch = (
        db.execute(
            select(Branch).where(
                Branch.restaurant_id == restaurant.id,
                Branch.slug == BRANCH_SLUG,
            )
        )
        .scalars()
        .first()
    )
    if branch is None:
        branch = Branch(
            restaurant_id=restaurant.id,
            name="Zaika — MG Road",
            slug=BRANCH_SLUG,
            address=rest_data["address"],
            timezone="Asia/Kolkata",
        )
        db.add(branch)
        db.flush()
    elif not branch.timezone:
        branch.timezone = "Asia/Kolkata"

    for number in TABLE_NUMBERS:
        table = (
            db.execute(
                select(DiningTable).where(
                    DiningTable.restaurant_id == restaurant.id,
                    DiningTable.branch_id == branch.id,
                    DiningTable.number == number,
                )
            )
            .scalars()
            .first()
        )
        if table is None:
            db.add(DiningTable(
                restaurant_id=restaurant.id,
                branch_id=branch.id,
                number=number,
                status="available",
            ))
    db.flush()

    categories: dict[str, MenuCategory] = {}
    for idx, cat in enumerate(data["categories"]):
        row = (
            db.execute(
                select(MenuCategory).where(
                    MenuCategory.restaurant_id == restaurant.id,
                    MenuCategory.slug == cat["id"],
                )
            )
            .scalars()
            .first()
        )
        if row is None:
            row = MenuCategory(
                restaurant_id=restaurant.id,
                slug=cat["id"],
                name=cat["name"],
                description=cat["description"],
                sort_order=cat["sortOrder"],
                is_active=cat["isActive"],
            )
            db.add(row)
            db.flush()
        else:
            row.name = cat["name"]
            row.description = cat["description"]
            row.sort_order = cat["sortOrder"]
            row.is_active = cat["isActive"]
        categories[cat["id"]] = row

    variant_count = 0
    add_on_count = 0
    for idx, item in enumerate(data["items"]):
        row = (
            db.execute(
                select(MenuItem).where(
                    MenuItem.restaurant_id == restaurant.id,
                    MenuItem.slug == item["id"],
                )
            )
            .scalars()
            .first()
        )
        if row is None:
            row = MenuItem(
                restaurant_id=restaurant.id,
                category_id=categories[item["categoryId"]].id,
                slug=item["id"],
                name=item["name"],
                description=item["description"],
                price_paise=item["pricePaise"],
                is_veg=item["isVeg"],
                is_available=item["isAvailable"],
                is_popular=item["isPopular"],
                image_url=item["imageUrl"],
                station=item["station"],
                variant_group_label=item.get("variantGroupLabel"),
                sort_order=idx,
            )
            db.add(row)
            db.flush()
        else:
            row.category_id = categories[item["categoryId"]].id
            row.name = item["name"]
            row.description = item["description"]
            row.price_paise = item["pricePaise"]
            row.is_veg = item["isVeg"]
            row.is_available = item["isAvailable"]
            row.is_popular = item["isPopular"]
            row.image_url = item["imageUrl"]
            row.station = item["station"]
            row.variant_group_label = item.get("variantGroupLabel")
            row.sort_order = idx

        for v_idx, variant in enumerate(item.get("variants") or []):
            v_row = (
                db.execute(
                    select(ItemVariant).where(
                        ItemVariant.menu_item_id == row.id,
                        ItemVariant.slug == variant["id"],
                    )
                )
                .scalars()
                .first()
            )
            if v_row is None:
                db.add(ItemVariant(
                    menu_item_id=row.id,
                    slug=variant["id"],
                    name=variant["name"],
                    price_delta_paise=variant["priceDeltaPaise"],
                    sort_order=v_idx,
                    is_available=variant["isAvailable"],
                ))
            else:
                v_row.name = variant["name"]
                v_row.price_delta_paise = variant["priceDeltaPaise"]
                v_row.sort_order = v_idx
                v_row.is_available = variant["isAvailable"]
            variant_count += 1

        for add_on in item.get("addOns") or []:
            a_row = (
                db.execute(
                    select(ItemAddon).where(
                        ItemAddon.menu_item_id == row.id,
                        ItemAddon.slug == add_on["id"],
                    )
                )
                .scalars()
                .first()
            )
            if a_row is None:
                db.add(ItemAddon(
                    restaurant_id=restaurant.id,
                    menu_item_id=row.id,
                    slug=add_on["id"],
                    name=add_on["name"],
                    price_paise=add_on["pricePaise"],
                    is_available=add_on["isAvailable"],
                ))
            else:
                a_row.name = add_on["name"]
                a_row.price_paise = add_on["pricePaise"]
                a_row.is_available = add_on["isAvailable"]
            add_on_count += 1

    db.commit()
    counts = {
        "restaurants": 1,
        "branches": 1,
        "tables": len(TABLE_NUMBERS),
        "categories": len(data["categories"]),
        "items": len(data["items"]),
        "variants": variant_count,
        "add_ons": add_on_count,
    }
    return counts


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    db = SessionLocal()
    try:
        counts = seed_zaika(db)
    finally:
        db.close()
    print("Zaika seed complete (idempotent — safe to re-run):")
    for key, value in counts.items():
        print(f"  {key}: {value}")
    print("No QR tokens were created. Generate a demo token with:")
    print("  python -m app.seed.demo_tokens --table 5")
    return 0


if __name__ == "__main__":
    sys.exit(main())
