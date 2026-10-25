from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class Listing:
    item_id: str
    title: str
    url: str
    item_price: Decimal
    shipping_price: Decimal
    currency: str
    buying_options: tuple[str, ...]
    condition: str | None
    aspects: dict[str, tuple[str, ...]]

    @property
    def total_price(self) -> Decimal:
        return self.item_price + self.shipping_price


@dataclass(frozen=True, slots=True)
class Bargain:
    listing: Listing
    market_price: Decimal
    estimated_savings: Decimal
    discount_percent: float
    score: float
    comparable_count: int
