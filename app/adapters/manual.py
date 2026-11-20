"""Manual adapter: prices you saw yourself (in store, in an email, on a site with no API).

It never fetches anything. Entries are written through services.pricing.log_manual_price
and stored as ordinary PricePoints with source="manual", so they appear in the comparison
table and history chart like any other listing.
"""
from __future__ import annotations

from datetime import date

from .base import HistoryPoint, Offer, ParsedQuery, PriceSource, ProductResult, SourceStatus


class ManualAdapter(PriceSource):
    name = "manual"
    label = "Manual entries"

    def search(self, query: ParsedQuery) -> list[ProductResult]:
        return []

    def get_current_prices(self, product) -> list[Offer]:
        return []

    def get_history(self, listing, since: date) -> list[HistoryPoint]:
        return []

    def handles(self, product) -> bool:
        return False

    def status(self) -> SourceStatus:
        return SourceStatus(self.name, self.label, True, "Use \"Log a price\" on any product")
