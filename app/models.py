"""Data model.

Matches the spec (Product, Listing, PricePoint, ListItem) plus a few additions:
  * Product.variant / asin / google_product_id / source / source_ref - identifiers the
    adapters need so different variants are never merged.
  * Listing.condition / source / external_id / seller - one listing per
    (product, retailer, condition), so a renewed offer never hides the new one.
  * PricePoint.day - the local calendar day; at most one point per listing per day.
  * ListItem.id / paid_price / paid_retailer / alerted_at - for the archive and alerts.
  * Setting, ApiUsage, SnapshotRun - preferences, API budget, job log.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import (Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text,
                        UniqueConstraint)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(300))
    brand: Mapped[str | None] = mapped_column(String(120))
    model: Mapped[str | None] = mapped_column(String(120))
    variant: Mapped[str | None] = mapped_column(String(200))
    upc: Mapped[str | None] = mapped_column(String(20), index=True)
    image_url: Mapped[str | None] = mapped_column(String(600))
    asin: Mapped[str | None] = mapped_column(String(12), index=True)
    google_product_id: Mapped[str | None] = mapped_column(String(64), index=True)
    source: Mapped[str] = mapped_column(String(20))           # mock | serpapi | keepa
    source_ref: Mapped[str | None] = mapped_column(Text)      # adapter-specific reference
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_refreshed_at: Mapped[datetime | None] = mapped_column(DateTime)

    listings: Mapped[list["Listing"]] = relationship(
        back_populates="product", cascade="all, delete-orphan", order_by="Listing.id")
    list_items: Mapped[list["ListItem"]] = relationship(
        back_populates="product", cascade="all, delete-orphan")

    @property
    def is_demo(self) -> bool:
        return self.source == "mock"


class Listing(Base):
    __tablename__ = "listings"
    __table_args__ = (UniqueConstraint("product_id", "retailer", "condition"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), index=True)
    retailer: Mapped[str] = mapped_column(String(40))          # key from retailers.RETAILERS
    url: Mapped[str | None] = mapped_column(String(1000))
    requires_membership: Mapped[bool] = mapped_column(Boolean, default=False)
    condition: Mapped[str] = mapped_column(String(10), default="new")  # new | renewed | used
    source: Mapped[str] = mapped_column(String(20))            # adapter that last wrote it
    external_id: Mapped[str | None] = mapped_column(String(120))
    seller: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    history_sources: Mapped[str] = mapped_column(Text, default="")  # sources that backfilled history

    product: Mapped[Product] = relationship(back_populates="listings")

    def history_synced_from(self, source: str) -> bool:
        return source in (self.history_sources or "").split(",")

    def mark_history_synced(self, source: str) -> None:
        names = [n for n in (self.history_sources or "").split(",") if n]
        if source not in names:
            names.append(source)
        self.history_sources = ",".join(names)
    price_points: Mapped[list["PricePoint"]] = relationship(
        back_populates="listing", cascade="all, delete-orphan", order_by="PricePoint.day")


class PricePoint(Base):
    __tablename__ = "price_points"
    __table_args__ = (UniqueConstraint("listing_id", "day"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    listing_id: Mapped[int] = mapped_column(ForeignKey("listings.id", ondelete="CASCADE"), index=True)
    price: Mapped[float | None] = mapped_column(Float)        # None = no price shown (e.g. out of stock)
    shipping: Mapped[float | None] = mapped_column(Float)     # None = not reported
    in_stock: Mapped[bool] = mapped_column(Boolean, default=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)  # UTC
    day: Mapped[date] = mapped_column(Date, index=True)       # local calendar day
    source: Mapped[str] = mapped_column(String(20), default="")

    listing: Mapped[Listing] = relationship(back_populates="price_points")

    @property
    def total(self) -> float | None:
        if self.price is None:
            return None
        return round(self.price + (self.shipping or 0.0), 2)


class ListItem(Base):
    __tablename__ = "list_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), index=True)
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    target_price: Mapped[float | None] = mapped_column(Float)
    priority: Mapped[int] = mapped_column(Integer, default=2)  # 1 high, 2 medium, 3 low
    notes: Mapped[str] = mapped_column(Text, default="")
    purchased: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    purchased_at: Mapped[datetime | None] = mapped_column(DateTime)
    paid_price: Mapped[float | None] = mapped_column(Float)    # per unit, incl. shipping
    paid_retailer: Mapped[str | None] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    alerted_at: Mapped[datetime | None] = mapped_column(DateTime)

    product: Mapped[Product] = relationship(back_populates="list_items")

    PRIORITY_LABELS = {1: "High", 2: "Medium", 3: "Low"}

    @property
    def priority_label(self) -> str:
        return self.PRIORITY_LABELS.get(self.priority, "Medium")


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")


class ApiUsage(Base):
    __tablename__ = "api_usage"
    __table_args__ = (UniqueConstraint("source", "month"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(20))
    month: Mapped[str] = mapped_column(String(7))   # YYYY-MM
    calls: Mapped[int] = mapped_column(Integer, default=0)
    note: Mapped[str] = mapped_column(Text, default="")


class SnapshotRun(Base):
    __tablename__ = "snapshot_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    products_checked: Mapped[int] = mapped_column(Integer, default=0)
    products_skipped: Mapped[int] = mapped_column(Integer, default=0)
    points_written: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[int] = mapped_column(Integer, default=0)
    log: Mapped[str] = mapped_column(Text, default="")
