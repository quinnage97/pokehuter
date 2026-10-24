from conftest import make_listing

from pokehunter.database import Database


def test_alert_state_is_persisted_and_deduplicated(tmp_path):
    database = Database(tmp_path / "state.sqlite3")
    database.initialize()
    listing = make_listing("item-1", "50")
    assert database.observe(listing) is False
    database.mark_alert_attempt(listing.item_id)
    database.mark_alert_sent(listing.item_id)
    assert database.observe(listing) is True


def test_failed_attempt_is_retryable(tmp_path):
    database = Database(tmp_path / "state.sqlite3")
    database.initialize()
    listing = make_listing("item-1", "50")
    database.observe(listing)
    database.mark_alert_attempt(listing.item_id, error="TelegramError")
    assert database.observe(listing) is False
