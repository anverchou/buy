"""Mock adapter: a seeded catalogue with realistic, deterministic price history.

Product names and model numbers are real; prices, UPCs and ASINs are made up.
UPCs use number-system 2 (reserved for in-store items, never a real product code), and
ASINs start with B0DEMO, so nothing here collides with a live lookup.

Price behaviour that is simulated:
  * slow drift (electronics get cheaper as models age, consumables creep up)
  * retail events computed from the calendar: Black Friday (+ early deals), holiday,
    Presidents' Day, Memorial Day, July 4th, Prime Day + competing sales, Labor Day,
    October Prime / Target Circle Week
  * retailer habits: Amazon reprices every few days and runs lightning deals,
    Walmart rollbacks, Costco instant savings, Best Buy weekly deals,
    Home Depot one-day special buys, manufacturer promos
  * out-of-stock runs (Amazon shows no price while out of stock)
  * staggered tracking start dates so partial-coverage labels appear

Series are generated day by day from a fixed anchor date with per-listing RNG streams,
so the value for a given day never changes between runs.
"""
from __future__ import annotations

import bisect
import random
import re
from dataclasses import dataclass
from datetime import date, timedelta
from functools import lru_cache

from ..retailers import RETAILERS, search_link
from .base import HistoryPoint, Offer, ParsedQuery, PriceSource, ProductResult, SourceStatus

START = date(2025, 10, 1)


def _upc(body11: str) -> str:
    digits = [int(c) for c in body11]
    total = sum(digits[0::2]) * 3 + sum(digits[1::2])
    return body11 + str((10 - total % 10) % 10)


@dataclass(frozen=True)
class MockListing:
    retailer: str
    mult: float                 # typical price as a fraction of MSRP
    since: date = START         # first day this listing was "tracked"
    condition: str = "new"
    oos_rate: float = 0.006     # daily chance an out-of-stock run starts
    events: float = 1.0         # how often it joins sale events


@dataclass(frozen=True)
class MockProduct:
    key: str
    name: str
    brand: str
    model: str | None
    variant: str
    upc: str
    asin: str
    msrp: float
    category: str
    keywords: str
    listings: tuple
    drift: float = 0.0          # annual change in regular price


L = MockListing
CATALOG: tuple[MockProduct, ...] = (
    MockProduct("ninja-af101", "Ninja Air Fryer, 4 Qt, Black/Gray", "Ninja", "AF101", "4 qt",
                _upc("20000100101"), "B0DEMO0101", 129.99, "kitchen", "air fryer airfryer",
                (L("amazon", 0.69), L("manufacturer", 0.86), L("walmart", 0.70),
                 L("target", 0.77, since=date(2026, 10, 2)), L("bestbuy", 0.77)), drift=-0.03),
    MockProduct("ninja-af161", "Ninja Air Fryer Max XL, 5.5 Qt, Gray", "Ninja", "AF161", "5.5 qt",
                _upc("20000100161"), "B0DEMO0161", 169.99, "kitchen", "air fryer airfryer max xl",
                (L("amazon", 0.70), L("manufacturer", 0.88),
                 L("costco", 0.66, since=date(2026, 3, 14), oos_rate=0.02),
                 L("walmart", 0.71), L("target", 0.76), L("bestbuy", 0.76)), drift=-0.03),
    MockProduct("kitchenaid-ksm150-red", "KitchenAid Artisan Series 5 Quart Tilt-Head Stand Mixer",
                "KitchenAid", "KSM150PSER", "5 qt · Empire Red", _upc("20000200150"), "B0DEMO0150",
                459.99, "kitchen", "stand mixer kitchen aid red",
                (L("amazon", 0.85), L("manufacturer", 0.98),
                 L("costco", 0.80, since=date(2025, 11, 1), oos_rate=0.015),
                 L("walmart", 0.85), L("target", 0.90), L("bestbuy", 0.90),
                 L("homedepot", 0.92, since=date(2026, 9, 15)))),
    MockProduct("kitchenaid-ksm150-black", "KitchenAid Artisan Series 5 Quart Tilt-Head Stand Mixer",
                "KitchenAid", "KSM150PSOB", "5 qt · Onyx Black", _upc("20000200151"), "B0DEMO0151",
                459.99, "kitchen", "stand mixer kitchen aid black",
                (L("amazon", 0.80), L("manufacturer", 0.98), L("walmart", 0.84),
                 L("target", 0.90), L("bestbuy", 0.90))),
    MockProduct("sony-wh1000xm5", "Sony WH-1000XM5 Wireless Noise Canceling Headphones", "Sony",
                "WH1000XM5/B", "Black", _upc("20000300005"), "B0DEMO0005", 399.99, "audio",
                "headphones noise cancelling canceling over-ear wh-1000xm5 xm5",
                (L("amazon", 0.82), L("amazon", 0.62, condition="renewed", oos_rate=0.02),
                 L("manufacturer", 0.95), L("costco", 0.75, since=date(2025, 11, 19), oos_rate=0.015),
                 L("walmart", 0.83), L("target", 0.88), L("bestbuy", 0.82)), drift=-0.06),
    MockProduct("dewalt-dcd771c2", "DEWALT 20V MAX Cordless Drill/Driver Kit", "DEWALT", "DCD771C2",
                "Kit · 2 × 1.3Ah batteries", _upc("20000400771"), "B0DEMO0771", 179.00, "tools",
                "drill driver cordless 20v",
                (L("amazon", 0.56), L("homedepot", 0.56), L("walmart", 0.57)), drift=-0.02),
    MockProduct("samsung-un65du7200", "Samsung 65\" Class DU7200 Crystal UHD 4K Smart TV", "Samsung",
                "UN65DU7200FXZA", "65 in", _upc("20000565720"), "B0DEMO0065", 529.99, "tv",
                "tv television 4k uhd crystal 65 inch",
                (L("amazon", 0.85), L("manufacturer", 0.86), L("walmart", 0.83),
                 L("target", 0.86), L("bestbuy", 0.85)), drift=-0.12),
    MockProduct("samsung-un55du7200", "Samsung 55\" Class DU7200 Crystal UHD 4K Smart TV", "Samsung",
                "UN55DU7200FXZA", "55 in", _upc("20000555720"), "B0DEMO0055", 429.99, "tv",
                "tv television 4k uhd crystal 55 inch",
                (L("amazon", 0.83), L("manufacturer", 0.86), L("walmart", 0.82),
                 L("target", 0.86), L("bestbuy", 0.84)), drift=-0.10),
    MockProduct("vitamix-5200", "Vitamix 5200 Blender, Professional-Grade", "Vitamix", "5200",
                "64 oz container · Black", _upc("20000605200"), "B0DEMO5200", 499.95, "kitchen",
                "blender",
                (L("amazon", 0.78), L("manufacturer", 0.90),
                 L("costco", 0.70, since=date(2026, 6, 1), oos_rate=0.02),
                 L("walmart", 0.80), L("target", 0.88), L("bestbuy", 0.84),
                 L("homedepot", 0.86, since=date(2026, 8, 10)))),
    MockProduct("tide-pods-81", "Tide PODS Laundry Detergent Pacs, Original Scent", "Tide", None,
                "81 count", _upc("20000700081"), "B0DEMO7081", 25.99, "household",
                "laundry detergent pods pacs",
                (L("amazon", 0.88), L("walmart", 0.84), L("target", 0.88),
                 L("homedepot", 0.93, since=date(2026, 4, 20))), drift=0.04),
    MockProduct("tide-pods-112", "Tide PODS Laundry Detergent Pacs, Original Scent", "Tide", None,
                "112 count", _upc("20000700112"), "B0DEMO7112", 32.99, "household",
                "laundry detergent pods pacs",
                (L("amazon", 0.90), L("walmart", 0.86), L("target", 0.90)), drift=0.04),
    MockProduct("tide-pods-152", "Tide PODS Laundry Detergent Pacs, Original Scent", "Tide", None,
                "152 count", _upc("20000700152"), "B0DEMO7152", 44.99, "household",
                "laundry detergent pods pacs bulk",
                (L("costco", 0.86, oos_rate=0.01), L("amazon", 0.99), L("walmart", 0.96)), drift=0.04),
    MockProduct("logitech-mx-master-3s", "Logitech MX Master 3S Wireless Performance Mouse",
                "Logitech", "910-006557", "Graphite", _upc("20000806557"), "B0DEMO6557", 99.99,
                "electronics", "mouse wireless mx master 3s",
                (L("amazon", 0.85), L("amazon", 0.60, condition="renewed", oos_rate=0.02),
                 L("manufacturer", 1.0), L("costco", 0.80, since=date(2026, 2, 1)),
                 L("walmart", 0.86), L("target", 0.95), L("bestbuy", 0.90))),
    MockProduct("weber-kettle-22", "Weber Original Kettle Charcoal Grill", "Weber", "741001",
                "22 in · Black", _upc("20000974100"), "B0DEMO7410", 159.00, "grill",
                "grill charcoal kettle bbq",
                (L("amazon", 0.92), L("manufacturer", 1.0), L("walmart", 0.92),
                 L("target", 0.95, since=date(2026, 5, 1)), L("homedepot", 0.95)), drift=0.03),
)
BY_KEY = {p.key: p for p in CATALOG}

MANUFACTURER_SITES = {
    "Ninja": "https://www.ninjakitchen.com/",
    "KitchenAid": "https://www.kitchenaid.com/",
    "Sony": "https://electronics.sony.com/",
    "Samsung": "https://www.samsung.com/us/",
    "Vitamix": "https://www.vitamix.com/",
    "Logitech": "https://www.logitech.com/",
    "Weber": "https://www.weber.com/",
}
CATEGORY_LABELS = {"kitchen": "Kitchen", "audio": "Audio", "tv": "TV", "tools": "Tools",
                   "household": "Household", "electronics": "Electronics", "grill": "Outdoor"}

# Sale depth ranges (fraction off the regular price) by category and event type.
DEPTH = {
    "kitchen":     {"bf": (0.22, 0.38), "early": (0.15, 0.28), "prime": (0.20, 0.35), "holiday": (0.08, 0.18), "major": (0.10, 0.20)},
    "audio":       {"bf": (0.20, 0.30), "early": (0.15, 0.25), "prime": (0.18, 0.28), "holiday": (0.08, 0.15), "major": (0.08, 0.15)},
    "tv":          {"bf": (0.15, 0.28), "early": (0.10, 0.20), "prime": (0.10, 0.20), "holiday": (0.05, 0.12), "major": (0.08, 0.15)},
    "tools":       {"bf": (0.25, 0.40), "early": (0.15, 0.30), "prime": (0.15, 0.30), "holiday": (0.10, 0.20), "major": (0.12, 0.25)},
    "household":   {"bf": (0.10, 0.20), "early": (0.08, 0.15), "prime": (0.12, 0.22), "holiday": (0.05, 0.10), "major": (0.05, 0.12)},
    "electronics": {"bf": (0.20, 0.35), "early": (0.15, 0.25), "prime": (0.20, 0.30), "holiday": (0.08, 0.15), "major": (0.08, 0.18)},
    "grill":       {"bf": (0.05, 0.12), "early": (0.05, 0.10), "prime": (0.08, 0.15), "holiday": (0.05, 0.10), "major": (0.12, 0.22)},
}
SHIPPING = {  # retailer -> (free-shipping threshold, fee); None threshold = always free
    "amazon": (35, 6.99), "walmart": (35, 6.99), "target": (35, 5.99), "bestbuy": (35, 5.99),
    "homedepot": (45, 5.99), "costco": (None, 0.0), "manufacturer": (49, 7.95),
}
ALL = frozenset(RETAILERS)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    d = date(year, month, 1)
    d += timedelta(days=(weekday - d.weekday()) % 7)
    return d + timedelta(weeks=n - 1)


def _last_weekday(year: int, month: int, weekday: int) -> date:
    d = date(year, month + 1, 1) - timedelta(days=1)
    return d - timedelta(days=(d.weekday() - weekday) % 7)


@lru_cache(maxsize=None)
def calendar_events(year: int) -> tuple:
    """(name, start, end, retailers, depth key, participation probability)."""
    thanksgiving = _nth_weekday(year, 11, 3, 4)
    prime = _nth_weekday(year, 7, 1, 2)
    oct_prime = _nth_weekday(year, 10, 1, 2)
    presidents = _nth_weekday(year, 2, 0, 3)
    memorial = _last_weekday(year, 5, 0)
    labor = _nth_weekday(year, 9, 0, 1)
    circle_spring = _nth_weekday(year, 4, 6, 1)
    circle_fall = _nth_weekday(year, 10, 6, 1)
    big_box = frozenset({"amazon", "walmart", "target", "bestbuy", "homedepot"})
    return (
        ("presidents", presidents - timedelta(days=4), presidents + timedelta(days=1),
         frozenset({"homedepot", "bestbuy", "walmart", "manufacturer"}), "major", 0.6),
        ("circle-spring", circle_spring, circle_spring + timedelta(days=6), frozenset({"target"}), "major", 0.6),
        ("memorial", memorial - timedelta(days=5), memorial, ALL - {"amazon"}, "major", 0.55),
        ("july4", date(year, 6, 29), date(year, 7, 5),
         frozenset({"homedepot", "walmart", "target", "manufacturer"}), "major", 0.45),
        ("prime", prime, prime + timedelta(days=3), frozenset({"amazon"}), "prime", 0.95),
        ("prime-rivals", prime - timedelta(days=2), prime + timedelta(days=5),
         frozenset({"walmart", "target", "bestbuy"}), "major", 0.7),
        ("labor", labor - timedelta(days=10), labor, ALL, "major", 0.5),
        ("circle-fall", circle_fall, circle_fall + timedelta(days=6),
         frozenset({"target", "walmart", "bestbuy"}), "major", 0.55),
        ("oct-prime", oct_prime, oct_prime + timedelta(days=1), frozenset({"amazon"}), "prime", 0.8),
        ("early-bf", thanksgiving - timedelta(days=13), thanksgiving - timedelta(days=7), big_box, "early", 0.75),
        ("bf", thanksgiving - timedelta(days=6), thanksgiving + timedelta(days=4), ALL, "bf", 0.92),
        ("holiday", date(year, 12, 8), date(year, 12, 22), ALL, "holiday", 0.5),
    )


def _windows(seed: int, p: MockProduct, l: MockListing) -> list[tuple[date, date, float]]:
    """Sale windows (start, end, pct off) for one listing: calendar events + retailer promos."""
    out = []
    for year in range(START.year, START.year + 4):
        for name, s, e, who, depth, prob in calendar_events(year):
            if l.retailer not in who:
                continue
            r = random.Random(f"{seed}|{p.key}|{l.retailer}|{l.condition}|{name}|{year}")
            if r.random() >= prob * l.events:
                continue
            lo, hi = DEPTH[p.category][depth]
            out.append((s + timedelta(days=r.randint(0, 1)), e - timedelta(days=r.randint(0, 1)),
                        r.uniform(lo, hi)))
    habits = {  # retailer -> (gap days, length days, pct range)
        "walmart": ((50, 90), (10, 21), (0.05, 0.12)),
        "costco": ((35, 60), (14, 25), (0.10, 0.22)),
        "bestbuy": ((45, 80), (3, 7), (0.08, 0.15)),
        "target": ((60, 100), (5, 8), (0.05, 0.12)),
        "homedepot": ((30, 60), (1, 1), (0.15, 0.25)),
        "manufacturer": ((90, 150), (5, 10), (0.08, 0.15)),
    }
    if l.retailer in habits:
        (g1, g2), (n1, n2), (lo, hi) = habits[l.retailer]
        r = random.Random(f"{seed}|{p.key}|{l.retailer}|{l.condition}|promos")
        d = START + timedelta(days=r.randint(5, 40))
        end_all = START + timedelta(days=365 * 4)
        while d < end_all:
            length = r.randint(n1, n2)
            out.append((d, d + timedelta(days=length - 1), r.uniform(lo, hi)))
            d += timedelta(days=r.randint(g1, g2))
    return out


def _steps(seed: int, p: MockProduct) -> tuple[list[date], list[float]]:
    r = random.Random(f"{seed}|{p.key}|steps")
    days, factors = [START], [1.0]
    d, f = START, 1.0
    while d < START + timedelta(days=365 * 4):
        gap = r.randint(70, 130)
        d += timedelta(days=gap)
        f *= 1 + p.drift * gap / 365 + r.uniform(-0.015, 0.015)
        days.append(d)
        factors.append(f)
    return days, factors


def retail_round(x: float, retailer: str) -> float:
    if x < 5:
        return round(x, 2)
    if retailer == "walmart":
        return round(round(x) - 0.03, 2)
    if retailer == "homedepot" and x >= 50:
        return float(round(x))
    return round(round(x) - 0.01, 2)


def shipping_for(retailer: str, price: float | None, category: str) -> float:
    threshold, fee = SHIPPING.get(retailer, (35, 5.99))
    if threshold is None or price is None or price >= threshold or category == "tv":
        return 0.0
    return fee


@lru_cache(maxsize=1024)
def series(seed: int, key: str, listing_index: int, end: date) -> tuple:
    """Daily (day, price|None, shipping, in_stock) from START through `end`."""
    p = BY_KEY[key]
    l = p.listings[listing_index]
    rng = random.Random(f"{seed}|{p.key}|{l.retailer}|{l.condition}|daily")
    windows = _windows(seed, p, l)
    step_days, step_factors = _steps(seed, p)
    out = []
    d = START
    noise, next_reprice = 0.0, START
    lightning_left, lightning_pct = 0, 0.0
    oos_left = 0
    while d <= end:
        factor = step_factors[bisect.bisect_right(step_days, d) - 1]
        regular = p.msrp * l.mult * factor
        disc = max((pct for s, e, pct in windows if s <= d <= e), default=0.0)
        if l.retailer == "amazon":
            if d >= next_reprice:
                noise = rng.uniform(-0.06, 0.02)
                next_reprice = d + timedelta(days=rng.randint(2, 6))
            if lightning_left == 0 and rng.random() < 0.012:
                lightning_left, lightning_pct = rng.randint(1, 2), rng.uniform(0.08, 0.15)
            if lightning_left:
                disc = max(disc, lightning_pct)
                lightning_left -= 1
        price = retail_round(regular * (1 + noise) * (1 - disc), l.retailer)
        if oos_left > 0:
            oos_left -= 1
            in_stock = False
        elif rng.random() < l.oos_rate:
            oos_left = rng.randint(1, 8)
            in_stock = False
        else:
            in_stock = True
        shown = None if (not in_stock and l.retailer == "amazon") else price
        out.append((d, shown, shipping_for(l.retailer, price, p.category), in_stock))
        d += timedelta(days=1)
    return tuple(out)


def _tokens(text: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9.]+", text.lower()) if len(t) >= 2 or t.isdigit()]


class MockAdapter(PriceSource):
    name = "mock"
    label = "Demo data (mock)"
    provides_history = True

    def __init__(self, seed: int = 42, today_fn=None):
        self.seed = seed
        self._today_fn = today_fn or date.today

    # -- helpers -------------------------------------------------------------
    def today(self) -> date:
        return self._today_fn()

    @staticmethod
    def _haystack(p: MockProduct) -> str:
        model_compact = re.sub(r"[^a-z0-9]", "", (p.model or "").lower())
        return " ".join([p.name, p.brand, p.model or "", model_compact, p.variant, p.upc, p.asin,
                         p.keywords, CATEGORY_LABELS.get(p.category, "")]).lower()

    @staticmethod
    def to_result(p: MockProduct) -> ProductResult:
        return ProductResult(source="mock", source_ref=p.key, name=p.name, brand=p.brand,
                             model=p.model, variant=p.variant, upc=p.upc,
                             image_url=f"/img/demo/{p.key}.svg", asin=p.asin)

    def _product_for(self, product) -> MockProduct | None:
        return BY_KEY.get(product.source_ref or "")

    # -- interface -----------------------------------------------------------
    def search(self, query: ParsedQuery) -> list[ProductResult]:
        if query.upc:
            return [self.to_result(p) for p in CATALOG if p.upc == query.upc]
        if query.asin:
            hits = [self.to_result(p) for p in CATALOG if p.asin == query.asin]
            if hits or not query.text:
                return hits
        tokens = _tokens(query.text)
        if not tokens:
            return []
        scored = []
        for p in CATALOG:
            hay = self._haystack(p)
            matched = sum(1 for t in tokens if t in hay)
            if query.kind == "url":
                ok = matched >= min(2, len(tokens))
            else:
                ok = matched / len(tokens) >= 0.6
            if ok:
                scored.append((matched / len(tokens), p))
        scored.sort(key=lambda x: (-x[0], x[1].name, x[1].variant))
        return [self.to_result(p) for _, p in scored]

    def handles(self, product) -> bool:
        return product.source == "mock" and self._product_for(product) is not None

    def _url(self, p: MockProduct, retailer: str) -> str | None:
        if retailer == "manufacturer":
            return MANUFACTURER_SITES.get(p.brand)
        if retailer == "amazon":
            return search_link("amazon", f"{p.brand} {p.model or p.name} {p.variant}")
        return search_link(retailer, f"{p.brand} {p.model or p.name}")

    def get_current_prices(self, product) -> list[Offer]:
        p = self._product_for(product)
        if not p:
            return []
        today = self.today()
        offers = []
        for i, l in enumerate(p.listings):
            if l.since > today:
                continue
            day, price, shipping, in_stock = series(self.seed, p.key, i, today)[-1]
            offers.append(Offer(retailer=l.retailer, price=price, shipping=shipping, in_stock=in_stock,
                                condition=l.condition, url=self._url(p, l.retailer),
                                requires_membership=RETAILERS[l.retailer].membership,
                                external_id=p.asin if l.retailer == "amazon" else None,
                                source=self.name))
        return offers

    def get_history(self, listing, since: date) -> list[HistoryPoint]:
        p = self._product_for(listing.product)
        if not p:
            return []
        today = self.today()
        for i, l in enumerate(p.listings):
            if l.retailer == listing.retailer and l.condition == listing.condition:
                first = max(since, l.since)
                return [HistoryPoint(d, price, ship, stock)
                        for d, price, ship, stock in series(self.seed, p.key, i, today) if d >= first]
        return []

    def status(self) -> SourceStatus:
        return SourceStatus(self.name, self.label, True,
                            f"{len(CATALOG)} demo products with history since {START:%b %d, %Y}")


def placeholder_svg(key: str) -> str | None:
    """Simple labelled tile used as the demo product image."""
    p = BY_KEY.get(key)
    if not p:
        return None
    hues = {"kitchen": "#e8eef8", "audio": "#efe9f7", "tv": "#e6f2ee", "tools": "#fbefdc",
            "household": "#e7f1f8", "electronics": "#eeeeec", "grill": "#f6e9e4"}
    bg = hues.get(p.category, "#eeeeec")
    initials = "".join(w[0] for w in re.findall(r"[A-Za-z]+", p.brand)[:2]).upper()
    if len(initials) < 2:
        initials = p.brand[:2].upper()
    label = CATEGORY_LABELS.get(p.category, "")
    from html import escape
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 120" role="img" '
        f'aria-label="{escape(p.brand)} {escape(label)}">'
        f'<rect width="120" height="120" rx="16" fill="{bg}"/>'
        '<text x="60" y="64" text-anchor="middle" font-family="system-ui,-apple-system,Segoe UI,sans-serif" '
        f'font-size="36" font-weight="650" fill="#3a3a37">{escape(initials)}</text>'
        '<text x="60" y="90" text-anchor="middle" font-family="system-ui,-apple-system,Segoe UI,sans-serif" '
        f'font-size="12" fill="#6b6a65" letter-spacing="0.5">{escape(label.upper())}</text>'
        '</svg>'
    )
