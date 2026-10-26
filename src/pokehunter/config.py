from __future__ import annotations

from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Server configuration, read from environment variables or a .env file.

    Everything a parent might want to tweak day to day (discounts, conditions, alerts) lives
    in the website's Settings tab instead and is stored in the database.
    """

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    ebay_client_id: str | None = None
    ebay_client_secret: SecretStr | None = None
    ebay_marketplace_id: str = "EBAY_GB"
    ebay_currency: str = "GBP"
    ebay_delivery_country: str = "GB"
    ebay_delivery_postcode: str | None = None
    ebay_category_id: str = "183454"  # Pokémon CCG individual cards
    # eBay's default Browse allowance is 5,000 calls a day. Stay safely under it.
    ebay_daily_call_budget: int = Field(default=4500, ge=50)
    # Optional eBay Partner Network campaign, only if you have one.
    ebay_affiliate_campaign_id: str | None = None

    telegram_bot_token: SecretStr | None = None
    telegram_chat_id: str | None = None

    # Protects the website. Leave empty only if the site is not reachable from the internet.
    site_password: SecretStr | None = None
    public_url: str | None = None

    database_path: Path = Path("var/pokehunter.sqlite3")
    scan_interval_minutes: int = Field(default=15, ge=5, le=240)
    catalog_refresh_hours: int = Field(default=24, ge=1)
    tcgdex_base: str = "https://api.tcgdex.net/v2/en"
    pokemon: str = "Charizard,Mew"
    log_level: str = "INFO"

    # Used only if the live exchange-rate lookup fails.
    fallback_eur_gbp: float = 0.86
    fallback_usd_gbp: float = 0.76

    @property
    def pokemon_names(self) -> list[str]:
        return [name.strip() for name in self.pokemon.split(",") if name.strip()]

    @property
    def ebay_ready(self) -> bool:
        return bool(self.ebay_client_id and self.ebay_client_secret)

    @property
    def telegram_ready(self) -> bool:
        return bool(self.telegram_bot_token and self.telegram_chat_id)
