# CLAUDE.md — Price-Tracked Shopping List

Context for Claude Code. This project was designed and built in a claude.ai chat
(Nov 19, 2026); this file carries over the decisions so work can continue here.

## What it is
Single-user web app: search a product (name, model #, UPC/EAN, ASIN, or pasted URL), compare
current prices across retailers, see per-retailer price history with a buy signal, and keep a
shopping list with targets, sparklines, totals and a purchase archive. README.md has the full
user-facing description, setup and scheduling instructions.

## Commands
```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
cp .env.example .env
python -m flask --app app run --port 5001            # 5000 clashes with macOS AirPlay
python -m flask --app app snapshot [--force] [--all] # daily price job (idempotent per day)
python -m flask --app app seed-demo | remove-demo
pytest -q                                             # 39 tests, no network
```
SQLite DB lives in `instance/prices.db` (gitignored). Delete it to start fresh; demo data
re-seeds on first start when no API keys are set.

## Decisions made with the user (don't silently change these)
- Stack: Python 3.10+ / Flask / SQLAlchemy 2 / SQLite / Chart.js (vendored in
  `app/static/vendor`, no CDN). Server-rendered Jinja + small vanilla JS. Mobile-first CSS.
- Runs on the user's own computer by default; optional `APP_PASSWORD` gate + CSRF on all POSTs
  for phone/LAN or hosted use.
- Data sources: mock by default; SerpApi (Google Shopping) for current prices across stores;
  Keepa for Amazon history (optional — paid, no free tier). `DATA_MODE=auto` switches to live
  when any key is set.
- No scraping. Retailers Google Shopping doesn't list (often Costco, some manufacturer stores)
  show a placeholder row + manual "Log a price" entry, stored as normal PricePoints.
- Alerts: in-app "Below target" flag; optional ntfy push (`NTFY_TOPIC`), once per drop.
- "Lowest" = lowest in-stock total (price + shipping, pre-tax), price ≤ `STALE_AFTER_DAYS` old,
  new condition only (renewed/used shown but excluded unless toggled), Costco counted unless the
  user unticks membership in Settings.
- Core retailers always shown (Amazon, manufacturer, Costco); Walmart, Target, Best Buy,
  Home Depot shown when they carry the item. Other stores are ignored (`app/retailers.py`).

## Architecture
- `app/adapters/` — every source implements `PriceSource` (`base.py`):
  `search(ParsedQuery)`, `get_current_prices(product)`, `get_history(listing, since)`,
  `handles(product)`. **Adapters never touch the DB**; they return dataclasses
  (`ProductResult`, `Offer`, `HistoryPoint`). Registry: `adapters/__init__.py`
  (price order mock → serpapi → keepa so Keepa's Amazon price wins).
  - `mock.py`: 14 real products (fake UPCs with "2" prefix, `B0DEMO…` ASINs), deterministic
    day-by-day simulation anchored at 2025-10-01 — calendar sales events, retailer habits,
    OOS runs, staggered tracking start dates. Values for past days never change.
  - `serpapi.py`: google_shopping search → google_immersive_product (more_stores) for offers,
    fallback google_product by product_id, then re-search. google_product `price_insights`
    backfills per-store history once. Monthly budget guard via `ApiUsage`.
  - `keepa.py`: product by ASIN/UPC (1 token, history included), csv → daily points.
- `app/services/` — all DB logic. `context.Ctx` bundles session/config/clock/prefs (tests pin
  the clock). `pricing.py` (refresh, upsert, comparison rows), `history.py` (windows, stats,
  buy signal, chart payload), `listview.py`, `snapshot.py`, `catalog.py`, `demo.py`.
- `app/query.py` classifies input; URLs are parsed locally (ASIN, retailer item id, slug text) —
  never fetched. Short links (amzn.to, a.co) get an explanatory note.
- Data model (`models.py`): spec fields plus Product.variant/asin/google_product_id/source/
  source_ref; Listing unique per (product, retailer, condition); PricePoint unique per
  (listing, local day) with `day` column; ListItem.paid_price/paid_retailer/alerted_at.

## Invariants
- One PricePoint per listing per local day (APP_TIMEZONE, default America/Los_Angeles);
  same-day refreshes update in place. Backfill never overwrites, except Keepa over non-Keepa
  history for Amazon.
- Different variants are never merged (dedupe only on same UPC/ASIN **and** same variant text).
- Snapshot job skips products already refreshed today unless `--force` (safe to run hourly).
- Error messages must never include API keys (request URLs contain them) — see `_describe()`
  in serpapi.py and the Keepa equivalent; there's a test for this.
- Chart colors follow the retailer (fixed slot in `RETAILERS`), never rank. Light/dark palettes
  are CSS variables in `app.css`; the chart reads them at build time.

## Buy signal (history.py constants)
Compared against the "Best available" line (lowest eligible in-stock price per day):
at low = within 2% of window low; below avg = >3% under; near = ±3%; above = >3% over
("Above average – consider waiting"); <7 days of data = "Not enough history yet".
Windows: 30d, 60d, 90d (default), YTD.

## Status / next steps
- Verified: tests, all pages in a headless browser at 1280px and 390px, light and dark, chart
  tooltip/crosshair, window toggles, partial-coverage labels, snapshot CLI + scripts.
- **Not yet verified against live APIs** (built from SerpApi docs and Keepa's csv format with
  fixture tests). First live task: set `SERPAPI_API_KEY`, run a real search, add a product,
  and check `offer_from_store()` field mapping (price, shipping, stock, condition) and
  `price_insights` date formats against real responses.
- Ideas not built: price-adjustment tracking after purchase, unit pricing across sizes,
  more retailers (Lowe's, Sam's Club), Postgres deployment recipe tested end to end.
