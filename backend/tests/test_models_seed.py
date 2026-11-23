"""Models + seed — Stage 3."""
from sqlalchemy import func, select

from app.models import (
    DiningTable,
    ItemAddon,
    ItemVariant,
    MenuCategory,
    MenuItem,
    Restaurant,
)
from app.seed.zaika import seed_zaika
from tests.helpers import seed


def _counts(db_session):
    return {
        "restaurants": db_session.execute(select(func.count()).select_from(Restaurant)).scalar_one(),
        "tables": db_session.execute(select(func.count()).select_from(DiningTable)).scalar_one(),
        "categories": db_session.execute(select(func.count()).select_from(MenuCategory)).scalar_one(),
        "items": db_session.execute(select(func.count()).select_from(MenuItem)).scalar_one(),
        "variants": db_session.execute(select(func.count()).select_from(ItemVariant)).scalar_one(),
        "add_ons": db_session.execute(select(func.count()).select_from(ItemAddon)).scalar_one(),
    }


def test_seed_idempotent(db_session):
    first = seed(db_session)
    before = _counts(db_session)
    second = seed(db_session)
    after = _counts(db_session)
    assert first == second
    assert before == after
    assert before["restaurants"] == 1
    assert before["categories"] == 6
    assert before["items"] == 24
    assert before["variants"] == 31
    assert before["add_ons"] == 22
    # Tables 1-10 always exist (other tests may add their own tables).
    numbers = sorted(
        t.number for t in db_session.execute(select(DiningTable)).scalars()
    )
    assert list(range(1, 11)) == [n for n in numbers if n <= 10]


def test_seed_exact_values(db_session):
    seed(db_session)
    butter = (
        db_session.execute(select(MenuItem).where(MenuItem.slug == "butter-chicken"))
        .scalars().first()
    )
    assert butter is not None
    assert butter.name == "Old Delhi Butter Chicken"
    assert butter.price_paise == 34900
    assert butter.is_veg is False
    assert butter.is_available is True
    assert butter.is_popular is True
    assert butter.station == "Main Kitchen"
    assert butter.variant_group_label == "Portion"
    half = (
        db_session.execute(
            select(ItemVariant).where(
                ItemVariant.menu_item_id == butter.id, ItemVariant.slug == "portion-half"
            )
        ).scalars().first()
    )
    assert half.price_delta_paise == -10000
    gravy = (
        db_session.execute(
            select(ItemAddon).where(
                ItemAddon.menu_item_id == butter.id, ItemAddon.slug == "extra-gravy"
            )
        ).scalars().first()
    )
    assert gravy.price_paise == 5000

    # Sold-out items from the demo menu.
    unavailable = sorted(
        i.slug for i in db_session.execute(
            select(MenuItem).where(MenuItem.is_available.is_(False))
        ).scalars()
    )
    assert unavailable == ["hara-bhara-kebab", "mutton-biryani"]

    # Relations: item -> category -> restaurant.
    category = db_session.get(MenuCategory, butter.category_id)
    assert category.slug == "main-course"
    assert category.restaurant_id == butter.restaurant_id
    restaurant = db_session.get(Restaurant, butter.restaurant_id)
    assert restaurant.slug == "zaika"
    assert restaurant.rating == 4.6

    # Tables 1-10 all exist and start available (other tests may add
    # their own tables, so filter to the seeded range).
    numbers = sorted(
        t.number for t in db_session.execute(select(DiningTable)).scalars()
    )
    assert [n for n in numbers if n <= 10] == list(range(1, 11))


def test_seed_rerun_updates_display_fields(db_session):
    seed(db_session)
    butter = (
        db_session.execute(select(MenuItem).where(MenuItem.slug == "butter-chicken"))
        .scalars().first()
    )
    butter.price_paise = 1  # simulate drift
    db_session.commit()
    seed_zaika(db_session)
    db_session.expire_all()
    butter = (
        db_session.execute(select(MenuItem).where(MenuItem.slug == "butter-chicken"))
        .scalars().first()
    )
    assert butter.price_paise == 34900
