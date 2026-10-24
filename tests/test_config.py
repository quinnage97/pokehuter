import pytest

from pokehunter.config import Settings


def test_webhook_config_requires_exact_https_url():
    settings = Settings(
        _env_file=None,
        ebay_deletion_endpoint="http://localhost/hook",
        ebay_verification_token="a" * 32,
    )
    with pytest.raises(ValueError, match="HTTPS"):
        settings.require_webhook_config()


def test_webhook_token_rejects_invalid_characters():
    settings = Settings(
        _env_file=None,
        ebay_deletion_endpoint="https://example.com/hook",
        ebay_verification_token="not allowed spaces in this token 123456789",
    )
    with pytest.raises(ValueError, match="32-80"):
        settings.require_webhook_config()


def test_queries_are_trimmed():
    settings = Settings(_env_file=None, ebay_search_queries=" Charizard , Mew ")
    assert settings.search_queries == ("Charizard", "Mew")
