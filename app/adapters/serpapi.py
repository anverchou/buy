"""SerpApi adapter: Google Shopping search + per-store offers.

Calls used (each successful call is one SerpApi search credit):
  * engine=google_shopping            - search results (each carries a product_id and a page token)
  * engine=google_immersive_product   - store offers for a product (more_stores=true)
  * engine=google_product             - fallback by stable product_id; also returns
                                        price_insights with per-store price history, used
                                        once per product to backfill non-Amazon history

Google Shopping only covers sellers who list there, so some retailers (often Costco or
smaller manufacturer stores) will be missing. Those rows can be filled in manually.
"""
from __future__ import annotations

import re
import time
from datetime import date, datetime, timezone

import requests

from ..retailers import (RETAILERS, asin_from_url, detect_condition, detect_in_stock,
                         normalize_store, parse_money, parse_shipping)
from .base import (BudgetExceeded, HistoryPoint, Offer, ParsedQuery, PriceSource, ProductResult,
                   SourceError, SourceStatus)

BASE_URL = "https://serpapi.com/search.json"

COLORS = ("black", "white", "gray", "grey", "silver", "red", "blue", "green", "graphite", "navy",
          "pink", "gold", "rose", "beige", "midnight", "starlight", "stainless")
VARIANT_RE = re.compile(
    r"\b\d+(?:\.\d+)?\s?(?:qt|quart|oz|ounce|lb|lbs|count|ct|pack|pk|gb|tb|inch|in|\"|cup|cups|"
    r"gal|gallon|l|ml|w|watt|ah|v)\b", re.I)
MODEL_RE = re.compile(r"\b(?=[A-Z0-9/-]*\d)(?=[A-Z0-9/-]*[A-Z])[A-Z0-9][A-Z0-9/-]{3,}\b")


def parse_title(title: str) -> dict:
    """Best-effort brand/model/variant from a shopping title. Never merges variants: the
    variant text is kept so two sizes of the same product stay separate rows."""
    out: dict = {"brand": None, "model": None, "variant": None}
    words = title.split()
    if words and words[0][:1].isupper():
        out["brand"] = words[0].strip(",")
    models = [m for m in MODEL_RE.findall(title) if not re.fullmatch(r"\d+[A-Z]?|4K|8K|UHD|HDR|USB-C|LED|OLED|QLED", m)]
    if models:
        out["model"] = max(models, key=len)
    parts = [m.group(0) for m in VARIANT_RE.finditer(title)]
    parts += [c.title() for c in COLORS if re.search(rf"\b{c}\b", title, re.I)]
    if parts:
        out["variant"] = " · ".join(dict.fromkeys(parts))
    return out


def _describe(exc) -> str:
    """Short, key-free description of a network error (request URLs contain the API key)."""
    if isinstance(exc, SourceError):
        return str(exc)
    if isinstance(exc, requests.Timeout):
        return "timed out"
    if isinstance(exc, requests.ConnectionError):
        return "could not connect"
    return type(exc).__name__ if exc else "unknown error"


def parse_insight_date(value, today: date) -> date | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        ts = value / 1000 if value > 10_000_000_000 else value
        return datetime.fromtimestamp(ts, tz=timezone.utc).date()
    s = str(value).strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}", s):
        try:
            return date.fromisoformat(s[:10])
        except ValueError:
            return None
    for fmt in ("%b %d, %Y", "%B %d, %Y", "%m/%d/%Y", "%a, %b %d, %Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    for fmt in ("%b %d", "%B %d", "%a, %b %d"):
        try:
            d = datetime.strptime(f"{s} {today.year}", f"{fmt} %Y").date()
            return d if d <= today else d.replace(year=today.year - 1)
        except ValueError:
            continue
    return None


class SerpApiAdapter(PriceSource):
    name = "serpapi"
    label = "Google Shopping (SerpApi)"
    provides_history = True

    def __init__(self, api_key: str, usage=None, monthly_limit: int = 250, session=None,
                 retry_delay: float = 2.0, today_fn=None):
        self.api_key = api_key
        self.usage = usage                    # services.usage.UsageCounter
        self.monthly_limit = monthly_limit
        self.http = session or requests.Session()
        self.retry_delay = retry_delay
        self._today = today_fn or date.today
        self._cache: dict = {}

    # -- HTTP ----------------------------------------------------------------
    def _call(self, params: dict) -> dict:
        if not self.api_key:
            raise SourceError("SERPAPI_API_KEY is not set")
        if self.usage and self.usage.used(self.name) >= self.monthly_limit:
            raise BudgetExceeded(
                f"SerpApi monthly budget reached ({self.monthly_limit} searches). "
                "Raise SERPAPI_MONTHLY_LIMIT if your plan allows more.")
        full = {"gl": "us", "hl": "en", **params, "api_key": self.api_key}
        last_error = None
        for attempt in range(2):
            try:
                resp = self.http.get(BASE_URL, params=full, timeout=40)
            except requests.RequestException as exc:  # network trouble: retry once
                last_error = exc
                time.sleep(self.retry_delay * (attempt + 1))
                continue
            try:
                data = resp.json()
            except ValueError:
                data = {}
            if resp.status_code == 429 or resp.status_code >= 500:
                last_error = SourceError(data.get("error") or f"SerpApi HTTP {resp.status_code}")
                time.sleep(self.retry_delay * (attempt + 1))
                continue
            if resp.status_code != 200 or data.get("error"):
                msg = data.get("error") or f"HTTP {resp.status_code}"
                # "hasn't returned any results" is a normal empty result, not a failure
                if "any results" in str(msg).lower():
                    if self.usage:
                        self.usage.add(self.name, 1)
                    return {}
                raise SourceError(f"SerpApi: {msg}")
            if self.usage:
                self.usage.add(self.name, 1)
            return data
        raise SourceError(f"SerpApi unavailable ({_describe(last_error)}) - the next run will retry")

    # -- search --------------------------------------------------------------
    def search(self, query: ParsedQuery) -> list[ProductResult]:
        q = query.upc or query.text or query.asin
        if not q:
            return []
        data = self._call({"engine": "google_shopping", "q": q})
        results, seen = [], set()
        for item in data.get("shopping_results", []) or []:
            pid = item.get("product_id")
            token = item.get("immersive_product_page_token")
            ident = pid or token
            if not ident or ident in seen:
                continue
            seen.add(ident)
            title = item.get("title") or "Untitled product"
            guess = parse_title(title)
            store_link = item.get("product_link") or item.get("link")
            results.append(ProductResult(
                source=self.name, source_ref=token or "", name=title,
                brand=guess["brand"], model=guess["model"], variant=guess["variant"],
                upc=query.upc, image_url=item.get("thumbnail") or item.get("serpapi_thumbnail"),
                asin=asin_from_url(store_link) or None, google_product_id=pid,
                price_hint=item.get("extracted_price") or parse_money(item.get("price")),
                store_hint=item.get("source")))
        return results

    # -- current prices ------------------------------------------------------
    def handles(self, product) -> bool:
        return bool(self.api_key) and product.source != "mock"

    def _product_page(self, product) -> dict:
        """Store offers for a product, trying the cheapest stable route first."""
        key = ("page", product.id)
        if key in self._cache:
            return self._cache[key]
        errors = []
        if product.source_ref and product.source == "serpapi":
            try:
                data = self._call({"engine": "google_immersive_product",
                                   "page_token": product.source_ref, "more_stores": "true"})
                if data.get("product_results", {}).get("stores"):
                    self._cache[key] = data
                    return data
            except BudgetExceeded:
                raise
            except SourceError as exc:
                errors.append(str(exc))
        if product.google_product_id:
            data = self._google_product(product)
            if data.get("product_results", {}).get("stores"):
                self._cache[key] = data
                return data
        # Last resort: search again by name/model and take the matching product.
        q = " ".join(x for x in (product.brand, product.model or product.name, product.variant) if x)
        found = self._call({"engine": "google_shopping", "q": q}).get("shopping_results", []) or []
        pick = next((i for i in found if product.google_product_id
                     and i.get("product_id") == product.google_product_id), None) or (found[0] if found else None)
        if pick and pick.get("immersive_product_page_token"):
            product.source_ref = pick["immersive_product_page_token"]
            product.google_product_id = product.google_product_id or pick.get("product_id")
            data = self._call({"engine": "google_immersive_product",
                               "page_token": product.source_ref, "more_stores": "true"})
            self._cache[key] = data
            return data
        raise SourceError("SerpApi: no store offers found" + (f" ({'; '.join(errors)})" if errors else ""))

    def _google_product(self, product) -> dict:
        key = ("gp", product.id)
        if key not in self._cache:
            self._cache[key] = self._call({"engine": "google_product",
                                           "product_id": product.google_product_id})
        return self._cache[key]

    def offer_from_store(self, store: dict, brand: str | None) -> Offer | None:
        retailer, seller = normalize_store(store.get("name"), brand)
        if retailer is None:
            return None
        price = store.get("extracted_price")
        if price is None:
            price = parse_money(store.get("price"))
        details = store.get("details_and_offers") or []
        if isinstance(details, str):
            details = [details]
        shipping = store.get("shipping_extracted")
        if shipping is None:
            shipping = parse_shipping(store.get("shipping"), *details)
        link = store.get("link")
        return Offer(
            retailer=retailer, price=float(price) if price is not None else None,
            shipping=float(shipping) if shipping is not None else None,
            in_stock=detect_in_stock(*details, store.get("tag")),
            condition=detect_condition(store.get("title"), store.get("tag"), *details),
            url=link, requires_membership=RETAILERS[retailer].membership,
            external_id=asin_from_url(link) if retailer == "amazon" else None,
            seller=seller, source=self.name)

    def get_current_prices(self, product) -> list[Offer]:
        data = self._product_page(product)
        pr = data.get("product_results", {}) or {}
        if pr.get("brand") and not product.brand:
            product.brand = pr["brand"]
        offers, seen = [], set()
        for store in pr.get("stores", []) or []:
            offer = self.offer_from_store(store, product.brand)
            if offer is None or offer.price is None:
                continue
            k = (offer.retailer, offer.condition)
            if k in seen:   # keep the first (Google lists the best offer per store first)
                continue
            seen.add(k)
            offers.append(offer)
        return offers

    # -- history (Google price insights, when available) ----------------------
    def get_history(self, listing, since: date) -> list[HistoryPoint]:
        product = listing.product
        if not product.google_product_id:
            return []
        data = self._google_product(product)
        insights = (data.get("product_results", {}) or {}).get("price_insights") or {}
        today = self._today()
        points: dict[date, float] = {}
        for store in insights.get("stores", []) or []:
            retailer, _ = normalize_store(store.get("name"), product.brand)
            if retailer != listing.retailer or listing.condition != "new":
                continue
            for entry in store.get("prices", []) or []:
                d = parse_insight_date(entry.get("date"), today)
                price = parse_money(entry.get("price") if entry.get("extracted_price") is None
                                    else entry.get("extracted_price"))
                if d and price is not None and since <= d <= today:
                    points[d] = price
        return [HistoryPoint(d, p, None, True) for d, p in sorted(points.items())]

    def status(self) -> SourceStatus:
        used = self.usage.used(self.name) if self.usage else 0
        return SourceStatus(self.name, self.label, bool(self.api_key),
                            f"{used} of {self.monthly_limit} searches used this month",
                            {"used": used, "limit": self.monthly_limit})
