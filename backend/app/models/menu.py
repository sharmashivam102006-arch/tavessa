"""Menu: categories, items, variants, per-item add-ons.

Add-on scoping choice (Stage 3, recorded in database-design.md): the
blueprint (§§ 3.10–3.11) has a global ``add_ons`` catalogue plus an
``item_add_ons`` join with a per-item price. The Zaika demo data, however,
defines add-ons **per item** — the same name/price pair is repeated with
per-item ids (e.g. ``extra-gravy`` on butter chicken vs paneer butter
masala) and there is no shared catalogue id in the frontend contract.
Stage 3 therefore stores one ``item_add_ons`` row scoped to
``(restaurant_id, menu_item_id)`` with ``slug`` unique per item; the
slug is the frontend add-on id (e.g. ``extra-gravy``). No separate
catalogue table is created. If a shared catalogue is ever needed, it is
a migration that adds ``add_ons`` and re-points these rows — the wire
shape (id, name, pricePaise, isAvailable) does not change.
"""
import uuid

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Integer,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.common import IdMixin, TimestampMixin


class MenuCategory(IdMixin, TimestampMixin, Base):
    __tablename__ = "menu_categories"

    restaurant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("restaurants.id"), nullable=False, index=True
    )
    slug: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    restaurant: Mapped["Restaurant"] = relationship("Restaurant", back_populates="categories")
    items: Mapped[list["MenuItem"]] = relationship(
        "MenuItem", back_populates="category", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("restaurant_id", "slug", name="uq_menu_categories_restaurant_slug"),
    )


class MenuItem(IdMixin, TimestampMixin, Base):
    __tablename__ = "menu_items"

    restaurant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("restaurants.id"), nullable=False, index=True
    )
    category_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("menu_categories.id"), nullable=False, index=True
    )
    slug: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    # Money: integer paise, never float.
    price_paise: Mapped[int] = mapped_column(BigInteger, nullable=False)
    is_veg: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_available: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_popular: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    image_url: Mapped[str | None] = mapped_column(Text)
    # Kitchen station as plain text in Stage 3 ("Main Kitchen", "Tandoor",
    # "Beverages", "Dessert"). kitchen_stations becomes its own table in
    # Stage 4; the wire value is the station name either way.
    station: Mapped[str] = mapped_column(Text, nullable=False, default="Main Kitchen")
    variant_group_label: Mapped[str | None] = mapped_column(Text)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    category: Mapped["MenuCategory"] = relationship("MenuCategory", back_populates="items")
    variants: Mapped[list["ItemVariant"]] = relationship(
        "ItemVariant", back_populates="menu_item", cascade="all, delete-orphan"
    )
    add_ons: Mapped[list["ItemAddon"]] = relationship(
        "ItemAddon", back_populates="menu_item", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("restaurant_id", "slug", name="uq_menu_items_restaurant_slug"),
        CheckConstraint("price_paise >= 0", name="menu_item_price_non_negative"),
    )


class ItemVariant(IdMixin, TimestampMixin, Base):
    __tablename__ = "item_variants"

    menu_item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("menu_items.id", ondelete="CASCADE"), nullable=False, index=True
    )
    slug: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    # May be negative (e.g. Half −10000 paise). The service enforces
    # base + delta + add-ons >= 0 at order time.
    price_delta_paise: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_available: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    menu_item: Mapped["MenuItem"] = relationship("MenuItem", back_populates="variants")

    __table_args__ = (
        UniqueConstraint("menu_item_id", "slug", name="uq_item_variants_item_slug"),
    )


class ItemAddon(IdMixin, TimestampMixin, Base):
    """An add-on offered for one menu item, at that item's price.

    Table name keeps the blueprint's ``item_add_ons``; the row itself
    carries name + price (no separate catalogue table — see module doc).
    """

    __tablename__ = "item_add_ons"

    restaurant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("restaurants.id"), nullable=False, index=True
    )
    menu_item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("menu_items.id", ondelete="CASCADE"), nullable=False, index=True
    )
    slug: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    price_paise: Mapped[int] = mapped_column(BigInteger, nullable=False)
    is_available: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    menu_item: Mapped["MenuItem"] = relationship("MenuItem", back_populates="add_ons")

    __table_args__ = (
        UniqueConstraint("menu_item_id", "slug", name="uq_item_add_ons_item_slug"),
        CheckConstraint("price_paise >= 0", name="item_add_on_price_non_negative"),
    )
