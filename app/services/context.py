"""Everything a service call needs, bundled so tests can pin the clock."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone

from sqlalchemy import select

from ..adapters import SourceSet, build_sources
from ..models import Setting
from ..timeutil import local_day
from .usage import UsageCounter

DEFAULT_SETTINGS = {
    "costco_member": "1",      # count membership-only listings toward "best price"
    "include_nonnew": "0",     # count renewed/used listings toward "best price"
}


@dataclass
class Prefs:
    costco_member: bool = True
    include_nonnew: bool = False

    def is_member(self, retailer: str) -> bool:
        return self.costco_member if retailer == "costco" else True


def load_prefs(session) -> Prefs:
    rows = {s.key: s.value for s in session.scalars(select(Setting))}
    get = lambda k: (rows.get(k, DEFAULT_SETTINGS[k]) == "1")  # noqa: E731
    return Prefs(costco_member=get("costco_member"), include_nonnew=get("include_nonnew"))


def save_pref(session, key: str, value: bool) -> None:
    row = session.get(Setting, key)
    if row is None:
        session.add(Setting(key=key, value="1" if value else "0"))
    else:
        row.value = "1" if value else "0"


@dataclass
class Ctx:
    session: object
    config: dict
    now: datetime                     # UTC, naive
    sources: SourceSet = None
    prefs: Prefs = field(default_factory=Prefs)

    @property
    def tzname(self) -> str:
        return self.config.get("APP_TIMEZONE", "America/Los_Angeles")

    @property
    def today(self) -> date:
        return local_day(self.now, self.tzname)

    @property
    def stale_days(self) -> int:
        return int(self.config.get("STALE_AFTER_DAYS", 7))


def make_ctx(session, config: dict, now: datetime | None = None) -> Ctx:
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    usage = UsageCounter(session, now_fn=lambda: now.replace(tzinfo=timezone.utc))
    tzname = config.get("APP_TIMEZONE", "America/Los_Angeles")
    sources = build_sources(config, usage=usage, today_fn=lambda: local_day(now, tzname))
    return Ctx(session=session, config=config, now=now, sources=sources, prefs=load_prefs(session))
