"""Adapter registry: builds the set of price sources from configuration.

To add a source, implement adapters.base.PriceSource and register it in build_sources().
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from ..config import effective_mode
from .base import PriceSource
from .keepa import KeepaAdapter
from .manual import ManualAdapter
from .mock import MockAdapter
from .serpapi import SerpApiAdapter


@dataclass
class SourceSet:
    mode: str
    sources: dict[str, PriceSource]
    search_order: list[str]
    price_order: list[str]

    def searchers(self) -> list[PriceSource]:
        return [self.sources[n] for n in self.search_order if n in self.sources]

    def pricers(self) -> list[PriceSource]:
        return [self.sources[n] for n in self.price_order if n in self.sources]

    def get(self, name: str) -> PriceSource | None:
        return self.sources.get(name)


def build_sources(config: dict, usage=None, today_fn=None) -> SourceSet:
    mode = effective_mode(config)
    today_fn = today_fn or date.today
    sources: dict[str, PriceSource] = {
        # The mock adapter always prices demo products, even in live mode.
        "mock": MockAdapter(seed=config.get("MOCK_SEED", 42), today_fn=today_fn),
        "manual": ManualAdapter(),
    }
    retry = config.get("HTTP_RETRY_DELAY", 2.0)
    if config.get("SERPAPI_API_KEY"):
        sources["serpapi"] = SerpApiAdapter(config["SERPAPI_API_KEY"], usage=usage,
                                            monthly_limit=config.get("SERPAPI_MONTHLY_LIMIT", 250),
                                            retry_delay=retry, today_fn=today_fn)
    if config.get("KEEPA_API_KEY"):
        sources["keepa"] = KeepaAdapter(config["KEEPA_API_KEY"], domain=config.get("KEEPA_DOMAIN", 1),
                                        tzname=config.get("APP_TIMEZONE", "America/Los_Angeles"),
                                        usage=usage, allow_text_search="serpapi" not in sources,
                                        retry_delay=retry, today_fn=today_fn)
    search_order = ["mock"] if mode == "mock" else [n for n in ("serpapi", "keepa") if n in sources]
    # Keepa runs last so its Amazon price wins over Google's copy of the Amazon offer.
    price_order = ["mock", "serpapi", "keepa"]
    return SourceSet(mode=mode, sources=sources, search_order=search_order, price_order=price_order)
