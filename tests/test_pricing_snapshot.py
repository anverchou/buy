from datetime import timedelta

from sqlalchemy import func, select

from app.adapters.mock import BY_KEY, MockAdapter
from app.models import ListItem, PricePoint
from app.services.catalog import search, select_result
from app.services.context import make_ctx, save_pref
from app.services.history import history_payload
from app.services.listview import list_view
from app.services.pricing import best_row, listing_rows, log_manual_price, refresh_product
from app.services.snapshot import run_snapshot

from .conftest import FIXED_NOW


def add(ctx, key):
    p = select_result(ctx, MockAdapter.to_result(BY_KEY[key]).to_payload())
    refresh_product(ctx, p)
    ctx.session.commit()
    return p


def count_points(session):
    return session.scalar(select(func.count(PricePoint.id)))


def test_refresh_backfills_history_and_is_idempotent(ctx, session):
    p = add(ctx, "sony-wh1000xm5")
    n = count_points(session)
    assert n > 365 * 3                     # several retailers with months of history
    refresh_product(ctx, p)
    session.commit()
    assert count_points(session) == n      # same day: updated in place, no duplicates
    days = [pt.day for l in p.listings for pt in l.price_points]
    assert len(days) == len(set((pt.listing_id, pt.day) for l in p.listings for pt in l.price_points))


def test_partial_tracking_and_placeholders(ctx):
    p = add(ctx, "dewalt-dcd771c2")
    rows = listing_rows(ctx, p)
    placeholders = {r.retailer for r in rows if r.placeholder}
    assert placeholders == {"manufacturer", "costco"}     # core retailers that don't carry it
    assert rows[-1].placeholder                             # placeholders sort last
    totals = [r.total for r in rows if not r.placeholder and r.in_stock and r.price is not None]
    assert totals == sorted(totals)                         # sorted by total cost

    af101 = add(ctx, "ninja-af101")
    data = history_payload(ctx, af101, listing_rows(ctx, af101))
    target_row = next(r for r in data["windows"]["90d"]["rows"] if r["label"] == "Target")
    assert target_row["stats"]["partial"] and target_row["stats"]["tracking_label"] == "Tracking since Oct 2"


def test_best_excludes_renewed_and_respects_membership(ctx, session):
    p = add(ctx, "sony-wh1000xm5")
    best = best_row(listing_rows(ctx, p))
    assert best.condition == "new"
    renewed = next(r for r in listing_rows(ctx, p) if r.condition == "renewed")
    assert not renewed.eligible and "not counted" in renewed.note

    save_pref(session, "costco_member", False)
    session.commit()
    ctx2 = make_ctx(session, ctx.config, now=FIXED_NOW)
    costco = next(r for r in listing_rows(ctx2, p) if r.retailer == "costco")
    assert not costco.eligible and costco.note.startswith("Membership")


def test_manual_price_logging(ctx):
    p = add(ctx, "dewalt-dcd771c2")
    log_manual_price(ctx, p, "costco", 69.99, 0.0)
    ctx.session.commit()
    rows = listing_rows(ctx, p)
    costco = next(r for r in rows if r.retailer == "costco")
    assert not costco.placeholder and costco.total == 69.99 and costco.source == "manual"
    assert best_row(rows).retailer == "costco"


def test_snapshot_skips_products_already_checked_today(ctx, session):
    p = add(ctx, "ninja-af161")
    session.add(ListItem(product=p, quantity=1, target_price=500))
    session.commit()
    n = count_points(session)
    run = run_snapshot(ctx, log=lambda *_: None)
    assert run.products_skipped == 1 and run.products_checked == 0
    assert count_points(session) == n

    tomorrow = make_ctx(session, ctx.config, now=FIXED_NOW + timedelta(days=1))
    run2 = run_snapshot(tomorrow, log=lambda *_: None)
    assert run2.products_checked == 1
    assert count_points(session) == n + len(p.listings)   # exactly one new point per listing

    run3 = run_snapshot(tomorrow, log=lambda *_: None)    # hourly re-runs are free
    assert run3.products_checked == 0 and count_points(session) == n + len(p.listings)


def test_target_alert_fires_once_and_rearms(ctx, session):
    p = add(ctx, "ninja-af161")
    item = ListItem(product=p, quantity=1, target_price=1000)
    session.add(item)
    session.commit()
    run = run_snapshot(ctx, force=True, log=lambda *_: None)
    assert "alert" in run.log and item.alerted_at is not None
    run = run_snapshot(ctx, force=True, log=lambda *_: None)
    assert "alert" not in run.log                         # no repeat while still below
    item.target_price = 1
    session.commit()
    run_snapshot(ctx, force=True, log=lambda *_: None)
    assert item.alerted_at is None                        # re-armed


def test_list_view_totals(ctx, session):
    a = add(ctx, "tide-pods-112")
    b = add(ctx, "dewalt-dcd771c2")
    session.add_all([ListItem(product=a, quantity=2, target_price=1000), ListItem(product=b, quantity=1)])
    session.commit()
    view = list_view(ctx)
    best_a = best_row(listing_rows(ctx, a)).total
    best_b = best_row(listing_rows(ctx, b)).total
    assert view["total"] == round(best_a * 2 + best_b, 2)
    assert view["below_target"] == 1
    row = next(r for r in view["rows"] if r.product.id == a.id)
    assert "<path" in row.spark_svg and row.signal["kind"] in {"low", "below", "near", "above"}


def test_search_service_marks_existing(ctx):
    add(ctx, "vitamix-5200")
    out = search(ctx, "vitamix")
    assert out.results and out.existing.get(0)
