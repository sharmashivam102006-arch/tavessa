"""Restaurant + Branch — the tenant (database-design.md §§ 3.1–3.2)."""
import uuid

from sqlalchemy import Boolean, CheckConstraint, Float, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.common import IdMixin, TimestampMixin


class Restaurant(IdMixin, TimestampMixin, Base):
    __tablename__ = "restaurants"

    name: Mapped[str] = mapped_column(Text, nullable=False)
    slug: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    tagline: Mapped[str | None] = mapped_column(Text)
    cuisine: Mapped[str | None] = mapped_column(Text)
    address: Mapped[str | None] = mapped_column(Text)
    opening_hours: Mapped[str | None] = mapped_column(Text)
    phone: Mapped[str | None] = mapped_column(Text)
    contact_name: Mapped[str | None] = mapped_column(Text)
    contact_email: Mapped[str | None] = mapped_column(Text)
    rating: Mapped[float | None] = mapped_column(Float)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="INR")
    # D1 (tax) undecided: tax is 0 in Stage 3. tax_mode is a placeholder
    # column only; no rate is invented.
    tax_mode: Mapped[str] = mapped_column(Text, nullable=False, default="inclusive")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    branches: Mapped[list["Branch"]] = relationship(
        "Branch", back_populates="restaurant", cascade="all, delete-orphan"
    )
    tables: Mapped[list["DiningTable"]] = relationship(
        "DiningTable", back_populates="restaurant"
    )
    categories: Mapped[list["MenuCategory"]] = relationship(
        "MenuCategory", back_populates="restaurant"
    )

    __table_args__ = (
        CheckConstraint("tax_mode IN ('inclusive', 'exclusive', 'none')", name="tax_mode_valid"),
    )


class Branch(IdMixin, TimestampMixin, Base):
    __tablename__ = "branches"

    restaurant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("restaurants.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    slug: Mapped[str] = mapped_column(Text, nullable=False)
    address: Mapped[str | None] = mapped_column(Text)
    # Branch-local timezone for dashboard/report date boundaries
    # (Stage 7). Defaults to Asia/Kolkata for the seeded Zaika
    # branch; never assume the server timezone.
    timezone: Mapped[str] = mapped_column(Text, nullable=False, default="Asia/Kolkata")
    contact_name: Mapped[str | None] = mapped_column(Text)
    contact_phone: Mapped[str | None] = mapped_column(Text)
    contact_email: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    restaurant: Mapped["Restaurant"] = relationship("Restaurant", back_populates="branches")

    __table_args__ = (
        UniqueConstraint("restaurant_id", "slug", name="uq_branches_restaurant_slug"),
    )
