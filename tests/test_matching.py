from __future__ import annotations

import pytest

from pokehunter.matching import (
    CardRef,
    detect_condition,
    match_title,
    norm_number,
    rejection_reason,
)

BASE = CardRef(
    "base1-4",
    "Charizard",
    "Charizard",
    "4",
    "Base Set",
    102,
    {"firstEdition": 7000.0, "holo": 345.0},
)
BASE2 = CardRef("base4-4", "Charizard", "Charizard", "4", "Base Set 2", 130, {"holo": 200.0})
VIVID = CardRef(
    "swsh4-25", "Charizard", "Charizard", "025", "Vivid Voltage", 185, {"holo": 1.6, "reverse": 2.4}
)
PROMO = CardRef(
    "swshp-SWSH050",
    "Charizard",
    "Charizard V",
    "SWSH050",
    "SWSH Black Star Promos",
    0,
    {"holo": 4.8},
)
MEW = CardRef("swsh8-114", "Mew", "Mew V", "114", "Fusion Strike", 264, {"holo": 2.4})
TG = CardRef(
    "swsh11tg-TG03",
    "Charizard",
    "Charizard",
    "TG03",
    "Lost Origin Trainer Gallery",
    30,
    {"holo": 20.0},
)
CARDS = [BASE, BASE2, VIVID, PROMO, MEW, TG]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("004", "4"), ("TG03", "tg3"), ("SWSH050", "swsh50"), ("199", "199"), ("4_A", "4_a")],
)
def test_norm_number(raw, expected):
    assert norm_number(raw) == expected


@pytest.mark.parametrize(
    ("title", "condition"),
    [
        ("Charizard 4/102 Base Set Holo NM", "NM"),
        ("Charizard 4/102 Base Set Holo Near Mint", "NM"),
        ("Charizard 4/102 Lightly Played", "LP"),
        ("Charizard 4/102 NM-LP", "LP"),
        ("Charizard 4/102 Holo Heavily Played", "HP"),
        ("Charizard 4/102 Holo HP", "HP"),
        ("Charizard ex 330 HP 199/165", "?"),  # card hit points, not a condition
        ("Charizard 4/102 played condition", "MP"),
        ("Charizard 4/102 creased", "DMG"),
        ("Charizard 4/102 Base Set", "?"),
    ],
)
def test_condition(title, condition):
    assert detect_condition(title) == condition


@pytest.mark.parametrize(
    "title",
    [
        "PSA 9 Charizard 4/102 Base Set",
        "Japanese Charizard No.006 Base",
        "Charizard 4/102 Proxy Custom Card",
        "Pokemon Card Lot Charizard 4/102 bundle",
        "Choose your card Charizard",
        "Charizard 4/102 Gold Metal Card",
        "Charizard SK 146/144 CREDIT CARD SKIN",
        "Plastic Fan Art Pokemon TCG Shining Charizard 107/105 Neo Destiny Holo",
        "POKELOTTERIA - CHARIZARD 146/144 Skyridge ITA - READ DESCRIPTION",
        "Charizard 4/102 Base Set GER",
        "Pokemon 150/147 Charizard GX sp250 tarjeta carta Pokemon.España",
    ],
)
def test_rejections(title):
    assert rejection_reason(title)


@pytest.mark.parametrize(
    "title",
    [
        "Charizard 4/102 Base Set Holo Pack Fresh NM",
        "Charizard 199/165 Special Illustration Rare 151 Near Mint",
        "Charizard ex 223/197 Obsidian Flames SIR in toploader",
    ],
)
def test_real_listings_not_rejected(title):
    assert rejection_reason(title) is None


def test_tag_team_is_not_mistaken_for_graded():
    assert rejection_reason("Reshiram & Charizard GX Tag Team 20/214") is None


def test_base_set_holo_matches_unlimited():
    match, _ = match_title("Pokemon Charizard 4/102 Base Set Holo Rare LP", CARDS)
    assert match.card.id == "base1-4" and match.variant == "holo" and match.condition == "LP"


def test_holo_in_title_picks_holo_version():
    both = CardRef(
        "bw-mew", "Mew", "Mew-EX", "46", "Dragons Exalted", 124, {"normal": None, "holo": 30.0}
    )
    match, _ = match_title("Pokemon Mew EX Basic Holo Psychic 46/124", [both])
    assert match.variant == "holo"
    match, _ = match_title("Mew EX 46/124 non holo", [both])
    assert match.variant == "normal"


def test_first_edition_detected():
    match, _ = match_title("1st Edition Charizard 4/102 Base Set Holo", CARDS)
    assert match.variant == "firstEdition"


def test_reverse_holo_and_padded_number():
    match, _ = match_title("Charizard 025/185 Vivid Voltage Reverse Holo NM", CARDS)
    assert match.card.id == "swsh4-25" and match.variant == "reverse"


def test_denominator_separates_reprint_sets():
    match, _ = match_title("Charizard 4/130 Base Set 2 Holo", CARDS)
    assert match.card.id == "base4-4"


def test_celebrations_reprint_never_valued_as_original():
    match, reason = match_title("Charizard 4/102 Celebrations Classic Collection", CARDS)
    assert match is None


def test_promo_code():
    match, _ = match_title("Charizard V SWSH 050 Black Star Promo", CARDS)
    assert match.card.id == "swshp-SWSH050"


def test_trainer_gallery_number():
    match, _ = match_title("Charizard TG03/TG30 Lost Origin", CARDS)
    assert match.card.id == "swsh11tg-TG03"


def test_mewtwo_is_not_mew():
    match, _ = match_title("Mewtwo 114/264 holo", CARDS)
    assert match is None


def test_mew_matches():
    match, _ = match_title("Mew V 114/264 Fusion Strike Full Art", CARDS)
    assert match.card.id == "swsh8-114"


def test_multiple_numbers_skipped():
    match, reason = match_title("Charizard 4/102 and Charizard 4/130", CARDS)
    assert match is None and "several" in reason


def test_number_required():
    match, _ = match_title("Charizard Base Set Holo Rare", CARDS)
    assert match is None


def test_first_edition_on_card_without_it_is_rejected():
    match, _ = match_title("1st edition Charizard 025/185", CARDS)
    assert match is None
