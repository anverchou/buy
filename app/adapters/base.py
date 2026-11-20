"""The adapter interface every price source implements.

    search(query)               -> list[ProductResult]
    get_current_prices(product) -> list[Offer]
    get_history(listing, since) -> list[HistoryPoint]

Adapters never touch the database; services decide what to store.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from datetime import date


class SourceError(Exception):
    """A source failed (network, auth, quota). The message is shown to the user."""


class BudgetExceeded(SourceError):
    pass


@dataclass
class ParsedQuery:
    raw: str
    kind: str = "text"            # text | upc | asin | url
    text: str = ""                # free-text form used for keyword search
    upc: str | None = None
    asin: str | None = None
    retailer: str | None = None   # retailer key when a URL was pasted
    retailer_item_id: str | None = None
    note: str | None = None       # shown to the user (e.g. short links)


@dataclass
class ProductResult:
    source: str
    source_ref: str
    name: str
    brand: str | None = None
    model: str | None = None
    variant: str | None = None
    upc: str | None = None
    image_url: str | None = None
    asin: str | None = None
    google_product_id: str | None = None
    price_hint: float | None = None
    store_hint: str | None = None

    def to_payload(self) -> dict:
        return asdict(self)

    @classmethod
    def from_payload(cls, data: dict) -> "ProductResult":
        allowed = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in data.items() if k in allowed})


@dataclass
class Offer:
    retailer: str                 # canonical key from retailers.RETAILERS
    price: float | None
    shipping: float | None
    in_stock: bool = True
    condition: str = "new"
    url: str | None = None
    requires_membership: bool = False
    external_id: str | None = None
    seller: str | None = None
    source: str = ""


@dataclass
class HistoryPoint:
    day: date
    price: float | None
    shipping: float | None = None
    in_stock: bool = True


@dataclass
class SourceStatus:
    name: str
    label: str
    configured: bool
    detail: str = ""
    extra: dict = field(default_factory=dict)


class PriceSource(ABC):
    name: str = "base"
    label: str = "Base"
    provides_history: bool = False

    @abstractmethod
    def search(self, query: ParsedQuery) -> list[ProductResult]:
        ...

    @abstractmethod
    def get_current_prices(self, product) -> list[Offer]:
        ...

    @abstractmethod
    def get_history(self, listing, since: date) -> list[HistoryPoint]:
        ...

    def handles(self, product) -> bool:
        """Whether this source can price the given product."""
        return False

    def status(self) -> SourceStatus:
        return SourceStatus(self.name, self.label, True)
