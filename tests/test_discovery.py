from conftest import make_listing

from pokehunter.discovery import is_target_listing


def test_accepts_english_target_from_aspect():
    listing = make_listing("1", "50", title="Charizard Base Set 4/102")
    assert is_target_listing(listing, currency="GBP", require_english=True)


def test_rejects_non_english_and_replica_cards():
    japanese = make_listing(
        "1",
        "50",
        title="Japanese Mew Pokemon card",
        aspects={"language": ("Japanese",)},
    )
    replica = make_listing("2", "10", title="English Charizard replica Pokemon card")
    lot = make_listing("3", "10", title="English Charizard lot of 10 Pokemon cards")
    assert not is_target_listing(japanese, currency="GBP", require_english=True)
    assert not is_target_listing(replica, currency="GBP", require_english=True)
    assert not is_target_listing(lot, currency="GBP", require_english=True)


def test_strict_mode_rejects_unknown_language():
    listing = make_listing(
        "1",
        "50",
        title="Mew 151 Pokemon card",
        aspects={"set": ("151",)},
    )
    assert not is_target_listing(listing, currency="GBP", require_english=True)
