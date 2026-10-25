from __future__ import annotations

from decimal import Decimal

import pytest

from pokehunter.config import Settings
from pokehunter.models import Listing


@pytest.fixture
def settings(tmp_path):
    return Settings(
        _env_file=None,
        ebay_client_id="client-id",
        ebay_client_secret="client-secret",
        ebay_deletion_endpoint="https://cards.example/webhooks/ebay/account-deletion",
        ebay_verification_token="a_valid_verification_token_123456",
        telegram_bot_token="telegram-token",
        telegram_chat_id="12345",
        database_path=tmp_path / "state.sqlite3",
    )


def make_listing(
    item_id: str,
    price: str,
    *,
    title: str = "Pokemon Charizard Base Set 4/102 English Holo",
    shipping: str = "0",
    currency: str = "GBP",
    aspects: dict[str, tuple[str, ...]] | None = None,
) -> Listing:
    return Listing(
        item_id=item_id,
        title=title,
        url=f"https://www.ebay.co.uk/itm/{item_id}",
        item_price=Decimal(price),
        shipping_price=Decimal(shipping),
        currency=currency,
        buying_options=("FIXED_PRICE",),
        condition="Ungraded",
        aspects=aspects or {"language": ("English",)},
    )
