"""Learn from listings the family marks as fake.

Every report teaches three simple, visible rules (all shown and undoable in Settings):
- the seller is blocked,
- words that keep appearing in fake titles but rarely elsewhere are blocked,
- a fake price level for that card version: copies at or below it are treated as fakes.
The reasons given are also passed to the photo check as examples.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from .db import Database, now_iso

WORD = re.compile(r"[a-z][a-z0-9]{2,}|[0-9]+[a-z]+[0-9a-z]*")
COMMON = {
    "pokemon",
    "pokémon",
    "card",
    "cards",
    "holo",
    "rare",
    "english",
    "tcg",
    "the",
    "and",
    "with",
    "for",
    "new",
    "mint",
    "near",
    "full",
    "art",
    "ultra",
    "secret",
    "promo",
}
# Fake copies are often priced a little above the reported one.
PRICE_MARGIN = 1.15
# A learned word must also never be part of a real card's name, set or rarity.


def title_words(title: str) -> set[str]:
    return {w for w in WORD.findall(title.lower()) if w not in COMMON}


@dataclass
class Signals:
    sellers: set[str] = field(default_factory=set)
    words: set[str] = field(default_factory=set)
    price_traps: dict[str, float] = field(default_factory=dict)  # item id -> fake price
    reasons: dict[str, list[str]] = field(default_factory=dict)  # card id -> reasons

    def flag(self, item_id: str, title: str, seller: str | None, total: float) -> str | None:
        """Why this listing looks like a known fake, or None."""
        if seller and seller.lower() in self.sellers:
            return f"Seller {seller} was reported for selling fakes"
        if hit := sorted(title_words(title) & self.words):
            return f"Title contains '{hit[0]}', seen in fakes you reported"
        trap = self.price_traps.get(item_id)
        if trap is not None and total <= trap * PRICE_MARGIN:
            return f"Priced like the £{trap:.2f} fake you reported for this card"
        return None


def record_fake(db: Database, listing_id: str, reason: str) -> int:
    row = db.one("SELECT * FROM listings WHERE item_id = ?", (listing_id,))
    if not row:
        raise KeyError(listing_id)
    with db.tx() as c:
        report_id = c.execute(
            """
            INSERT INTO fake_reports (listing_id, item_id, title, seller, total, fair_gbp,
                reason, image, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                listing_id,
                row["match_item_id"],
                row["title"],
                row["seller"],
                row["total"],
                row["fair_gbp"],
                reason.strip()[:200] or "Marked as fake",
                row["image"],
                now_iso(),
            ),
        ).lastrowid
        c.execute(
            "UPDATE listings SET user_verdict = 'fake', is_deal = 0, dismissed = 1"
            " WHERE item_id = ?",
            (listing_id,),
        )
    return int(report_id)


def undo_fake(db: Database, report_id: int) -> None:
    row = db.one("SELECT listing_id FROM fake_reports WHERE id = ?", (report_id,))
    if not row:
        raise KeyError(report_id)
    with db.tx() as c:
        c.execute("DELETE FROM fake_reports WHERE id = ?", (report_id,))
        c.execute(
            "UPDATE listings SET user_verdict = NULL, dismissed = 0 WHERE item_id = ?",
            (row["listing_id"],),
        )


def learn(db: Database, *, block_sellers: bool = True) -> Signals:
    reports = db.query("SELECT * FROM fake_reports ORDER BY id")
    signals = Signals()
    if not reports:
        return signals
    if block_sellers:
        signals.sellers = {r["seller"].lower() for r in reports if r["seller"]}

    # Words: in 2+ reported fakes, and in at most 1% of the other listings seen this month.
    fake_counts: Counter[str] = Counter()
    for report in reports:
        fake_counts.update(title_words(report["title"]))
    cutoff = (datetime.now(UTC) - timedelta(days=30)).isoformat()
    others = db.query(
        "SELECT title FROM listings WHERE last_seen >= ? AND user_verdict IS NULL", (cutoff,)
    )
    other_counts: Counter[str] = Counter()
    for row in others:
        other_counts.update(title_words(row["title"]))
    ignored = set(db.get_kv("ignored_fake_words", []) or [])
    for card in db.query("SELECT name, set_name, rarity FROM cards"):
        ignored |= title_words(" ".join(filter(None, card.values())))
    limit = max(1, len(others) // 100)
    signals.words = {
        word
        for word, count in fake_counts.items()
        if count >= 2 and other_counts[word] <= limit and word not in ignored
    }

    for report in reports:
        # Only fakes that were clearly underpriced set a price trap; a fake priced like the
        # real card must not block genuine copies.
        fair, total = report["fair_gbp"], report["total"]
        if report["item_id"] and fair and total and total <= fair * 0.75:
            current = signals.price_traps.get(report["item_id"], 0)
            signals.price_traps[report["item_id"]] = max(current, total)
        card_id = (report["item_id"] or "").split(":", 1)[0]
        signals.reasons.setdefault(card_id, []).append(report["reason"])
    return signals


def summary(db: Database, signals: Signals) -> dict[str, Any]:
    reports = db.query(
        """
        SELECT f.*, c.name AS card_name, c.set_name, c.local_id, i.variant
        FROM fake_reports f
        LEFT JOIN items i ON i.id = f.item_id
        LEFT JOIN cards c ON c.id = i.card_id
        ORDER BY f.id DESC
        """
    )
    return {
        "reports": reports,
        "sellers": sorted(signals.sellers),
        "words": sorted(signals.words),
        "ignored_words": sorted(db.get_kv("ignored_fake_words", []) or []),
    }
