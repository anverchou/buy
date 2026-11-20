"""Price history: time windows, summary stats, the buy signal and chart data."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, timedelta

from ..models import Listing, Product
from ..retailers import CONDITION_LABELS, RETAILER_ORDER, RETAILERS, retailer_name
from ..timeutil import short_date_year
from .context import Ctx, Prefs

# key, button label, days (None = year to date), adjective used in sentences
WINDOWS = (
    ("30d", "30 days", 30, "30-day"),
    ("60d", "60 days", 60, "60-day"),
    ("90d", "90 days", 90, "90-day"),
    ("ytd", "YTD", None, "YTD"),
)
WINDOW_KEYS = [w[0] for w in WINDOWS]
DEFAULT_WINDOW = "90d"

AT_LOW_TOLERANCE = 0.02   # within 2% of the low counts as "at the low"
NEAR_AVG_BAND = 0.03      # within 3% of the average counts as "near average"
MIN_DAYS_FOR_SIGNAL = 7


def window_start(key: str, today: date) -> date:
    for k, _label, days, _adj in WINDOWS:
        if k == key:
            return date(today.year, 1, 1) if days is None else today - timedelta(days=days - 1)
    raise KeyError(key)


def window_adj(key: str) -> str:
    return next(w[3] for w in WINDOWS if w[0] == key)


@dataclass
class Stats:
    current: float | None
    low: float | None
    low_day: date | None
    high: float | None
    high_day: date | None
    avg: float | None
    pct_above_low: float | None
    days: int
    first_day: date | None
    partial: bool
    tracking_label: str | None

    def to_json(self) -> dict:
        d = asdict(self)
        for k in ("low_day", "high_day", "first_day"):
            d[k] = d[k].isoformat() if d[k] else None
        for k in ("current", "low", "high", "avg"):
            d[k] = round(d[k], 2) if d[k] is not None else None
        d["pct_above_low"] = round(d["pct_above_low"], 1) if d["pct_above_low"] is not None else None
        return d


def compute_stats(series: dict[date, float | None], start: date, today: date,
                  current: float | None) -> Stats | None:
    tracked = [d for d, v in series.items() if v is not None]
    if not tracked:
        return None
    first = min(tracked)
    vals = [(d, v) for d, v in sorted(series.items()) if v is not None and start <= d <= today]
    if not vals:
        return Stats(current, None, None, None, None, None, None, 0, first, first > start,
                     f"Tracking since {short_date_year(first, today)}")
    low_day, low = min(vals, key=lambda x: (x[1], -x[0].toordinal()))   # most recent low
    high_day, high = max(vals, key=lambda x: (x[1], -x[0].toordinal()))
    avg = sum(v for _, v in vals) / len(vals)
    pct = ((current - low) / low * 100) if (current is not None and low) else None
    partial = first > start
    return Stats(current, low, low_day, high, high_day, avg, pct, len(vals), first, partial,
                 f"Tracking since {short_date_year(first, today)}" if partial else None)


def buy_signal(stats: Stats | None, key: str) -> dict:
    adj = window_adj(key)
    if stats is None or stats.current is None:
        return {"kind": "none", "label": "No current price", "detail": ""}
    if stats.days < MIN_DAYS_FOR_SIGNAL or stats.avg is None:
        return {"kind": "insufficient", "label": "Not enough history yet",
                "detail": f"{stats.days} day{'s' if stats.days != 1 else ''} of prices in this window"}
    cur, low, avg = stats.current, stats.low, stats.avg
    detail = (f"${cur:,.2f} now · ${avg:,.2f} average · "
              f"{max(stats.pct_above_low or 0, 0):.0f}% above the {adj} low of ${low:,.2f}")
    if cur <= low * (1 + AT_LOW_TOLERANCE):
        return {"kind": "low", "label": f"At {adj} low", "detail": detail}
    diff = (cur - avg) / avg
    if diff < -NEAR_AVG_BAND:
        return {"kind": "below", "label": "Below average", "detail": detail}
    if diff <= NEAR_AVG_BAND:
        return {"kind": "near", "label": "Near average", "detail": detail}
    return {"kind": "above", "label": "Above average – consider waiting", "detail": detail}


def counts_toward_best(listing: Listing, prefs: Prefs) -> bool:
    if listing.condition != "new" and not prefs.include_nonnew:
        return False
    if listing.requires_membership and not prefs.is_member(listing.retailer):
        return False
    return True


def listing_series(listing: Listing, start: date | None = None) -> dict[date, float | None]:
    return {p.day: p.price for p in listing.price_points if start is None or p.day >= start}


def best_series(product: Product, prefs: Prefs, start: date | None = None) -> dict[date, float]:
    """Lowest in-stock price per day across the listings that count toward "best"."""
    best: dict[date, float] = {}
    for l in product.listings:
        if not counts_toward_best(l, prefs):
            continue
        for p in l.price_points:
            if p.price is None or not p.in_stock or (start and p.day < start):
                continue
            if p.day not in best or p.price < best[p.day]:
                best[p.day] = p.price
    return best


def _sorted_listings(product: Product) -> list[Listing]:
    cond_order = {"new": 0, "renewed": 1, "used": 2}
    return sorted(product.listings, key=lambda l: (RETAILER_ORDER.index(l.retailer)
                                                   if l.retailer in RETAILER_ORDER else 99,
                                                   cond_order.get(l.condition, 9)))


def listing_label(listing: Listing, brand: str | None) -> str:
    label = retailer_name(listing.retailer, brand)
    if listing.condition != "new":
        label += f" ({CONDITION_LABELS.get(listing.condition, listing.condition)})"
    return label


def best_current_price(rows) -> float | None:
    """Current price (before shipping) of the cheapest eligible listing right now."""
    prices = [r.price for r in rows if r.eligible and r.price is not None]
    return min(prices) if prices else None


def best_window(ctx: Ctx, product: Product, rows, key: str) -> tuple[Stats | None, dict]:
    start = window_start(key, ctx.today)
    stats = compute_stats(best_series(product, ctx.prefs), start, ctx.today, best_current_price(rows))
    return stats, buy_signal(stats, key)


def history_payload(ctx: Ctx, product: Product, rows) -> dict:
    today = ctx.today
    starts = {k: window_start(k, today) for k in WINDOW_KEYS}
    span_start = min(starts.values())
    labels = [span_start + timedelta(days=i) for i in range((today - span_start).days + 1)]
    listings = _sorted_listings(product)
    current_by_listing = {r.listing_id: r for r in rows if r.listing_id}

    datasets = []
    for l in listings:
        pts = {p.day: p for p in l.price_points if p.day >= span_start}
        datasets.append({
            "id": l.id,
            "label": listing_label(l, product.brand),
            "retailer": l.retailer,
            "slot": RETAILERS[l.retailer].slot if l.retailer in RETAILERS else 8,
            "condition": l.condition,
            "manual": l.source == "manual" or any(p.source == "manual" for p in pts.values()),
            "hidden": l.condition != "new" and not ctx.prefs.include_nonnew,
            "data": [round(pts[d].price, 2) if d in pts and pts[d].price is not None else None
                     for d in labels],
            "stock": [(pts[d].in_stock if d in pts else None) for d in labels],
        })

    best = best_series(product, ctx.prefs)
    cur_best = best_current_price(rows)
    windows = {}
    for key, label, _days, adj in WINDOWS:
        start = starts[key]
        best_stats = compute_stats(best, start, today, cur_best)
        stat_rows = []
        for l in listings:
            row = current_by_listing.get(l.id)
            st = compute_stats(listing_series(l), start, today, row.price if row else None)
            stat_rows.append({
                "id": l.id,
                "label": listing_label(l, product.brand),
                "slot": RETAILERS[l.retailer].slot if l.retailer in RETAILERS else 8,
                "in_stock": bool(row and row.in_stock),
                "stats": st.to_json() if st else None,
            })
        windows[key] = {
            "key": key, "label": label, "adj": adj, "start": start.isoformat(),
            "best": best_stats.to_json() if best_stats else None,
            "signal": buy_signal(best_stats, key),
            "rows": stat_rows,
        }
    return {
        "labels": [d.isoformat() for d in labels],
        "datasets": datasets,
        "windows": windows,
        "default": DEFAULT_WINDOW,
        "today": today.isoformat(),
    }
