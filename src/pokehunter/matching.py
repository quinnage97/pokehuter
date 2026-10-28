"""Work out which card (and which variant) an eBay title is, and what condition it is in.

The guiding rule is *precision over recall*: when a title is ambiguous we either skip it or
assume the cheaper interpretation, so we never alert on a "deal" that is really a different,
cheaper card.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

CONDITION_FACTOR = {"NM": 1.0, "LP": 0.85, "MP": 0.7, "HP": 0.55, "DMG": 0.4, "?": 0.8}
CONDITION_LABEL = {
    "NM": "Near Mint",
    "LP": "Lightly Played",
    "MP": "Moderately Played",
    "HP": "Heavily Played",
    "DMG": "Damaged",
    "?": "Not stated",
}
VARIANT_LABEL = {
    "firstEdition": "1st Edition",
    "holo": "Holo",
    "normal": "Normal",
    "reverse": "Reverse Holo",
}

# Checked worst-first so "NM-LP" or "LP/MP" is treated as the lower grade.
_CONDITIONS = [
    ("DMG", r"\b(dmg|damaged|damage|poor|creased?|water ?damaged?|torn|ripped)\b"),
    ("HP", r"(?<!\d)(?<!\d )\b(hp|heavily[ -]played|heavy[ -]play(ed)?)\b"),
    (
        "MP",
        r"\b(mp|moderately[ -]played|moderate[ -]play|good condition)\b"
        r"|(?<!lightly )(?<!lightly-)(?<!light )(?<!heavily )\bplayed\b",
    ),
    ("LP", r"\b(lp|lightly[ -]played|light[ -]play(ed)?|excellent|exc|ex/nm|vg)\b"),
    ("NM", r"\b(nm|near[ -]mint|mint|nm[-/]m|m[-/]nm|pack[ -]fresh)\b"),
]
_CONDITIONS = [(code, re.compile(rx, re.I)) for code, rx in _CONDITIONS]

_GRADED = re.compile(r"\b(psa|bgs|cgc|sgc|beckett|graded|slab|slabbed)\b|\bace\s*\d", re.I)
_EXCLUDED = re.compile(
    r"\b(proxy|replica|custom|fan[ -]?made|fake|orica|metal|gold (?:metal|foil|plated)"
    r"|digital|code card|tcg ?live|online code|jumbo|oversized?|lots?|bundle|job ?lot|binder"
    r"|sleeves?|deck ?box|playmat|art ?print|poster|sticker|figure|plush|pin badge|coin"
    r"|acrylic|framed?|keychain|key ?ring|choose|pick|select|you choose|empty|booster"
    r"|blister|tin|etb|elite trainer|collection box|mystery|repack|bulk|set of|x\d+"
    # Non-card items and lucky dips that use a real card's name and number in the title.
    r"|skins?|vinyl|decals?|fan ?art|plastic|novelty|hand ?made|inspired|tribute|unofficial"
    r"|not (?:real|original|genuine)|lotter(?:y|ia)|raffle|giveaway|packs|god pack"
    r"|pack opening|pacchett\w*|read description|printed|art card|canvas|magnets?|patch"
    r"|phone case|display (?:case|stand|frame)|wooden|engraved|etched|carbon"
    # Art cases / extended-art displays: printed artwork around a card, or no card at all.
    r"|extended art|(?:art|custom|acrylic|resin|epoxy) (?:case|block|display)|art cases?"
    r"|magnetic (?:art|extended|display)|lenticular|3d art|card art display|shadow ?box)\b",
    re.I,
)
_NON_ENGLISH = re.compile(
    r"\b(japanese|japan|jpn|jp|korean|kor|chinese|s-chinese|t-chinese|german|deutsch|french"
    r"|fran[cç]ais|italian|italiano|spanish|espa[nñ]ol|portuguese|dutch|thai|indonesian"
    r"|ita|ger|deu|fra|fr|esp|spa|por|kr|cn|chn|jap|sc|tc|tarjetas?|cartas?|carte|karten?"
    r"|espa[nñ]a|versione|vf)\b",
    re.I,
)
_FIRST_ED = re.compile(r"\b(1st|first)[\s-]*ed(ition|\.)?\b", re.I)
_REVERSE = re.compile(r"\b(reverse|rev[ .-]?holo|r/h)\b", re.I)
_FRACTION = re.compile(
    r"(?<![\w/])([a-z]{0,4}\d{1,3}[a-z]?)\s*/\s*([a-z]{0,4}\d{1,3})(?![\w/])", re.I
)
_SET_STOPWORDS = {"set", "the", "and", "promos", "promo", "black", "star", "pokemon", "collection"}


def norm_number(value: str) -> str:
    """'004' -> '4', 'TG03' -> 'tg3', 'SWSH050' -> 'swsh50'."""
    m = re.fullmatch(r"([a-z]*)0*(\d+)([a-z]?)", value.strip().lower())
    if not m:
        return value.strip().lower()
    return f"{m.group(1)}{m.group(2)}{m.group(3)}"


def digits(value: str) -> int | None:
    found = re.sub(r"\D", "", value)
    return int(found) if found else None


_FORMS = [
    ("mega", r"\bmega\b|\bm\s+(?:charizard|mew)\b"),
    ("gx", r"\bgx\b"),
    ("vmax", r"\bvmax\b"),
    ("vstar", r"\bvstar\b"),
    ("v", r"\bv\b"),
    ("break", r"\bbreak\b"),
    ("dark", r"\bdark\s+(?:charizard|mew)\b"),
    ("shining", r"\bshining\s+(?:charizard|mew)\b"),
    ("radiant", r"\bradiant\s+(?:charizard|mew)\b"),
    ("delta", r"δ|\bdelta\b"),
]
_FORMS = [(form, re.compile(rx, re.I)) for form, rx in _FORMS]


def card_forms(text: str) -> set[str]:
    """Which special form a name or title mentions: Mega, GX, V, VMAX, Dark, Shining..."""
    text = re.sub(r"\bv[\s-]+(max|star)\b", r"v\1", text, flags=re.I)
    return {form for form, pattern in _FORMS if pattern.search(text)}


def word_match(name: str, text: str) -> bool:
    """True if `name` appears as its own word: 'Mew' matches 'Mew ex' but not 'Mewtwo'."""
    return re.search(rf"(?<![a-z]){re.escape(name.lower())}(?![a-z])", text.lower()) is not None


def detect_condition(title: str) -> str:
    for code, pattern in _CONDITIONS:
        if pattern.search(title):
            return code
    return "?"


def detect_variant(title: str) -> str | None:
    if _FIRST_ED.search(title):
        return "firstEdition"
    if _REVERSE.search(title):
        return "reverse"
    return None


def rejection_reason(title: str) -> str | None:
    if _GRADED.search(title):
        return "graded"
    if _NON_ENGLISH.search(title):
        return "not English"
    if m := _EXCLUDED.search(title):
        return f"excluded word: {m.group(0)}"
    # Celebrations "Classic Collection" cards are official reprints and part of a master set.
    if re.search(r"\breprint", title, re.I) and not re.search(r"celebration", title, re.I):
        return "excluded word: reprint"
    return None


@dataclass(frozen=True)
class CardRef:
    """The bits of a catalogue card needed for matching."""

    id: str
    pokemon: str
    name: str
    local_id: str
    set_name: str
    set_official: int | None
    variants: dict[str, float | None]  # variant -> market price in GBP (None if unknown)

    @property
    def is_promo(self) -> bool:
        return "promo" in self.set_name.lower() or not self.set_official

    def cheapest(self, options: list[str]) -> str:
        priced = [(self.variants.get(v) or float("inf"), v) for v in options]
        return min(priced)[1]


@dataclass(frozen=True)
class Match:
    card: CardRef
    variant: str
    condition: str
    note: str = ""


def _title_tokens(title: str) -> set[str]:
    joined = re.sub(r"([a-z]+)[\s#-]+(\d)", r"\1\2", title.lower())
    return {norm_number(t) for t in re.findall(r"[a-z]*\d+[a-z]?", joined)}


def _set_score(card: CardRef, title: str) -> int:
    words = {w for w in re.findall(r"[a-z0-9]+", card.set_name.lower()) if len(w) > 2}
    words -= _SET_STOPWORDS
    lowered = title.lower()
    return sum(1 for w in words if re.search(rf"\b{re.escape(w)}\b", lowered))


def match_title(title: str, cards: list[CardRef]) -> tuple[Match | None, str]:
    """Return (match, reason). `match` is None when the title should be ignored."""
    if reason := rejection_reason(title):
        return None, reason

    fractions = {(norm_number(a), digits(b)) for a, b in _FRACTION.findall(title) if digits(b)}
    if len(fractions) > 1:
        return None, "several card numbers in title"
    tokens = _title_tokens(title)
    has_promo_word = "promo" in title.lower()

    candidates: list[CardRef] = []
    for card in cards:
        if not word_match(card.pokemon, title):
            continue
        # "Mew" listings must not be Mewtwo cards (unless the card itself names Mewtwo).
        if word_match("mewtwo", title) and not word_match("mewtwo", card.name):
            continue
        # A title naming a form the card isn't (e.g. "Mega Charizard X ex" with the number of
        # the plain "Charizard EX" promo) is a different card.
        if card_forms(title) - card_forms(card.name):
            continue
        number = norm_number(card.local_id)
        if fractions:
            num, den = next(iter(fractions))
            if num == number and card.set_official and den == card.set_official:
                candidates.append(card)
        elif card.is_promo:
            if number.isdigit():
                if has_promo_word and re.search(rf"#\s*0*{number}\b|\b0*{number}\b", title):
                    candidates.append(card)
            elif number in tokens:
                candidates.append(card)
    # Celebrations "Classic Collection" reprints carry the original numbers (e.g. 4/102), so
    # never value them as the far pricier originals.
    if re.search(r"celebration|classic collection", title, re.I):
        candidates = [c for c in candidates if "celebration" in c.set_name.lower()]
    if not candidates:
        return None, "no card number match"

    wanted_variant = detect_variant(title)
    if wanted_variant:
        candidates = [c for c in candidates if wanted_variant in c.variants]
        if not candidates:
            return None, "variant does not exist for that card"

    note = ""
    if len(candidates) > 1:
        scores = [(_set_score(c, title), c) for c in candidates]
        best = max(s for s, _ in scores)
        top = [c for s, c in scores if s == best]
        if len(top) == 1 and best > 0:
            candidates = top
        else:
            note = "ambiguous set, valued as the cheapest match"

    def value(card: CardRef) -> float:
        prices = [p for p in card.variants.values() if p]
        return min(prices) if prices else float("inf")

    card = min(candidates, key=value)
    if wanted_variant:
        variant = wanted_variant
    elif re.search(r"\bnon[ -]?holo\b", title, re.I) and "normal" in card.variants:
        variant = "normal"
    elif re.search(r"\bholo\b", title, re.I) and "holo" in card.variants:
        variant = "holo"
    else:
        base = [v for v in card.variants if v in ("holo", "normal")] or list(card.variants)
        variant = card.cheapest(base)
    return Match(card=card, variant=variant, condition=detect_condition(title), note=note), "ok"


STAMP_LABEL = {
    "set-logo": "set-logo stamp",
    "pre-release": "prerelease stamp",
    "staff": "staff stamp",
    "pokemon-center": "Pokémon Center stamp",
    "player-rewards-program": "Play! Pokémon stamp",
    "30th-anniversary": "30th Anniversary stamp",
    "25th-celebration": "25th Celebration stamp",
    "w-promo": "W stamp",
}


def variant_label(variant: str) -> str:
    """'holo' -> 'Holo'; 'holo:set-logo' -> 'Holo · set-logo stamp'."""
    base, _, stamps = variant.partition(":")
    label = VARIANT_LABEL.get(base, base.title())
    for stamp in filter(None, stamps.split("+")):
        label += " · " + STAMP_LABEL.get(stamp, stamp.replace("-", " ").title() + " stamp")
    return label
