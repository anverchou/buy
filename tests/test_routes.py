import json
import re

from sqlalchemy import select

from app import create_app
from app.adapters.mock import BY_KEY, MockAdapter
from app.models import ListItem, Product


def pick(client, q):
    page = client.get(f"/search?q={q}").data.decode()
    payloads = re.findall(r'name="payload" value="([^"]+)"', page)
    assert payloads, page
    from html import unescape
    return [json.loads(unescape(p)) for p in payloads]


def test_full_flow(app, session):
    client = app.test_client()
    assert client.get("/").status_code == 200
    results = pick(client, "air+fryer")
    assert {r["variant"] for r in results} >= {"4 qt", "5.5 qt"}
    payload = next(r for r in results if r["variant"] == "5.5 qt")
    resp = client.post("/products/select", data={"payload": json.dumps(payload)})
    assert resp.status_code == 302
    pid = int(resp.headers["Location"].rsplit("/", 1)[1])
    page = client.get(f"/products/{pid}").data.decode()
    assert "Lowest total" in page and 'id="history-data"' in page and "Not carried in the demo data" not in page
    data = json.loads(re.search(r'<script id="history-data" type="application/json">(.*?)</script>', page, re.S).group(1))
    assert set(data["windows"]) == {"30d", "60d", "90d", "ytd"} and data["datasets"]

    client.post("/list/add", data={"product_id": pid, "quantity": "2", "target_price": "$1,000",
                                   "priority": "1", "notes": "test"})
    item = session.scalar(select(ListItem).where(ListItem.product_id == pid))
    assert (item.quantity, item.target_price, item.priority) == (2, 1000.0, 1)
    listing = client.get("/").data.decode()
    assert "Below target" in listing and "Total at best prices" in listing

    client.post(f"/list/{item.id}/update", data={"quantity": "3", "target_price": "", "priority": "3"})
    session.refresh(item)
    assert item.quantity == 3 and item.target_price is None

    client.post(f"/products/{pid}/log-price", data={"retailer": "costco", "price": "88.50", "shipping": ""})
    assert "88.50" in client.get(f"/products/{pid}").data.decode()

    client.post(f"/list/{item.id}/purchase", data={"paid_price": "90", "paid_retailer": "Costco"})
    session.refresh(item)
    assert item.purchased and item.paid_price == 90.0
    assert "Costco" in client.get("/archive").data.decode()
    client.post(f"/list/{item.id}/restore")
    session.refresh(item)
    assert not item.purchased
    client.post(f"/list/{item.id}/delete")
    assert session.get(ListItem, item.id) is None


def test_settings_and_snapshot_pages(app):
    client = app.test_client()
    assert client.get("/settings").status_code == 200
    assert client.post("/settings", data={"costco_member": "1"}).status_code == 302
    assert client.post("/settings/run-snapshot").status_code == 302
    assert client.get("/img/demo/vitamix-5200.svg").mimetype == "image/svg+xml"


def test_demo_seed_and_remove(app, session):
    client = app.test_client()
    client.post("/settings/load-demo")
    assert session.scalar(select(ListItem).where(ListItem.purchased.is_(True)))
    page = client.get("/").data.decode()
    assert "Sony WH-1000XM5" in page
    client.post("/settings/remove-demo")
    assert session.scalar(select(Product)) is None


def test_csrf_and_password(tmp_path, monkeypatch):
    monkeypatch.delenv("SERPAPI_API_KEY", raising=False)
    monkeypatch.delenv("KEEPA_API_KEY", raising=False)
    app = create_app({"TESTING": True, "DATABASE_URL": f"sqlite:///{tmp_path / 'a.db'}",
                      "APP_PASSWORD": "hunter22", "SERPAPI_API_KEY": "", "KEEPA_API_KEY": ""})
    client = app.test_client()
    r = client.get("/")
    assert r.status_code == 302 and "/login" in r.headers["Location"]
    page = client.get("/login").data.decode()
    token = re.search(r'name="csrf" value="([^"]+)"', page).group(1)
    assert client.post("/login", data={"password": "hunter22"}).status_code == 400   # no CSRF token
    r = client.post("/login", data={"password": "hunter22", "csrf": token, "next": "/"})
    assert r.status_code == 302
    assert client.get("/").status_code == 200
