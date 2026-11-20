from app.query import parse_query


def test_text_and_model_number():
    q = parse_query("ninja air fryer")
    assert q.kind == "text" and q.text == "ninja air fryer"
    assert parse_query("WH-1000XM5").kind == "text"


def test_upc_and_ean():
    q = parse_query("0 12345 67890 5")
    assert q.kind == "upc" and q.upc == "012345678905"
    assert parse_query("4006381333931").kind == "upc"


def test_asin():
    q = parse_query("b0demo0161")
    assert q.kind == "asin" and q.asin == "B0DEMO0161"


def test_amazon_url():
    q = parse_query("https://www.amazon.com/Ninja-AF161-Fryer/dp/B07FDJMC9Q/ref=sr_1_1?keywords=x")
    assert q.kind == "asin" and q.asin == "B07FDJMC9Q" and q.retailer == "amazon"
    assert "Ninja AF161 Fryer" in q.text


def test_retailer_urls():
    w = parse_query("https://www.walmart.com/ip/Ninja-Air-Fryer-Max-XL-AF161/123456789?athbdg=L1600")
    assert (w.retailer, w.retailer_item_id, w.text) == ("walmart", "123456789", "Ninja Air Fryer Max XL AF161")
    t = parse_query("https://www.target.com/p/kitchenaid-artisan-5qt-stand-mixer/-/A-12345678")
    assert t.retailer == "target" and t.retailer_item_id == "12345678"
    b = parse_query("https://www.bestbuy.com/site/sony-wh-1000xm5-headphones-black/6505727.p?skuId=6505727")
    assert b.retailer == "bestbuy" and "sony wh 1000xm5" in b.text.lower()
    h = parse_query("homedepot.com/p/DEWALT-20V-MAX-Drill-DCD771C2/204279858")
    assert h.retailer == "homedepot" and h.retailer_item_id == "204279858"
    c = parse_query("https://www.costco.com/vitamix-5200-blender.product.100123456.html")
    assert c.retailer == "costco" and c.text == "vitamix 5200 blender"


def test_manufacturer_url_uses_slug():
    q = parse_query("https://www.ninjakitchen.com/products/ninja-air-fryer-max-xl-zidAF161/")
    assert q.kind == "url" and "air fryer max xl" in q.text


def test_short_link_is_explained_not_fetched():
    q = parse_query("https://amzn.to/3abcDEF")
    assert q.note and "Short links" in q.note and not q.text
