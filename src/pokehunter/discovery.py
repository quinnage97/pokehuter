from __future__ import annotations

import re

from .models import Listing

TARGET_PATTERN = re.compile(r"\b(charizard|mew)\b", re.IGNORECASE)
EXCLUDED_PATTERN = re.compile(
    r"\b(proxy|replica|reprint|custom|digital|orica|fan[ -]?made|metal card|mystery|"
    r"bundle|job lot|collection)\b",
    re.IGNORECASE,
)
NON_ENGLISH_PATTERN = re.compile(
    r"\b(japanese|japan|german|french|spanish|italian|korean|chinese|dutch|"
    r"portuguese|indonesian|thai)\b",
    re.IGNORECASE,
)
ENGLISH_PATTERN = re.compile(r"\benglish\b", re.IGNORECASE)


def is_target_listing(listing: Listing, *, currency: str, require_english: bool) -> bool:
    if listing.currency != currency:
        return False
    if not TARGET_PATTERN.search(listing.title) or EXCLUDED_PATTERN.search(listing.title):
        return False
    if not require_english:
        return True
    language_values = listing.aspects.get("language", ())
    if language_values:
        return any(value.casefold() == "english" for value in language_values)
    if NON_ENGLISH_PATTERN.search(listing.title):
        return False
    return bool(ENGLISH_PATTERN.search(listing.title))
