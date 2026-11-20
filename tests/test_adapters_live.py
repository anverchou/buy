"""SerpApi and Keepa adapters, against recorded-shape fixtures (no network)."""
from datetime import date, datetime, timedelta, timezone

import pytest

from app.adapters.base import BudgetExceeded, ParsedQuery
from app.adapters.keepa import KEEPA_EPOCH, KeepaAdapter, daily_from_csv
from app.adapters.serpapi import SerpApiAdapter, parse_title
from app.models import Listing, Product
from app.retailers import normalize_store, parse_shipping

from .conftest import FakeHTTP

TODAY = date(2026, 11, 19)

SHOPPING = {"shopping_results": [
    {"position": 1, "title": "Ninja AF161 Max XL Air Fryer, 5.5 Qt, Gray", "product_id": "111",
     "immersive_product_page_token": "tok111", "source": "Walmart", "price": "$99.00", "extracted_price": 99.0,
     "thumbnail": "https://example.test/a.jpg"},
    {"position": 2, "title": "Ninja AF101 Air Fryer 4 Qt Black", "product_id": "222",
     "immersive_product_page_token": "tok222", "source": "Amazon.com", "price": "$69.99", "extracted_price": 69.99},
    {"position": 3, "title": "Ninja AF161 Max XL Air Fryer, 5.5 Qt, Gray", "product_id": "111",
     "immersive_product_page_token": "tok111b", "source": "Target", "extracted_price": 109.99},
]}
IMMERSIVE = {"product_results": {"title": "Ninja AF161", "brand": "Ninja", "stores": [
    {"name": "Walmart", "link": "https://www.walmart.com/ip/123", "price": "$99.00", "extracted_price": 99.0,
     "details_and_offers": ["Free delivery by Sat", "In stock"]},
    {"name": "Walmart - SuperDeals LLC", "link": "https://www.walmart.com/ip/123?s=2", "extracted_price": 95.0,
     "details_and_offers": ["$7.99 delivery"]},
    {"name": "Amazon.com", "link": "https://www.google.com/url?q=https://www.amazon.com/dp/B07FDJMC9Q%3Fth%3D1",
     "extracted_price": 104.5, "shipping": "Free"},
    {"name": "Best Buy", "title": "Ninja AF161 - Refurbished", "extracted_price": 79.99, "details_and_offers": ["Free shipping"]},
    {"name": "Target", "extracted_price": 109.99, "details_and_offers": ["Out of stock online"]},
    {"name": "Ninja Kitchen", "extracted_price": 129.99, "shipping_extracted": 0},
    {"name": "Some Small Shop", "extracted_price": 60.0},
]}}
GOOGLE_PRODUCT = {"product_results": {"stores": IMMERSIVE["product_results"]["stores"], "price_insights": {
    "stores": [{"name": "Walmart", "prices": [{"date": "2026-11-01", "price": 119.0},
                                              {"date": "Nov 10, 2026", "price": "$109.00"},
                                              {"date": "2025-01-01", "price": 1.0}]}]}}}


def serp_handler(url, params):
    engine = params.get("engine")
    if engine == "google_shopping":
        return SHOPPING, 200
    if engine == "google_immersive_product":
        return IMMERSIVE, 200
    if engine == "google_product":
        return GOOGLE_PRODUCT, 200
    return {"error": "bad engine"}, 400


class Usage:
    def __init__(self):
        self.n = {}

    def used(self, s):
        return self.n.get(s, 0)

    def add(self, s, k=1):
        self.n[s] = self.n.get(s, 0) + k


def make_product(**kw):
    p = Product(id=1, name="Ninja AF161", brand="Ninja", source="serpapi", source_ref="tok111",
                google_product_id="111", **kw)
    return p


def test_store_normalization_and_shipping():
    assert normalize_store("Amazon.com - Seller", "Ninja") == ("amazon", "Seller")
    assert normalize_store("The Home Depot", None)[0] == "homedepot"
    assert normalize_store("Ninja Kitchen", "Ninja")[0] == "manufacturer"
    assert normalize_store("SharkNinja", "Ninja")[0] == "manufacturer"
    assert normalize_store("eBay", "Ninja") == (None, None)
    assert parse_shipping("Free delivery") == 0.0 and parse_shipping("$5.99 shipping") == 5.99
    assert parse_shipping("Get it by Friday") is None


def test_parse_title_keeps_variant():
    t = parse_title("Ninja AF161 Max XL Air Fryer, 5.5 Qt, Gray")
    assert t["model"] == "AF161" and "5.5 Qt" in t["variant"] and "Gray" in t["variant"]


def test_serpapi_search_dedupes_by_product_id():
    http = FakeHTTP(serp_handler)
    a = SerpApiAdapter("key", usage=Usage(), session=http, retry_delay=0)
    results = a.search(ParsedQuery(raw="ninja", text="ninja air fryer"))
    assert [r.google_product_id for r in results] == ["111", "222"]
    assert results[0].source_ref == "tok111" and results[0].price_hint == 99.0
    assert http.calls[0][1]["gl"] == "us"


def test_serpapi_offers():
    a = SerpApiAdapter("key", usage=Usage(), session=FakeHTTP(serp_handler), retry_delay=0)
    offers = {(o.retailer, o.condition): o for o in a.get_current_prices(make_product())}
    assert set(offers) == {("walmart", "new"), ("amazon", "new"), ("bestbuy", "renewed"),
                           ("target", "new"), ("manufacturer", "new")}
    assert offers[("walmart", "new")].price == 99.0 and offers[("walmart", "new")].shipping == 0.0
    assert offers[("amazon", "new")].external_id == "B07FDJMC9Q"
    assert offers[("target", "new")].in_stock is False


def test_serpapi_history_from_price_insights():
    a = SerpApiAdapter("key", usage=Usage(), session=FakeHTTP(serp_handler), retry_delay=0,
                       today_fn=lambda: TODAY)
    listing = Listing(retailer="walmart", condition="new", source="serpapi")
    listing.product = make_product()
    pts = a.get_history(listing, TODAY - timedelta(days=400))
    assert [(p.day, p.price) for p in pts] == [(date(2026, 11, 1), 119.0), (date(2026, 11, 10), 109.0)]


def test_serpapi_budget_guard():
    usage = Usage()
    usage.add("serpapi", 250)
    a = SerpApiAdapter("key", usage=usage, monthly_limit=250, session=FakeHTTP(serp_handler))
    with pytest.raises(BudgetExceeded):
        a.search(ParsedQuery(raw="x", text="x"))


def kt(dt: datetime) -> int:
    return int((dt - KEEPA_EPOCH).total_seconds() // 60)


def test_keepa_csv_to_daily():
    d0 = datetime(2026, 11, 10, 20, tzinfo=timezone.utc)   # Nov 10, 12:00 Pacific
    arr = [kt(d0), 9999, kt(d0 + timedelta(days=3)), -1, kt(d0 + timedelta(days=5)), 8999]
    pts = daily_from_csv(arr, date(2026, 11, 9), TODAY, "America/Los_Angeles")
    assert pts[0].day == date(2026, 11, 10) and pts[0].price == 99.99
    by_day = {p.day: p for p in pts}
    assert by_day[date(2026, 11, 12)].price == 99.99           # carried forward
    assert by_day[date(2026, 11, 13)].in_stock is False and by_day[date(2026, 11, 13)].price is None
    assert by_day[TODAY].price == 89.99


def test_keepa_adapter_offers_and_history():
    d0 = datetime(2026, 10, 1, 20, tzinfo=timezone.utc)
    csv = [None] * 7
    csv[0] = [kt(d0), 12999, kt(d0 + timedelta(days=30)), 10999]
    csv[2] = [kt(d0), 8000]
    product_json = {"asin": "B07FDJMC9Q", "title": "Ninja AF161", "brand": "Ninja", "model": "AF161",
                    "upcList": ["622356000000"], "imagesCSV": "abc.jpg,def.jpg", "csv": csv}
    http = FakeHTTP(lambda url, params: ({"products": [product_json], "tokensLeft": 1199, "tokensConsumed": 1}, 200))
    k = KeepaAdapter("key", usage=Usage(), session=http, retry_delay=0, today_fn=lambda: TODAY)
    r = k.search(ParsedQuery(raw="B07FDJMC9Q", kind="asin", asin="B07FDJMC9Q"))[0]
    assert r.asin == "B07FDJMC9Q" and r.image_url.endswith("/abc.jpg") and r.upc == "622356000000"
    p = Product(id=2, name="x", brand="Ninja", source="keepa", asin="B07FDJMC9Q")
    offers = {(o.retailer, o.condition): o for o in k.get_current_prices(p)}
    assert offers[("amazon", "new")].price == 109.99 and offers[("amazon", "used")].price == 80.0
    listing = Listing(retailer="amazon", condition="new", source="keepa")
    listing.product = p
    hist = k.get_history(listing, date(2026, 9, 1))
    assert hist[0].day == date(2026, 10, 1) and hist[-1].day == TODAY and hist[-1].price == 109.99
    assert len(http.calls) == 1                                 # one token: cached per product


def test_network_errors_never_expose_the_api_key():
    import requests

    class Boom:
        def get(self, url, params=None, timeout=None):
            raise requests.ConnectionError(f"failed {url}?api_key={params.get('api_key') or params.get('key')}")

    from app.adapters.base import SourceError
    s = SerpApiAdapter("SECRET123", usage=Usage(), session=Boom(), retry_delay=0)
    k = KeepaAdapter("SECRET456", usage=Usage(), session=Boom(), retry_delay=0)
    for call in (lambda: s.search(ParsedQuery(raw="x", text="x")),
                 lambda: k.search(ParsedQuery(raw="B0X", kind="asin", asin="B07FDJMC9Q"))):
        with pytest.raises(SourceError) as err:
            call()
        assert "SECRET" not in str(err.value) and "could not connect" in str(err.value)
