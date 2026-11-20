"""Price-Tracked Shopping List - Flask app factory."""
from __future__ import annotations

import hmac
import secrets

import click
from dotenv import load_dotenv
from flask import Flask, abort, redirect, request, session, url_for

from .config import BASE_DIR, effective_mode, load_config
from .db import Session, init_engine


def create_app(test_config: dict | None = None) -> Flask:
    load_dotenv(BASE_DIR / ".env")
    app = Flask(__name__, instance_path=str(BASE_DIR / "instance"))
    app.config.from_mapping(load_config())
    if test_config:
        app.config.update(test_config)

    init_engine(app.config["DATABASE_URL"])

    @app.teardown_appcontext
    def _remove_session(_exc=None):
        Session.remove()

    from . import filters
    filters.register(app)

    from .routes import bp
    app.register_blueprint(bp)

    _install_security(app)
    _register_cli(app)

    if app.config.get("AUTO_SEED_DEMO") and not app.config.get("TESTING") \
            and effective_mode(app.config) == "mock":
        with app.app_context():
            from .models import Product
            from .services.context import make_ctx
            from .services.demo import seed_demo
            s = Session()
            if s.query(Product.id).first() is None:
                seed_demo(make_ctx(s, app.config))
    return app


def _install_security(app: Flask) -> None:
    open_endpoints = {"main.login", "static", "main.healthz", "main.demo_image"}

    @app.before_request
    def _guard():
        if app.config.get("APP_PASSWORD") and request.endpoint not in open_endpoints \
                and not session.get("auth"):
            return redirect(url_for("main.login", next=request.full_path))
        if request.method == "POST" and not app.config.get("WTF_CSRF_DISABLED"):
            token = session.get("csrf")
            sent = request.form.get("csrf", "")
            if not token or not hmac.compare_digest(token, sent):
                abort(400, "Form expired - go back, reload the page and try again.")

    @app.context_processor
    def _csrf():
        if "csrf" not in session:
            session["csrf"] = secrets.token_urlsafe(24)
        return {"csrf_token": session["csrf"]}


def _register_cli(app: Flask) -> None:
    @app.cli.command("snapshot")
    @click.option("--force", is_flag=True, help="Re-check products already checked today.")
    @click.option("--all", "include_all", is_flag=True, help="Include products not on the active list.")
    def snapshot_cmd(force: bool, include_all: bool):
        """Record today's price for every tracked listing (run daily, or hourly - it's idempotent)."""
        from .services.context import make_ctx
        from .services.snapshot import run_snapshot
        run = run_snapshot(make_ctx(Session(), app.config), force=force, include_all=include_all,
                           log=click.echo)
        raise SystemExit(1 if run.errors and not run.products_checked else 0)

    @app.cli.command("seed-demo")
    def seed_demo_cmd():
        """Add the demo shopping list (mock data)."""
        from .services.context import make_ctx
        from .services.demo import seed_demo
        n = seed_demo(make_ctx(Session(), app.config))
        click.echo(f"Added {n} demo items." if n else "Demo data already present.")

    @app.cli.command("remove-demo")
    def remove_demo_cmd():
        """Delete all demo (mock) products and their list items."""
        from .services.demo import remove_demo
        click.echo(f"Removed {remove_demo(Session())} demo products.")
