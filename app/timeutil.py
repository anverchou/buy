"""Time helpers. Timestamps are stored in UTC; days and display use APP_TIMEZONE."""
from __future__ import annotations

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo


def tz(name: str) -> ZoneInfo:
    return ZoneInfo(name)


def to_local(dt_utc: datetime | None, tzname: str) -> datetime | None:
    if dt_utc is None:
        return None
    if dt_utc.tzinfo is None:
        dt_utc = dt_utc.replace(tzinfo=timezone.utc)
    return dt_utc.astimezone(ZoneInfo(tzname))


def local_day(dt_utc: datetime, tzname: str) -> date:
    return to_local(dt_utc, tzname).date()


def short_date(d: date) -> str:
    return f"{d:%b} {d.day}"


def short_date_year(d: date, today: date) -> str:
    return short_date(d) if d.year == today.year else f"{d:%b} {d.day}, {d.year}"


def relative_time(dt_utc: datetime | None, now_utc: datetime, tzname: str) -> str:
    if dt_utc is None:
        return "never"
    delta = now_utc - dt_utc
    secs = delta.total_seconds()
    if secs < 90:
        return "just now"
    if secs < 3600:
        return f"{int(secs // 60)} min ago"
    if secs < 86400 and local_day(dt_utc, tzname) == local_day(now_utc, tzname):
        local = to_local(dt_utc, tzname)
        return f"today {local:%I:%M %p}".replace(" 0", " ").lower().replace("today", "Today")
    days = (local_day(now_utc, tzname) - local_day(dt_utc, tzname)).days
    if days == 1:
        return "yesterday"
    return f"{days} days ago"
