"""HTTP routes."""
from __future__ import annotations

import hmac
import json
import re
from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

from flask import (Blueprint, Response, abort, current_app, flash, redirect, render_template, request,
                   session, url_for)
from sqlalchemy import select

from .adapters.mock import placeholder_svg
from .config import effective_mode
from .db import Session
from .models import ListItem, Product, SnapshotRun
from .retailers import CONDITION_LABELS, RETAILERS, retailer_name
from .services.catalog import search as run_search
from .services.catalog import select_result
from .services.context import Ctx, make_ctx, save_pref
from .services.demo import remove_demo, seed_demo
from .services.history import WINDOWS, history_payload
from .services.listview import build_list_row, list_view, load_items
from .services.pricing import best_row, listing_rows, log_manual_price, refresh_product
from .services.snapshot import run_snapshot

bp = Blueprint("main", __name__)


def ctx() -> Ctx:
    return make_ctx(Session(), current_app.config)


def parse_amount(raw: str | None) -> float | None:
    if raw is None:
        return None
    s = re.sub(r"[,$\s]", "", raw)
    if not s:
        return None
    try:
        value = float(s)
    except ValueError:
        return None
    return round(value, 2) if value >= 0 else None


def parse_int(raw: str | None, default: int, lo: int, hi: int) -> int:
    try:
        return max(lo, min(hi, int(raw)))
    except (TypeError, ValueError):
        return default


def safe_next(default: str) -> str:
    nxt = request.form.get("next") or request.args.get("next") or ""
    return nxt if nxt.startswith("/") and not nxt.startswith("//") else default


def report_flash(report) -> None:
    for err in report.errors:
        flash(err, "error")


@bp.app_context_processor
def _globals():
    cfg = current_app.config
    mode = effective_mode(cfg)
    last = Session().scalar(select(SnapshotRun).order_by(SnapshotRun.id.desc()).limit(1))
    return {"data_mode": mode, "last_run": last, "auth_enabled": bool(cfg.get("APP_PASSWORD")),
            "retailers": RETAILERS, "condition_labels": CONDITION_LABELS}


# -- list ------------------------------------------------------------------
@bp.get("/")
def shopping_list():
    c = ctx()
    view = list_view(c)
    return render_template("list.html", view=view, today=c.today)


@bp.post("/list/add")
def list_add():
    c = ctx()
    product = c.session.get(Product, parse_int(request.form.get("product_id"), 0, 0, 10**9)) or abort(404)
    item = c.session.scalar(select(ListItem).where(ListItem.product_id == product.id,
                                                   ListItem.purchased.is_(False)))
    if item is None:
        item = ListItem(product=product)
        c.session.add(item)
        flash("Added to your list.", "ok")
    else:
        flash("Updated the item on your list.", "ok")
    _apply_item_form(item)
    c.session.commit()
    return redirect(safe_next(url_for("main.product_page", pid=product.id)))


def _apply_item_form(item: ListItem) -> None:
    f = request.form
    item.quantity = parse_int(f.get("quantity"), item.quantity or 1, 1, 999)
    if "target_price" in f:
        item.target_price = parse_amount(f.get("target_price"))
        item.alerted_at = None
    item.priority = parse_int(f.get("priority"), item.priority or 2, 1, 3)
    if "notes" in f:
        item.notes = (f.get("notes") or "").strip()[:2000]


@bp.post("/list/<int:iid>/update")
def list_update(iid: int):
    c = ctx()
    item = c.session.get(ListItem, iid) or abort(404)
    _apply_item_form(item)
    c.session.commit()
    flash("Saved.", "ok")
    return redirect(safe_next(url_for("main.shopping_list")))


@bp.post("/list/<int:iid>/purchase")
def list_purchase(iid: int):
    c = ctx()
    item = c.session.get(ListItem, iid) or abort(404)
    row = build_list_row(c, item)
    item.purchased = True
    paid_on = request.form.get("purchased_on")
    try:
        day = date.fromisoformat(paid_on) if paid_on else c.today
    except ValueError:
        day = c.today
    when = c.now if day == c.today else datetime.combine(day, time(12), tzinfo=ZoneInfo(c.tzname)) \
        .astimezone(timezone.utc).replace(tzinfo=None)
    item.purchased_at = when
    item.paid_price = parse_amount(request.form.get("paid_price"))
    if item.paid_price is None and row.best:
        item.paid_price = row.best.total
    item.paid_retailer = (request.form.get("paid_retailer") or "").strip()[:120] or \
        (row.best.retailer_label if row.best else None)
    c.session.commit()
    flash(f"Checked off {item.product.name}. It's in Past purchases.", "ok")
    return redirect(safe_next(url_for("main.shopping_list")))


@bp.post("/list/<int:iid>/restore")
def list_restore(iid: int):
    c = ctx()
    item = c.session.get(ListItem, iid) or abort(404)
    item.purchased, item.purchased_at, item.paid_price, item.paid_retailer = False, None, None, None
    c.session.commit()
    flash("Moved back to your list.", "ok")
    return redirect(safe_next(url_for("main.archive")))


@bp.post("/list/<int:iid>/delete")
def list_delete(iid: int):
    c = ctx()
    item = c.session.get(ListItem, iid) or abort(404)
    c.session.delete(item)
    c.session.commit()
    flash("Removed.", "ok")
    return redirect(safe_next(url_for("main.shopping_list")))


@bp.get("/archive")
def archive():
    c = ctx()
    items = load_items(c.session, purchased=True)
    spent = sum((i.paid_price or 0) * i.quantity for i in items)
    return render_template("archive.html", items=items, spent=spent)


# -- search & products -----------------------------------------------------
@bp.get("/search")
def search_page():
    c = ctx()
    q = (request.args.get("q") or "").strip()
    outcome = run_search(c, q) if q else None
    c.session.commit()  # API usage counters
    payloads = [json.dumps(r.to_payload()) for r in outcome.results] if outcome else []
    return render_template("search.html", q=q, outcome=outcome, payloads=payloads)


@bp.post("/products/select")
def select_product():
    c = ctx()
    try:
        payload = json.loads(request.form.get("payload") or "{}")
    except ValueError:
        abort(400)
    if not payload.get("source") or not payload.get("name"):
        abort(400)
    product = select_result(c, payload)
    if not product.listings:
        report_flash(refresh_product(c, product))
    c.session.commit()
    return redirect(url_for("main.product_page", pid=product.id))


@bp.get("/products/<int:pid>")
def product_page(pid: int):
    c = ctx()
    product = c.session.get(Product, pid) or abort(404)
    rows = listing_rows(c, product)
    history = history_payload(c, product, rows)
    item = c.session.scalar(select(ListItem).where(ListItem.product_id == pid, ListItem.purchased.is_(False)))
    return render_template("product.html", product=product, rows=rows, best=best_row(rows),
                           history=history, windows=WINDOWS, item=item, today=c.today,
                           prefs=c.prefs, retailer_choices=[(k, retailer_name(k, product.brand))
                                                            for k in RETAILERS])


@bp.post("/products/<int:pid>/refresh")
def product_refresh(pid: int):
    c = ctx()
    product = c.session.get(Product, pid) or abort(404)
    report = refresh_product(c, product)
    c.session.commit()
    report_flash(report)
    if report.sources_ok:
        flash(f"Checked {report.points} listing{'s' if report.points != 1 else ''}.", "ok")
    return redirect(url_for("main.product_page", pid=pid))


@bp.post("/products/<int:pid>/log-price")
def product_log_price(pid: int):
    c = ctx()
    product = c.session.get(Product, pid) or abort(404)
    retailer = request.form.get("retailer", "")
    price = parse_amount(request.form.get("price"))
    if retailer not in RETAILERS or price is None:
        flash("Enter a retailer and a price.", "error")
        return redirect(url_for("main.product_page", pid=pid) + "#compare")
    condition = request.form.get("condition", "new")
    condition = condition if condition in CONDITION_LABELS else "new"
    url = (request.form.get("url") or "").strip()
    if url and not re.match(r"^https?://", url):
        url = ""
    try:
        day = date.fromisoformat(request.form.get("day") or c.today.isoformat())
    except ValueError:
        day = c.today
    log_manual_price(c, product, retailer, price, parse_amount(request.form.get("shipping")),
                     in_stock=request.form.get("in_stock", "1") == "1", condition=condition,
                     url=url or None, day=min(day, c.today))
    c.session.commit()
    flash(f"Logged {retailer_name(retailer, product.brand)} at ${price:,.2f}.", "ok")
    return redirect(url_for("main.product_page", pid=pid) + "#compare")


@bp.get("/img/demo/<key>.svg")
def demo_image(key: str):
    svg = placeholder_svg(key) or abort(404)
    return Response(svg, mimetype="image/svg+xml", headers={"Cache-Control": "public, max-age=86400"})


# -- settings --------------------------------------------------------------
@bp.get("/settings")
def settings():
    c = ctx()
    statuses = [s.status() for s in c.sources.sources.values()]
    runs = list(c.session.scalars(select(SnapshotRun).order_by(SnapshotRun.id.desc()).limit(5)))
    demo_count = len(list(c.session.scalars(select(Product.id).where(Product.source == "mock"))))
    return render_template("settings.html", prefs=c.prefs, statuses=statuses, runs=runs,
                           demo_count=demo_count, mode=c.sources.mode, cfg=current_app.config)


@bp.post("/settings")
def settings_save():
    s = Session()
    save_pref(s, "costco_member", request.form.get("costco_member") == "1")
    save_pref(s, "include_nonnew", request.form.get("include_nonnew") == "1")
    s.commit()
    flash("Settings saved.", "ok")
    return redirect(url_for("main.settings"))


@bp.post("/settings/run-snapshot")
def settings_run_snapshot():
    c = ctx()
    lines = []
    run = run_snapshot(c, force=request.form.get("force") == "1", log=lines.append)
    flash(f"Snapshot: {run.products_checked} checked, {run.products_skipped} already done today, "
          f"{run.points_written} prices recorded, {run.errors} errors.", "error" if run.errors else "ok")
    return redirect(url_for("main.settings"))


@bp.post("/settings/remove-demo")
def settings_remove_demo():
    n = remove_demo(Session())
    flash(f"Removed {n} demo products.", "ok")
    return redirect(url_for("main.settings"))


@bp.post("/settings/load-demo")
def settings_load_demo():
    n = seed_demo(ctx())
    flash(f"Added {n} demo items." if n else "Demo data is already loaded.", "ok")
    return redirect(url_for("main.shopping_list"))


# -- auth & health ---------------------------------------------------------
@bp.route("/login", methods=["GET", "POST"])
def login():
    password = current_app.config.get("APP_PASSWORD")
    if not password:
        return redirect(url_for("main.shopping_list"))
    error = None
    if request.method == "POST":
        if hmac.compare_digest(request.form.get("password", ""), password):
            session["auth"] = True
            session.permanent = True
            nxt = request.form.get("next") or "/"
            return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else "/")
        error = "That password didn't match."
    return render_template("login.html", error=error, next=request.args.get("next", "/"))


@bp.post("/logout")
def logout():
    session.pop("auth", None)
    return redirect(url_for("main.login"))


@bp.get("/healthz")
def healthz():
    return {"ok": True}
