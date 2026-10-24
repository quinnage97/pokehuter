from __future__ import annotations

import math
import re
import statistics
from decimal import ROUND_HALF_UP, Decimal

from .models import Bargain, Listing

WORDS = re.compile(r"[a-z0-9]+(?:/[a-z0-9]+)?", re.IGNORECASE)
GRADE = re.compile(r"\b(psa|cgc|bgs|ace)\s*([1-9](?:\.5)?|10)\b", re.IGNORECASE)
STOP_WORDS = {
    "a",
    "and",
    "card",
    "cards",
    "charizard",
    "english",
    "for",
    "holo",
    "mint",
    "near",
    "nm",
    "of",
    "pokemon",
    "rare",
    "the",
    "tcg",
    "uk",
}


def identity_tokens(title: str) -> set[str]:
    return {
        token.casefold() for token in WORDS.findall(title) if token.casefold() not in STOP_WORDS
    }


def grade_bucket(title: str) -> str:
    match = GRADE.search(title)
    return f"{match.group(1).casefold()}-{match.group(2)}" if match else "ungraded"


def title_similarity(left: str, right: str) -> float:
    left_tokens, right_tokens = identity_tokens(left), identity_tokens(right)
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def score_listing(
    listing: Listing,
    pool: list[Listing],
    *,
    min_comparables: int,
    similarity_threshold: float,
) -> Bargain | None:
    comparable = [
        candidate
        for candidate in pool
        if candidate.item_id != listing.item_id
        and candidate.currency == listing.currency
        and grade_bucket(candidate.title) == grade_bucket(listing.title)
        and title_similarity(candidate.title, listing.title) >= similarity_threshold
    ]
    if len(comparable) < min_comparables:
        return None

    prices = [candidate.total_price for candidate in comparable]
    market = Decimal(str(statistics.median(prices))).quantize(Decimal("0.01"))
    if market <= 0 or listing.total_price >= market:
        return None
    savings = (market - listing.total_price).quantize(Decimal("0.01"))
    discount = float((savings / market) * 100)

    discount_component = min(discount / 40.0, 1.0) * 70
    confidence_component = min(len(comparable) / 10.0, 1.0) * 20
    cheaper_count = sum(price >= listing.total_price for price in prices)
    percentile_component = cheaper_count / len(prices) * 10
    score = Decimal(str(discount_component + confidence_component + percentile_component))
    score = float(score.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))
    if not math.isfinite(score):
        return None
    return Bargain(
        listing=listing,
        market_price=market,
        estimated_savings=savings,
        discount_percent=round(discount, 1),
        score=min(score, 100.0),
        comparable_count=len(comparable),
    )
