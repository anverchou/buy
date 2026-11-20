"""Demo shopping list built from the mock catalogue, so the app is useful before any API keys."""
from __future__ import annotations

from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select

from ..adapters.mock import BY_KEY, MockAdapter, series
from ..models import ListItem, Product
from ..retailers import retailer_name
from .catalog import select_result
from .context import Ctx
from .pricing import refresh_product

# key, quantity, target price (per unit, incl. shipping), priority 1-3, notes
DEMO_LIST = [
    ("sony-wh1000xm5", 1, 249.00, 1, "For the December flights"),
    ("ninja-af161", 1, 99.00, 2, "Replacing the old 3-qt one"),
    ("dewalt-dcd771c2", 1, 79.00, 2, ""),
    ("samsung-un65du7200", 1, 329.00, 2, "Bedroom wall - measure first"),
    ("kitchenaid-ksm150-red", 1, 279.00, 3, "Gift - confirm the color"),
    ("logitech-mx-master-3s", 1, 59.00, 2, ""),
    ("vitamix-5200", 1, 299.00, 3, "Compare with Costco's Vitamix bundle"),
    ("tide-pods-112", 2, 27.00, 3, "Stock up"),
    ("tide-pods-152", 1, 36.00, 3, "Only if it's cheaper than two 81-packs"),
]
# key, quantity, purchase day, retailer
DEMO_ARCHIVE = [
    ("weber-kettle-22", 1, date(2026, 7, 4), "homedepot"),
    ("tide-pods-81", 1, date(2026, 10, 20), "walmart"),
]


def _ensure_product(ctx: Ctx, key: str) -> Product:
    product = select_result(ctx, MockAdapter.to_result(BY_KEY[key]).to_payload())
    if not product.listings:
        refresh_product(ctx, product)
    return product


def _paid_on(ctx: Ctx, key: str, day: date, retailer: str) -> float | None:
    p = BY_KEY[key]
    for i, l in enumerate(p.listings):
        if l.retailer == retailer and l.condition == "new":
            for d, price, ship, _stock in series(ctx.config.get("MOCK_SEED", 42), key, i, ctx.today):
                if d == day and price is not None:
                    return round(price + (ship or 0), 2)
    return None


def seed_demo(ctx: Ctx) -> int:
    """Create the demo list. Returns the number of list items created (0 if already seeded)."""
    if ctx.session.scalar(select(Product.id).where(Product.source == "mock").limit(1)):
        return 0
    created = 0
    for key, qty, target, priority, notes in DEMO_LIST:
        product = _ensure_product(ctx, key)
        ctx.session.add(ListItem(product=product, quantity=qty, target_price=target,
                                 priority=priority, notes=notes))
        created += 1
    for key, qty, day, retailer in DEMO_ARCHIVE:
        if day > ctx.today:
            continue
        product = _ensure_product(ctx, key)
        when = datetime.combine(day, time(18, 0), tzinfo=ZoneInfo(ctx.tzname)).astimezone(timezone.utc)
        ctx.session.add(ListItem(product=product, quantity=qty, priority=2, notes="",
                                 purchased=True, purchased_at=when.replace(tzinfo=None),
                                 paid_price=_paid_on(ctx, key, day, retailer),
                                 paid_retailer=retailer_name(retailer, product.brand)))
        created += 1
    ctx.session.commit()
    return created


def remove_demo(session) -> int:
    ids = list(session.scalars(select(Product.id).where(Product.source == "mock")))
    if ids:
        session.execute(delete(ListItem).where(ListItem.product_id.in_(ids)))
        for p in session.scalars(select(Product).where(Product.id.in_(ids))):
            session.delete(p)
    session.commit()
    return len(ids)
