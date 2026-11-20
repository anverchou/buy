# Price-Tracked Shopping List

A shopping list that compares today's prices across retailers and keeps each item's price
history, so you can tell whether now is a good time to buy.

- **Search** by product name, model number, UPC/EAN, ASIN, or a pasted product link.
  Every size, color and variant is its own result; they are never merged.
- **Compare** Amazon, the manufacturer's store and Costco (always shown), plus Walmart, Target,
  Best Buy and Home Depot when they carry the item. Each row shows the price, shipping, total,
  stock, condition, whether a membership is needed, a link, and when it was last checked.
  Rows are sorted by total cost, and the lowest is highlighted.
- **History:** a chart with 30-day, 60-day, 90-day and YTD views, one line per retailer, with
  hover tooltips. A summary table shows current, low, high, average and % above low, and a
  plain-language buy signal appears above it. Retailers tracked for less than the window
  are labeled "Tracking since …".
- **List:** quantity, target price, priority and notes for each item, plus its best current
  price, a 30-day sparkline, a running total and "Below target" flags. Check items off as
  you buy them; they move to Past buys with what you paid.

Stack: Python 3.10+, Flask, SQLAlchemy and SQLite, with Chart.js bundled in the app, so it
needs no CDN. The layout is responsive and built for phones first.

---

## 1. Quick start (demo data, no keys)

```bash
cd price-tracked-shopping-list
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                 # Windows: copy .env.example .env
flask --app app run
```

Open <http://127.0.0.1:5000>. On first start the app loads a demo list of real products
with 12+ months of simulated prices. The simulation includes Black Friday, Prime Day, Costco
instant savings, out-of-stock gaps and staggered tracking start dates. Every feature works
in this mode. Remove the demo data later under **Settings → Demo data**.

### Use it from your phone

```bash
# in .env: APP_PASSWORD=something-long   (anyone on your Wi-Fi could reach it otherwise)
flask --app app run --host 0.0.0.0
```

On your phone, open `http://<your-computer's-LAN-IP>:5000`. For access away from home,
install [Tailscale](https://tailscale.com) on both devices and use the computer's Tailscale
address instead. For a longer-running server, use waitress, which is included and also works
on Windows:

```bash
waitress-serve --listen=0.0.0.0:5000 --call app:create_app
```

---

## 2. Real prices: add API keys

Put the keys in `.env`. When any key is set, `DATA_MODE=auto` switches to live sources.

| Source | Gives you | Cost | How the app uses it |
|---|---|---|---|
| **SerpApi**, Google Shopping (`SERPAPI_API_KEY`) | Search, plus current offers from every store Google lists | Free plan: 250 searches/month | 1 search per query. Adding a product: 1 call for the store offers and 1 for Google's price-history insights, when available. Daily: **1 call per item on your list** |
| **Keepa** (`KEEPA_API_KEY`) | Full Amazon price history; ASIN/UPC lookup | Paid, no free tier | 1 token per product per day. History comes in the same call |

The free SerpApi plan covers about 8 list items checked daily. `SERPAPI_MONTHLY_LIMIT` is a
hard stop: when the app reaches it, it stops calling and tells you why.

Without Keepa, Amazon's history builds up from the daily snapshots, the same way the other
stores' does. Keepa is optional, and you can add it at any time.

**Stores that aren't on Google Shopping.** Costco and many manufacturer stores have no public
price API, and this app doesn't scrape them. They show up only when they list on Google
Shopping. Otherwise the row reads "Not found on Google Shopping" and has a **Log a price**
button. Prices you log are stored like any other price point and appear in the table and
chart (as dots). Pasted links are parsed locally for the product ID or name; the app never
loads the retailer's page.

---

## 3. Schedule the daily price snapshot

`flask --app app snapshot` records one price per listing per day for every item on your
active list. It is **idempotent**: products already checked today are skipped without any
API calls. That means you can schedule it **hourly**, and a laptop that was asleep at the
scheduled time still gets its snapshot on the next run.

```bash
flask --app app snapshot            # normal run
flask --app app snapshot --force    # re-check items already checked today (uses credits)
flask --app app snapshot --all      # include products you viewed but didn't add to the list
```

Output is appended to `instance/snapshot.log` when you run it through the scripts below. Each
run is also listed under **Settings → Daily price snapshot**, where there is a
**Run snapshot now** button.

**macOS / Linux (cron).** Run `crontab -e` and add:

```cron
# every hour; only the first successful run each day calls the APIs
5 * * * * /full/path/to/price-tracked-shopping-list/scripts/run_snapshot.sh
```

**macOS (launchd).** This also runs after the Mac wakes up. Edit the path in
`scripts/com.pricetracker.snapshot.plist`, then:

```bash
cp scripts/com.pricetracker.snapshot.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.pricetracker.snapshot.plist
```

**Windows (Task Scheduler).** In PowerShell:

```powershell
schtasks /Create /SC HOURLY /TN "PriceSnapshot" /TR "powershell -NoProfile -ExecutionPolicy Bypass -File C:\path\to\price-tracked-shopping-list\scripts\run_snapshot.ps1"
```

In Task Scheduler you can then tick *"Run task as soon as possible after a scheduled start is
missed."*

**Hosted** (Render, Railway, Fly.io). Run the web service with
`waitress-serve --listen=0.0.0.0:$PORT --call app:create_app`, and add a cron job that runs
`flask --app app snapshot` once a day. Both must use the same database: either point
`DATABASE_URL` at Postgres (and add `psycopg[binary]` to requirements), or mount a persistent
disk for the SQLite file. Set `APP_PASSWORD` and `SECRET_KEY`.

**Price-drop alerts (optional).** Install the [ntfy](https://ntfy.sh) app, subscribe to a
hard-to-guess topic, and set `NTFY_TOPIC` in `.env`. After each snapshot you get one push per
item that dropped to or below its target. An item alerts again only after its price has gone
back above target and dropped again.

---

## 4. How it works

```
app/
  adapters/        one class per price source, all implementing the same interface
    base.py        PriceSource: search(query) · get_current_prices(product) · get_history(listing, since)
    mock.py        seeded catalogue + deterministic 12-month price simulation
    serpapi.py     Google Shopping search, store offers, price-insight history
    keepa.py       Amazon lookup + history (Keepa csv format → daily points)
    manual.py      prices you log yourself
  services/        search, refresh, history stats, buy signal, list view, snapshot job, demo seed
  models.py        Product · Listing · PricePoint · ListItem (+ Setting, ApiUsage, SnapshotRun)
  query.py         classifies input: text / UPC / ASIN / URL (parses retailer links locally)
  retailers.py     retailer list, store-name normalization, shipping/condition parsing
  routes.py, templates/, static/
tests/             39 tests, no network needed
```

**Data model.** These follow the spec, plus a few additions:

- **Product:** `id, name, brand, model, upc, image_url`, plus `variant`, `asin`,
  `google_product_id`, `source`, `source_ref`.
- **Listing:** `id, product_id, retailer, url, requires_membership`, plus `condition`,
  `source`, `seller`, `external_id`. A product has one listing per retailer and condition,
  so a renewed offer never hides the new one.
- **PricePoint:** `listing_id, price, shipping, in_stock, captured_at`, plus `day` (your
  local date), with **at most one point per listing per day**.
- **ListItem:** `product_id, quantity, target_price, priority, notes, purchased,
  purchased_at`, plus `paid_price`, `paid_retailer`, `alerted_at`.

**What counts as "lowest."** The in-stock offer with the lowest total (price plus shipping,
before tax) whose price is no more than 7 days old. Renewed and used offers are listed but
not counted unless you enable them in Settings. Costco counts unless you untick
"I have a Costco membership."

**Buy signal.** Today's best price is compared with the *Best available* line (the lowest
in-stock price each day) for the selected window:

| Signal | Rule |
|---|---|
| At 90-day low (or 30-day, 60-day, YTD) | within 2% of the window's low |
| Below average | more than 3% under the window's average |
| Near average | within ±3% of the average |
| Above average – consider waiting | more than 3% over the average |
| Not enough history yet | fewer than 7 days of prices in the window |

The thresholds are constants at the top of `app/services/history.py`. The list view shows
the 90-day signal.

**Adding a source.** Subclass `PriceSource`, return `ProductResult` / `Offer` /
`HistoryPoint` objects (adapters never touch the database), and register the class in
`app/adapters/__init__.py`.

---

## 5. Tests

```bash
pip install -r requirements-dev.txt
pytest -q
```

The tests cover input parsing, history statistics and signal thresholds, how stable the
simulated data is, separation of variants, one point per day, snapshot skip and force,
target alerts, membership and condition rules, SerpApi and Keepa parsing (against
recorded-shape fixtures), and the full web flow including CSRF protection and the password
gate.

## 6. Known limits

- **SerpApi field names.** The SerpApi adapter follows SerpApi's documented response fields.
  If Google changes a layout, `offer_from_store()` in `serpapi.py` is the one place to
  adjust. Product page tokens can expire, so the adapter falls back to the stable product
  ID, and then to a fresh search.
- **Title parsing.** Brand, model and variant for Google results are parsed from the listing
  title, so they can be incomplete. The product name is always kept as listed.
- **Costs left out.** Totals don't include sales tax or membership fees.
- **One user.** The app is designed for a single person.
- **Demo prices.** Demo prices are simulated and the demo UPCs and ASINs are fake. They use
  the UPC "2" prefix and a `B0DEMO` ASIN prefix, so they never match real products.
