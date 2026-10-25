from conftest import make_listing

from pokehunter.market import grade_bucket, score_listing, title_similarity


def test_similarity_preserves_set_and_card_number_identity():
    left = "Pokemon Charizard Base Set 4/102 English Holo"
    right = "Charizard 4/102 Base Set Pokemon Card English"
    wrong = "Charizard VMAX 020/189 Darkness Ablaze English"
    assert title_similarity(left, right) > title_similarity(left, wrong)


def test_grade_bucket_separates_graded_and_raw():
    assert grade_bucket("Charizard PSA 10") == "psa-10"
    assert grade_bucket("Charizard ungraded") == "ungraded"


def test_scores_clear_bargain_against_similar_comparables():
    cheap = make_listing("cheap", "50")
    pool = [cheap] + [
        make_listing(str(index), price) for index, price in enumerate(("90", "100", "110", "120"))
    ]
    bargain = score_listing(cheap, pool, min_comparables=3, similarity_threshold=0.45)
    assert bargain is not None
    assert bargain.market_price == 105
    assert bargain.estimated_savings == 55
    assert bargain.discount_percent == 52.4
    assert bargain.score >= 60


def test_requires_enough_comparables():
    cheap = make_listing("cheap", "50")
    pool = [cheap, make_listing("other", "100")]
    assert score_listing(cheap, pool, min_comparables=3, similarity_threshold=0.45) is None
