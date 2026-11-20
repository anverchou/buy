"""Shopping-list view model: best price per item, sparkline, target flags, totals."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from ..models import Listing, ListItem, Product
from .context import Ctx
from .history import best_series, best_window, window_start
from .pricing import Row, best_row, listing_rows


@dataclass
class ListRow:
    item: ListItem
    product: Product
    best: Row | None
    rows: list[Row]
    spark_svg: str
    trend_pct: float | None
    below_target: bool
    signal: dict
    line_total: float | None


def sparkline_svg(values: list[float | None], width: int = 112, height: int = 32) -> str:
    """Single-series sparkline: muted 2px line with an accent end dot."""
    pts = [(i, v) for i, v in enumerate(values) if v is not None]
    if len(pts) < 2:
        return ('<svg class="spark" viewBox="0 0 %d %d" width="%d" height="%d" aria-hidden="true"></svg>'
                % (width, height, width, height))
    lo, hi = min(v for _, v in pts), max(v for _, v in pts)
    span = (hi - lo) or 1.0
    pad = 5
    n = max(len(values) - 1, 1)

    def xy(i, v):
        x = pad + (width - 2 * pad) * i / n
        y = pad + (height - 2 * pad) * (1 - (v - lo) / span) if hi != lo else height / 2
        return x, y

    coords = [xy(i, v) for i, v in pts]
    path = " ".join(f"{'M' if k == 0 else 'L'}{x:.1f},{y:.1f}" for k, (x, y) in enumerate(coords))
    ex, ey = coords[-1]
    return (f'<svg class="spark" viewBox="0 0 {width} {height}" width="{width}" height="{height}" '
            f'aria-hidden="true"><path d="{path}" fill="none" stroke="var(--spark-line)" stroke-width="2" '
            f'stroke-linejoin="round" stroke-linecap="round"/>'
            f'<circle cx="{ex:.1f}" cy="{ey:.1f}" r="4" fill="var(--series-1)" '
            f'stroke="var(--surface-1)" stroke-width="2"/></svg>')


def load_items(session, purchased: bool) -> list[ListItem]:
    stmt = (select(ListItem).where(ListItem.purchased.is_(purchased))
            .options(selectinload(ListItem.product).selectinload(Product.listings)
                     .selectinload(Listing.price_points)))
    if purchased:
        stmt = stmt.order_by(ListItem.purchased_at.desc())
    else:
        stmt = stmt.order_by(ListItem.priority, ListItem.created_at)
    return list(session.scalars(stmt))


def build_list_row(ctx: Ctx, item: ListItem) -> ListRow:
    product = item.product
    rows = listing_rows(ctx, product, with_placeholders=False)
    best = best_row(rows)
    start = window_start("30d", ctx.today)
    series = best_series(product, ctx.prefs, start)
    values = [series.get(start + timedelta(days=i)) for i in range((ctx.today - start).days + 1)]
    known = [v for v in values if v is not None]
    trend = ((known[-1] - known[0]) / known[0] * 100) if len(known) >= 2 and known[0] else None
    below = bool(best and item.target_price is not None and best.total is not None
                 and best.total <= item.target_price)
    _stats, signal = best_window(ctx, product, rows, "90d")
    line_total = round(best.total * item.quantity, 2) if best and best.total is not None else None
    return ListRow(item, product, best, rows, sparkline_svg(values), trend, below, signal, line_total)


def list_view(ctx: Ctx) -> dict:
    items = [build_list_row(ctx, i) for i in load_items(ctx.session, purchased=False)]
    priced = [r for r in items if r.line_total is not None]
    return {
        "rows": items,
        "total": round(sum(r.line_total for r in priced), 2),
        "units": sum(r.item.quantity for r in priced),
        "unpriced": len(items) - len(priced),
        "below_target": sum(1 for r in items if r.below_target),
    }
