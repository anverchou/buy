from datetime import date

from app.adapters.mock import BY_KEY, CATALOG, START, MockAdapter, series
from app.query import parse_query

TODAY = date(2026, 11, 19)


def test_series_is_deterministic_and_stable_over_time():
    a = series(42, "sony-wh1000xm5", 0, TODAY)
    series.cache_clear()
    b = series(42, "sony-wh1000xm5", 0, date(2026, 12, 31))
    assert a == b[: len(a)]                       # extending the end never rewrites the past
    assert a[0][0] == START and a[-1][0] == TODAY
    assert (TODAY - START).days >= 365            # at least 12 months


def test_prices_are_plausible():
    for p in CATALOG:
        for i, _ in enumerate(p.listings):
            prices = [x[1] for x in series(42, p.key, i, TODAY) if x[1] is not None]
            assert prices, p.key
            assert min(prices) > p.msrp * 0.3 and max(prices) < p.msrp * 1.25, p.key


def test_upcs_are_valid_and_unique():
    upcs = [p.upc for p in CATALOG]
    assert len(set(upcs)) == len(upcs)
    for u in upcs:
        d = [int(c) for c in u]
        assert len(u) == 12 and u[0] == "2"
        assert (sum(d[0:11:2]) * 3 + sum(d[1:11:2]) + d[11]) % 10 == 0


def test_search_keeps_variants_apart():
    m = MockAdapter(today_fn=lambda: TODAY)
    keys = [r.source_ref for r in m.search(parse_query("ninja air fryer"))]
    assert {"ninja-af101", "ninja-af161"} <= set(keys)
    tide = [r.variant for r in m.search(parse_query("tide pods"))]
    assert {"81 count", "112 count", "152 count"} <= set(tide)


def test_search_by_upc_asin_model_and_url():
    m = MockAdapter(today_fn=lambda: TODAY)
    assert [r.source_ref for r in m.search(parse_query(BY_KEY["vitamix-5200"].upc))] == ["vitamix-5200"]
    assert [r.source_ref for r in m.search(parse_query("B0DEMO0161"))] == ["ninja-af161"]
    assert m.search(parse_query("KSM150PSOB"))[0].source_ref == "kitchenaid-ksm150-black"
    hits = m.search(parse_query("https://www.walmart.com/ip/Ninja-Air-Fryer-Max-XL-AF161/123456789"))
    assert hits[0].source_ref == "ninja-af161"
