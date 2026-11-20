"""Refreshing prices from sources, and turning listings into comparison rows."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select

from ..adapters.base import Offer, SourceError
from ..models import Listing, PricePoint, Product
from ..retailers import CONDITION_LABELS, RETAILERS, retailer_name, search_link
from ..timeutil import relative_time
from .context import Ctx

HISTORY_DAYS = 400


@dataclass
class RefreshReport:
    product_id: int
    points: int = 0
    history_points: int = 0
    new_listings: int = 0
    sources_ok: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _noon_utc(day: date, tzname: str) -> datetime:
    local = datetime.combine(day, time(12, 0), tzinfo=ZoneInfo(tzname))
    return local.astimezone(timezone.utc).replace(tzinfo=None)


def get_point(session, listing: Listing, day: date) -> PricePoint | None:
    for p in listing.price_points:
        if p.day == day:
            return p
    return None


def upsert_point(session, listing: Listing, day: date, price, shipping, in_stock: bool,
                 captured_at: datetime, source: str, overwrite: bool = True,
                 existing: PricePoint | None = None) -> bool:
    """One point per listing per local day. Returns True if something was written."""
    if existing is None:
        existing = get_point(session, listing, day)
    if existing is not None:
        if not overwrite:
            return False
        existing.price, existing.shipping, existing.in_stock = price, shipping, in_stock
        existing.captured_at, existing.source = captured_at, source
        return True
    point = PricePoint(price=price, shipping=shipping, in_stock=in_stock,
                       captured_at=captured_at, day=day, source=source)
    listing.price_points.append(point)
    session.add(point)
    return True


def get_or_create_listing(session, product: Product, retailer: str, condition: str,
                          source: str) -> tuple[Listing, bool]:
    for l in product.listings:
        if l.retailer == retailer and l.condition == condition:
            return l, False
    listing = Listing(retailer=retailer, condition=condition, source=source,
                      requires_membership=RETAILERS[retailer].membership)
    product.listings.append(listing)
    session.add(listing)
    session.flush()
    return listing, True


def _apply_offer(ctx: Ctx, product: Product, offer: Offer, source_name: str) -> tuple[Listing, bool]:
    listing, created = get_or_create_listing(ctx.session, product, offer.retailer, offer.condition, source_name)
    listing.source = source_name
    listing.url = offer.url or listing.url
    listing.requires_membership = offer.requires_membership or RETAILERS[offer.retailer].membership
    listing.seller = offer.seller
    if offer.external_id:
        listing.external_id = offer.external_id
    if offer.retailer == "amazon" and offer.external_id and not product.asin:
        product.asin = offer.external_id
    upsert_point(ctx.session, listing, ctx.today, offer.price, offer.shipping, offer.in_stock,
                 ctx.now, source_name)
    return listing, created


def _backfill(ctx: Ctx, source, listing: Listing, report: RefreshReport) -> None:
    if not source.provides_history or listing.history_synced_from(source.name):
        return
    try:
        points = source.get_history(listing, ctx.today - timedelta(days=HISTORY_DAYS))
    except SourceError as exc:
        report.errors.append(f"{source.label}: {exc}")
        return
    by_day = {p.day: p for p in listing.price_points}
    with ctx.session.no_autoflush:
        for hp in points:
            if hp.day >= ctx.today:
                continue  # today's point comes from the live offer
            existing = by_day.get(hp.day)
            # Keepa is the authoritative Amazon history; it may replace other backfilled days.
            overwrite = existing is not None and source.name == "keepa" and existing.source != "keepa"
            if existing is None or overwrite:
                upsert_point(ctx.session, listing, hp.day, hp.price, hp.shipping, hp.in_stock,
                             _noon_utc(hp.day, ctx.tzname), source.name, overwrite=True,
                             existing=existing)
                report.history_points += 1
    listing.mark_history_synced(source.name)


def refresh_product(ctx: Ctx, product: Product) -> RefreshReport:
    report = RefreshReport(product.id)
    for source in ctx.sources.pricers():
        if not source.handles(product):     # re-checked each loop: SerpApi may discover the ASIN
            continue
        try:
            offers = source.get_current_prices(product)
        except SourceError as exc:
            report.errors.append(f"{source.label}: {exc}")
            continue
        report.sources_ok.append(source.name)
        for offer in offers:
            if offer.retailer not in RETAILERS:
                continue
            listing, created = _apply_offer(ctx, product, offer, source.name)
            report.points += 1
            report.new_listings += int(created)
            _backfill(ctx, source, listing, report)
    if report.sources_ok:
        product.last_refreshed_at = ctx.now
    ctx.session.flush()
    return report


def log_manual_price(ctx: Ctx, product: Product, retailer: str, price: float, shipping: float | None,
                     in_stock: bool = True, condition: str = "new", url: str | None = None,
                     day: date | None = None) -> Listing:
    if retailer not in RETAILERS:
        raise ValueError("Unknown retailer")
    listing, _ = get_or_create_listing(ctx.session, product, retailer, condition, "manual")
    if url:
        listing.url = url
    day = day or ctx.today
    captured = ctx.now if day == ctx.today else _noon_utc(day, ctx.tzname)
    upsert_point(ctx.session, listing, day, price, shipping, in_stock, captured, "manual")
    ctx.session.flush()
    return listing


# ---------------------------------------------------------------------------
# Comparison rows
# ---------------------------------------------------------------------------
@dataclass
class Row:
    retailer: str
    retailer_label: str
    condition: str = "new"
    listing_id: int | None = None
    seller: str | None = None
    price: float | None = None
    shipping: float | None = None
    total: float | None = None
    in_stock: bool = False
    requires_membership: bool = False
    url: str | None = None
    last_checked: datetime | None = None
    last_checked_label: str = ""
    day: date | None = None
    stale: bool = False
    source: str = ""
    eligible: bool = False
    note: str = ""               # why it isn't counted toward "best"
    is_best: bool = False
    placeholder: bool = False
    tracking_since: date | None = None

    @property
    def condition_label(self) -> str:
        return CONDITION_LABELS.get(self.condition, self.condition.title())

    @property
    def sort_key(self):
        if self.placeholder:
            return (3, 0.0, self.retailer_label)
        if self.price is None:
            return (2, 0.0, self.retailer_label)
        if not self.in_stock:
            return (1, self.total or 0.0, self.retailer_label)
        return (0, self.total or 0.0, self.retailer_label)


def latest_point(listing: Listing) -> PricePoint | None:
    return max(listing.price_points, key=lambda p: p.day) if listing.price_points else None


def first_day(listing: Listing) -> date | None:
    return min(p.day for p in listing.price_points) if listing.price_points else None


def listing_rows(ctx: Ctx, product: Product, with_placeholders: bool = True) -> list[Row]:
    rows: list[Row] = []
    for l in product.listings:
        pt = latest_point(l)
        row = Row(retailer=l.retailer, retailer_label=retailer_name(l.retailer, product.brand),
                  condition=l.condition, listing_id=l.id, seller=l.seller,
                  requires_membership=l.requires_membership, url=l.url, source=l.source,
                  tracking_since=first_day(l))
        if pt is not None:
            row.price, row.shipping, row.total = pt.price, pt.shipping, pt.total
            row.in_stock, row.last_checked, row.day, row.source = pt.in_stock, pt.captured_at, pt.day, pt.source
            row.last_checked_label = relative_time(pt.captured_at, ctx.now, ctx.tzname)
            row.stale = (ctx.today - pt.day).days > ctx.stale_days
        reasons = []
        if row.price is None:
            reasons.append("No price")
        elif not row.in_stock:
            reasons.append("Out of stock")
        if row.stale:
            reasons.append(f"Price older than {ctx.stale_days} days")
        if row.condition != "new" and not ctx.prefs.include_nonnew:
            reasons.append(f"{row.condition_label} - not counted")
        if row.requires_membership and not ctx.prefs.is_member(row.retailer):
            reasons.append("Membership required")
        row.eligible = not reasons
        row.note = "; ".join(reasons)
        rows.append(row)
    if with_placeholders:
        present = {r.retailer for r in rows}
        query = " ".join(x for x in (product.brand, product.model or product.name) if x)
        for key, r in RETAILERS.items():
            if r.core and key not in present:
                rows.append(Row(retailer=key, retailer_label=retailer_name(key, product.brand),
                                placeholder=True, requires_membership=r.membership,
                                url=search_link(key, query),
                                note=("Not carried in the demo data" if product.is_demo
                                      else "Not found on Google Shopping")))
    rows.sort(key=lambda r: r.sort_key)
    best = min((r for r in rows if r.eligible), key=lambda r: (r.total, r.retailer_label), default=None)
    if best:
        best.is_best = True
    return rows


def best_row(rows: list[Row]) -> Row | None:
    return next((r for r in rows if r.is_best), None)
