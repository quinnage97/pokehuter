from __future__ import annotations

import re
from functools import cached_property
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{32,80}$")


class Settings(BaseSettings):
    """Runtime configuration loaded exclusively from environment variables."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    ebay_client_id: str | None = None
    ebay_client_secret: SecretStr | None = None
    ebay_environment: str = "production"
    ebay_marketplace_id: str = "EBAY_GB"
    ebay_currency: str = "GBP"
    ebay_delivery_country: str = "GB"
    ebay_delivery_postcode: str | None = None
    ebay_category_id: str = "183454"
    ebay_search_queries: str = "Charizard pokemon card,Mew pokemon card"
    ebay_affiliate_campaign_id: str | None = None
    ebay_affiliate_reference_id: str | None = "pokehunter"
    ebay_deletion_endpoint: str | None = None
    ebay_verification_token: SecretStr | None = None

    telegram_bot_token: SecretStr | None = None
    telegram_chat_id: str | None = None

    database_path: Path = Path("var/pokehunter.sqlite3")
    log_level: str = "INFO"
    min_discount_percent: float = Field(default=20.0, ge=0, le=100)
    min_savings: float = Field(default=10.0, ge=0)
    min_bargain_score: float = Field(default=60.0, ge=0, le=100)
    min_comparables: int = Field(default=3, ge=2, le=50)
    title_similarity_threshold: float = Field(default=0.45, ge=0, le=1)
    require_english: bool = True
    ebay_result_limit: int = Field(default=100, ge=10, le=200)
    public_key_cache_seconds: int = Field(default=3600, ge=60, le=86400)

    @cached_property
    def search_queries(self) -> tuple[str, ...]:
        values = tuple(q.strip() for q in self.ebay_search_queries.split(",") if q.strip())
        if not values:
            raise ValueError("EBAY_SEARCH_QUERIES must contain at least one query")
        return values

    @property
    def ebay_api_base(self) -> str:
        if self.ebay_environment.lower() == "sandbox":
            return "https://api.sandbox.ebay.com"
        if self.ebay_environment.lower() != "production":
            raise ValueError("EBAY_ENVIRONMENT must be 'production' or 'sandbox'")
        return "https://api.ebay.com"

    def require_ebay_credentials(self) -> tuple[str, str]:
        if not self.ebay_client_id or not self.ebay_client_secret:
            raise ValueError("EBAY_CLIENT_ID and EBAY_CLIENT_SECRET are required")
        return self.ebay_client_id, self.ebay_client_secret.get_secret_value()

    def validate_browse_config(self) -> None:
        if self.ebay_affiliate_campaign_id and not re.fullmatch(
            r"\d{10}", self.ebay_affiliate_campaign_id
        ):
            raise ValueError("EBAY_AFFILIATE_CAMPAIGN_ID must be a 10-digit EPN campaign ID")
        if self.ebay_affiliate_reference_id and len(self.ebay_affiliate_reference_id) > 256:
            raise ValueError("EBAY_AFFILIATE_REFERENCE_ID cannot exceed 256 characters")
        if self.ebay_environment.lower() == "production":
            if not self.ebay_affiliate_campaign_id:
                raise ValueError(
                    "EBAY_AFFILIATE_CAMPAIGN_ID is required for production Browse API use"
                )
            if not self.ebay_delivery_postcode:
                raise ValueError("EBAY_DELIVERY_POSTCODE is required for production Browse API use")

    def require_telegram_credentials(self) -> tuple[str, str]:
        if not self.telegram_bot_token or not self.telegram_chat_id:
            raise ValueError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are required")
        return self.telegram_bot_token.get_secret_value(), self.telegram_chat_id

    def require_webhook_config(self) -> tuple[str, str]:
        if not self.ebay_deletion_endpoint or not self.ebay_deletion_endpoint.startswith(
            "https://"
        ):
            raise ValueError("EBAY_DELETION_ENDPOINT must be the public HTTPS webhook URL")
        if not self.ebay_verification_token:
            raise ValueError("EBAY_VERIFICATION_TOKEN is required")
        token = self.ebay_verification_token.get_secret_value()
        if not TOKEN_PATTERN.fullmatch(token):
            raise ValueError(
                "EBAY_VERIFICATION_TOKEN must be 32-80 letters, numbers, underscores or hyphens"
            )
        return self.ebay_deletion_endpoint, token
