"""Jinja filters for money, percentages and local dates."""
from __future__ import annotations

from datetime import date, datetime

from flask import current_app

from .timeutil import short_date_year, to_local


def money(value, dash: str = "—") -> str:
    if value is None:
        return dash
    return f"${value:,.2f}"


def shipping(value) -> str:
    if value is None:
        return "Not listed"
    return "Free" if value == 0 else f"+ ${value:,.2f}"


def pct(value, signed: bool = True) -> str:
    if value is None:
        return "—"
    if signed:
        return f"{value:+.0f}%".replace("-", "−")
    return f"{value:.0f}%"


def local_dt(value: datetime | None, fmt: str = "%b %-d, %Y %-I:%M %p") -> str:
    if value is None:
        return "—"
    tzname = current_app.config.get("APP_TIMEZONE", "America/Los_Angeles")
    local = to_local(value, tzname)
    try:
        return local.strftime(fmt)
    except ValueError:  # Windows has no %-d
        return local.strftime(fmt.replace("%-", "%#"))


def nice_date(value) -> str:
    if value is None:
        return "—"
    if isinstance(value, datetime):
        tzname = current_app.config.get("APP_TIMEZONE", "America/Los_Angeles")
        value = to_local(value, tzname).date()
    return short_date_year(value, date.today())


def register(app) -> None:
    app.jinja_env.filters.update(money=money, shipping=shipping, pct=pct, local_dt=local_dt,
                                 nice_date=nice_date)
