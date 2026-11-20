"""Keepa adapter: Amazon product lookup, current price and full price history.

One product request costs 1 token per ASIN (history included). Text search costs
10 tokens per page, so it is only used when SerpApi is not configured.

Keepa price arrays ("csv") are flat lists [keepaTime, value, keepaTime, value, ...]
where keepaTime is minutes since 2011-01-01 UTC and value is in cents (-1 = no offer).
"""
from __future__ import annotations

import time
from datetime import date, datetime, timedelta, timezone

import requests

from ..timeutil import local_day
from .base import HistoryPoint, Offer, ParsedQuery, PriceSource, ProductResult, SourceError, SourceStatus

BASE_URL = "https://api.keepa.com"
KEEPA_EPOCH = datetime(2011, 1, 1, tzinfo=timezone.utc)

# csv indices (Keepa "CsvType")
AMAZON, NEW, USED, REFURBISHED = 0, 1, 2, 6
CONDITION_SERIES = {"new": (AMAZON, NEW), "used": (USED,), "renewed": (REFURBISHED,)}


def keepa_time_to_utc(minutes: int) -> datetime:
    return KEEPA_EPOCH + timedelta(minutes=minutes)


def csv_pairs(arr) -> list[tuple[datetime, int]]:
    if not arr:
        return []
    return [(keepa_time_to_utc(arr[i]), arr[i + 1]) for i in range(0, len(arr) - 1, 2)]


def daily_from_csv(arr, since: date, today: date, tzname: str) -> list[HistoryPoint]:
    """Closing price per local day, carried forward between changes."""
    pairs = csv_pairs(arr)
    if not pairs:
        return []
    by_day: dict[date, int] = {}
    for ts, value in pairs:
        by_day[local_day(ts.replace(tzinfo=None), tzname)] = value  # last change that day wins
    first = min(by_day)
    out, current = [], None
    d = first
    while d <= today:
        if d in by_day:
            current = by_day[d]
        if d >= since and current is not None:
            if current < 0:
                out.append(HistoryPoint(d, None, None, False))
            else:
                out.append(HistoryPoint(d, round(current / 100, 2), 0.0, True))
        d += timedelta(days=1)
    return out


def image_url(product: dict) -> str | None:
    images = product.get("images")
    if images and isinstance(images, list):
        first = images[0]
        name = first.get("l") or first.get("m") if isinstance(first, dict) else None
        if name:
            return f"https://m.media-amazon.com/images/I/{name}"
    csv = product.get("imagesCSV")
    if csv:
        return f"https://m.media-amazon.com/images/I/{csv.split(',')[0]}"
    return None


class KeepaAdapter(PriceSource):
    name = "keepa"
    label = "Keepa (Amazon history)"
    provides_history = True

    def __init__(self, api_key: str, domain: int = 1, tzname: str = "America/Los_Angeles",
                 usage=None, allow_text_search: bool = False, session=None, retry_delay: float = 2.0,
                 today_fn=None):
        self.api_key = api_key
        self.domain = domain
        self.tzname = tzname
        self.usage = usage
        self.allow_text_search = allow_text_search
        self.http = session or requests.Session()
        self.retry_delay = retry_delay
        self._today = today_fn or date.today
        self._cache: dict[str, dict] = {}
        self.tokens_left: int | None = None

    def _get(self, path: str, params: dict) -> dict:
        if not self.api_key:
            raise SourceError("KEEPA_API_KEY is not set")
        full = {"key": self.api_key, "domain": self.domain, **params}
        last = None
        for attempt in range(2):
            try:
                resp = self.http.get(f"{BASE_URL}/{path}", params=full, timeout=60)
            except requests.RequestException as exc:
                last = exc
                time.sleep(self.retry_delay * (attempt + 1))
                continue
            try:
                data = resp.json()
            except ValueError:
                data = {}
            if "tokensLeft" in data:
                self.tokens_left = data["tokensLeft"]
            if self.usage and data.get("tokensConsumed"):
                self.usage.add(self.name, int(data["tokensConsumed"]))
            if resp.status_code == 429:
                raise SourceError("Keepa: out of tokens right now - the next run will retry")
            if resp.status_code != 200 or data.get("error"):
                err = data.get("error") or {}
                msg = err.get("message") if isinstance(err, dict) else str(err)
                raise SourceError(f"Keepa: {msg or 'HTTP ' + str(resp.status_code)}")
            return data
        reason = ("timed out" if isinstance(last, requests.Timeout) else
                  "could not connect" if isinstance(last, requests.ConnectionError) else type(last).__name__)
        raise SourceError(f"Keepa unavailable ({reason}) - the next run will retry")

    def _product(self, asin: str | None = None, code: str | None = None) -> dict | None:
        cache_key = asin or f"code:{code}"
        if cache_key in self._cache:
            return self._cache[cache_key]
        params = {"history": 1, "days": 400}
        if asin:
            params["asin"] = asin
        else:
            params["code"] = code
        data = self._get("product", params)
        products = data.get("products") or []
        prod = products[0] if products else None
        if prod and prod.get("asin"):
            self._cache[prod["asin"]] = prod
        self._cache[cache_key] = prod
        return prod

    def to_result(self, p: dict) -> ProductResult:
        upcs = p.get("upcList") or []
        eans = p.get("eanList") or []
        variant = " · ".join(x for x in (p.get("size"), p.get("color")) if x) or None
        return ProductResult(source=self.name, source_ref=p.get("asin", ""), name=p.get("title") or p.get("asin"),
                             brand=p.get("brand"), model=p.get("model") or p.get("partNumber"),
                             variant=variant, upc=(upcs[0] if upcs else (eans[0] if eans else None)),
                             image_url=image_url(p), asin=p.get("asin"))

    def search(self, query: ParsedQuery) -> list[ProductResult]:
        if query.asin:
            p = self._product(asin=query.asin)
            return [self.to_result(p)] if p else []
        if query.upc:
            p = self._product(code=query.upc)
            return [self.to_result(p)] if p else []
        if self.allow_text_search and query.text:
            data = self._get("search", {"type": "product", "term": query.text, "history": 0})
            return [self.to_result(p) for p in (data.get("products") or [])[:10]]
        return []

    def handles(self, product) -> bool:
        return bool(self.api_key) and product.source != "mock" and bool(product.asin)

    @staticmethod
    def _last_value(arr) -> int | None:
        if not arr or len(arr) < 2:
            return None
        return arr[-1]

    def get_current_prices(self, product) -> list[Offer]:
        p = self._product(asin=product.asin)
        if not p:
            return []
        csv = p.get("csv") or []
        url = f"https://www.amazon.com/dp/{product.asin}"
        offers = []
        for condition, indices in CONDITION_SERIES.items():
            for idx in indices:
                arr = csv[idx] if idx < len(csv) else None
                value = self._last_value(arr)
                if value is None:
                    continue
                seller = None if idx == AMAZON else "Marketplace sellers"
                if value < 0:
                    if idx == AMAZON:
                        continue  # Amazon itself out of stock - fall through to marketplace
                    offers.append(Offer("amazon", None, None, False, condition, url,
                                        external_id=product.asin, seller=seller, source=self.name))
                else:
                    offers.append(Offer("amazon", round(value / 100, 2), 0.0 if idx == AMAZON else None,
                                        True, condition, url, external_id=product.asin, seller=seller,
                                        source=self.name))
                break
        return offers

    def get_history(self, listing, since: date) -> list[HistoryPoint]:
        if listing.retailer != "amazon" or not listing.product.asin:
            return []
        p = self._product(asin=listing.product.asin)
        if not p:
            return []
        csv = p.get("csv") or []
        for idx in CONDITION_SERIES.get(listing.condition, ()):
            arr = csv[idx] if idx < len(csv) else None
            points = daily_from_csv(arr, since, self._today(), self.tzname)
            if any(pt.price is not None for pt in points):
                return points
        return []

    def status(self) -> SourceStatus:
        detail = "Amazon price history"
        if self.tokens_left is not None:
            detail += f" · {self.tokens_left} tokens left"
        return SourceStatus(self.name, self.label, bool(self.api_key), detail)
