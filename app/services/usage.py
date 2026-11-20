"""Per-month API usage counter, so the app can stay inside a plan's quota."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from ..models import ApiUsage


class UsageCounter:
    def __init__(self, session, now_fn=None):
        self.session = session
        self._now = now_fn or (lambda: datetime.now(timezone.utc))

    def _month(self) -> str:
        return self._now().strftime("%Y-%m")

    def _row(self, source: str) -> ApiUsage:
        month = self._month()
        row = self.session.scalar(select(ApiUsage).where(ApiUsage.source == source, ApiUsage.month == month))
        if row is None:
            row = ApiUsage(source=source, month=month, calls=0)
            self.session.add(row)
            self.session.flush()
        return row

    def used(self, source: str) -> int:
        row = self.session.scalar(select(ApiUsage).where(ApiUsage.source == source,
                                                         ApiUsage.month == self._month()))
        return row.calls if row else 0

    def add(self, source: str, n: int = 1) -> None:
        row = self._row(source)
        row.calls += n
        self.session.flush()
