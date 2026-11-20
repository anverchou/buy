"""Retailer catalogue, store-name normalization and small parsing helpers."""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Retailer:
    key: str
    name: str
    slot: int                 # fixed chart color slot (color follows the retailer, never its rank)
    core: bool = False        # always shown, even when no listing was found
    membership: bool = False  # requires a paid membership
    search_url: str = ""      # used for demo links and "find it" links


RETAILERS: dict[str, Retailer] = {
    "amazon": Retailer("amazon", "Amazon", 1, core=True,
                       search_url="https://www.amazon.com/s?k={q}"),
    "manufacturer": Retailer("manufacturer", "Manufacturer store", 2, core=True),
    "costco": Retailer("costco", "Costco", 3, core=True, membership=True,
                       search_url="https://www.costco.com/s?keyword={q}"),
    "walmart": Retailer("walmart", "Walmart", 4, search_url="https://www.walmart.com/search?q={q}"),
    "target": Retailer("target", "Target", 5, search_url="https://www.target.com/s?searchTerm={q}"),
    "bestbuy": Retailer("bestbuy", "Best Buy", 6, search_url="https://www.bestbuy.com/site/searchpage.jsp?st={q}"),
    "homedepot": Retailer("homedepot", "Home Depot", 7, search_url="https://www.homedepot.com/s/{q}"),
}
RETAILER_ORDER = list(RETAILERS)

# Brand -> official store names that differ from the brand itself.
MANUFACTURER_ALIASES = {
    "ninja": ["sharkninja", "ninja kitchen", "ninjakitchen"],
    "sony": ["sony electronics", "sony store"],
    "samsung": ["samsung electronics", "samsung us"],
    "kitchenaid": ["kitchenaid", "kitchen aid"],
    "logitech": ["logitech g", "logi"],
    "tide": ["procter & gamble", "p&g", "pgshop"],
}

CONDITION_LABELS = {"new": "New", "renewed": "Renewed", "used": "Used"}


def _norm(text: str | None) -> str:
    return re.sub(r"[^a-z0-9& ]+", " ", (text or "").lower()).strip()


def normalize_store(store_name: str | None, brand: str | None = None) -> tuple[str | None, str | None]:
    """Map a store name from a shopping API to (retailer_key, seller_note).

    Returns (None, None) for stores we don't track.
    """
    raw = (store_name or "").strip()
    n = _norm(raw)
    if not n:
        return None, None
    seller = None
    if " - " in raw:  # "Walmart - SellerName" marketplace offers
        seller = raw.split(" - ", 1)[1].strip() or None
    checks = [
        ("amazon", r"^amazon\b"),
        ("walmart", r"^walmart\b"),
        ("target", r"^target\b"),
        ("bestbuy", r"^best ?buy\b"),
        ("homedepot", r"^(the )?home ?depot\b"),
        ("costco", r"^costco\b"),
    ]
    for key, pattern in checks:
        if re.search(pattern, n):
            return key, seller
    b = _norm(brand)
    if b:
        names = [b, b.replace(" ", "")] + MANUFACTURER_ALIASES.get(b, [])
        compact = n.replace(" ", "")
        for candidate in names:
            c = candidate.replace(" ", "")
            if c and (compact == c or compact.startswith(c) or n.startswith(candidate)):
                return "manufacturer", None
    return None, None


def retailer_name(key: str, brand: str | None = None) -> str:
    if key == "manufacturer":
        return f"{brand} (manufacturer)" if brand else "Manufacturer store"
    r = RETAILERS.get(key)
    return r.name if r else key.title()


def detect_condition(*texts: str | None) -> str:
    blob = " ".join(t for t in texts if t).lower()
    if re.search(r"\b(renewed|refurbished|refurb|reconditioned|remanufactured)\b", blob):
        return "renewed"
    if re.search(r"\b(used|pre-?owned|open[- ]box|second[- ]hand)\b", blob):
        return "used"
    return "new"


_MONEY = re.compile(r"\$\s?([0-9][0-9,]*(?:\.[0-9]{1,2})?)")


def parse_money(value) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    m = _MONEY.search(str(value))
    if m:
        return float(m.group(1).replace(",", ""))
    try:
        return float(str(value).replace(",", "").strip())
    except ValueError:
        return None


def parse_shipping(*texts) -> float | None:
    """Return shipping cost from free-text delivery descriptions; 0.0 for free, None if unknown."""
    for t in texts:
        if t is None:
            continue
        if isinstance(t, (int, float)):
            return float(t)
        s = str(t).lower()
        if not any(w in s for w in ("ship", "deliver", "free")):
            continue
        if "free" in s:
            return 0.0
        amount = parse_money(s)
        if amount is not None:
            return amount
    return None


def detect_in_stock(*texts) -> bool:
    blob = " ".join(str(t) for t in texts if t).lower()
    return not re.search(r"out of stock|sold out|unavailable|not available", blob)


def asin_from_url(url: str | None) -> str | None:
    if not url:
        return None
    from urllib.parse import parse_qs, unquote, urlparse
    u = unquote(url)
    parsed = urlparse(u)
    if "google." in parsed.netloc:  # google redirect: /url?q=<target>
        qs = parse_qs(parsed.query)
        for key in ("q", "url", "adurl"):
            if qs.get(key):
                u = unquote(qs[key][0])
                break
    m = re.search(r"/(?:dp|gp/product|gp/aw/d|product)/([A-Z0-9]{10})(?:[/?]|$)", u)
    return m.group(1) if m else None


def search_link(retailer_key: str, query: str) -> str | None:
    from urllib.parse import quote_plus
    r = RETAILERS.get(retailer_key)
    if not r or not r.search_url:
        return None
    return r.search_url.format(q=quote_plus(query))
