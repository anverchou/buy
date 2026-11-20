"""The daily price-snapshot job.

Records one price point per tracked listing per day. Safe to schedule hourly: products
already checked today are skipped (no API calls) unless --force is used, so a laptop that
was asleep at the scheduled time still gets its snapshot on the next run.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from ..models import ListItem, Product, SnapshotRun
from ..timeutil import local_day
from .context import Ctx
from .listview import build_list_row
from .notify import send_push
from .pricing import refresh_product


def products_to_track(session, include_all: bool = False) -> list[Product]:
    if include_all:
        return list(session.scalars(select(Product).order_by(Product.id)))
    stmt = (select(Product).join(ListItem).where(ListItem.purchased.is_(False))
            .distinct().order_by(Product.id))
    return list(session.scalars(stmt))


def check_target_alerts(ctx: Ctx) -> list[str]:
    """Flag (and optionally push) items whose best total dropped to/below target.
    Each item alerts once per drop; it re-arms when the price goes back above target."""
    messages = []
    items = ctx.session.scalars(select(ListItem).where(ListItem.purchased.is_(False),
                                                       ListItem.target_price.is_not(None)))
    for item in items:
        row = build_list_row(ctx, item)
        if row.below_target and row.best:
            if item.alerted_at is None:
                name = f"{item.product.name} ({item.product.variant})" if item.product.variant else item.product.name
                msg = (f"{name}: ${row.best.total:,.2f} at {row.best.retailer_label} "
                       f"(target ${item.target_price:,.2f})")
                messages.append(msg)
                base = ctx.config.get("APP_BASE_URL")
                send_push(ctx.config, "Price below target", msg,
                          f"{base}/products/{item.product_id}" if base else None)
                item.alerted_at = ctx.now
        elif item.alerted_at is not None:
            item.alerted_at = None
    return messages


def run_snapshot(ctx: Ctx, force: bool = False, include_all: bool = False, log=print) -> SnapshotRun:
    run = SnapshotRun(started_at=ctx.now)
    ctx.session.add(run)
    lines = []
    for product in products_to_track(ctx.session, include_all):
        label = product.name + (f" ({product.variant})" if product.variant else "")
        if (not force and product.last_refreshed_at
                and local_day(product.last_refreshed_at, ctx.tzname) == ctx.today):
            run.products_skipped += 1
            lines.append(f"skip  {label}: already checked today")
            continue
        report = refresh_product(ctx, product)
        run.products_checked += 1
        run.points_written += report.points
        run.errors += len(report.errors)
        line = f"ok    {label}: {report.points} prices"
        if report.history_points:
            line += f", {report.history_points} history days backfilled"
        lines.append(line)
        for err in report.errors:
            lines.append(f"error {label}: {err}")
        ctx.session.commit()
    for msg in check_target_alerts(ctx):
        lines.append(f"alert {msg}")
    run.finished_at = datetime.now(timezone.utc).replace(tzinfo=None)
    run.log = "\n".join(lines)
    ctx.session.commit()
    for line in lines:
        log(line)
    log(f"done: {run.products_checked} checked, {run.products_skipped} skipped, "
        f"{run.points_written} prices recorded, {run.errors} errors")
    return run
