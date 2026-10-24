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


@pytest.mark.parametrize("campaign", ["123", "abcdefghij", "12345678901"])
def test_affiliate_campaign_id_must_be_ten_digits(campaign):
    settings = Settings(_env_file=None, ebay_affiliate_campaign_id=campaign)
    with pytest.raises(ValueError, match="10-digit"):
        settings.validate_browse_config()


def test_affiliate_reference_id_has_bounded_length():
    settings = Settings(_env_file=None, ebay_affiliate_reference_id="x" * 257)
    with pytest.raises(ValueError, match="256"):
        settings.validate_browse_config()


def test_production_browse_requires_epn_campaign():
    settings = Settings(_env_file=None, ebay_environment="production")
    with pytest.raises(ValueError, match="required for production"):
        settings.validate_browse_config()


def test_production_browse_requires_delivery_postcode():
    settings = Settings(
        _env_file=None,
        ebay_environment="production",
        ebay_affiliate_campaign_id="1234567890",
    )
    with pytest.raises(ValueError, match="DELIVERY_POSTCODE"):
        settings.validate_browse_config()
