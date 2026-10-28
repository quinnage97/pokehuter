"""The Deals tab filters, saved on the server so they can also decide which deals alert.

Mirrors `passes()` in static/app.js: keep the two in step.
"""

from __future__ import annotations

from typing import Any

DEFAULTS: dict[str, Any] = {
    "q": "",
    "sort": "best",
    "poke": "All",
    "cond": [],
    "vars": [],
    "max": "",
    "min": "",
    "disc": "",
    "fb": "",
    "star": False,
    "photo": False,
    "verified": False,
    "post": False,
    "alerts": False,  # also apply these filters to Telegram alerts
}
SORTS = {"best", "saving", "cheap", "dear", "new", "old"}


def _number(value: Any) -> float | None:
    try:
        return None if value in ("", None) else float(value)
    except (TypeError, ValueError):
        return None


def clean(raw: dict[str, Any]) -> dict[str, Any]:
    """Keep only known filter settings, in the right types."""
    f = dict(DEFAULTS)
    for key, default in DEFAULTS.items():
        if key not in raw:
            continue
        value = raw[key]
        if isinstance(default, bool):
            f[key] = bool(value)
        elif isinstance(default, list):
            f[key] = [str(v) for v in value or []][:20]
        elif key in ("max", "min", "disc", "fb"):
            number = _number(value)
            f[key] = "" if number is None else number
        else:
            f[key] = str(value or "")[:100]
    if f["sort"] not in SORTS:
        f["sort"] = "best"
    return f


def passes(d: dict[str, Any], f: dict[str, Any]) -> bool:
    q = str(f.get("q") or "").strip().lower()
    text = f"{d.get('card_name')} {d.get('set_name')} {d.get('local_id')} {d.get('title')}"
    if q and q not in text.lower():
        return False
    if f.get("poke", "All") != "All" and d.get("pokemon") != f["poke"]:
        return False
    if f.get("cond") and d.get("condition") not in f["cond"]:
        return False
    if f.get("vars"):
        base, _, stamps = str(d.get("variant") or "").partition(":")
        if not ((base in f["vars"] and not stamps) or (stamps and "stamped" in f["vars"])):
            return False
    total = d.get("total") or 0
    if (limit := _number(f.get("max"))) is not None and total > limit:
        return False
    if (limit := _number(f.get("min"))) is not None and total < limit:
        return False
    if (limit := _number(f.get("disc"))) is not None and (d.get("discount_pct") or -1) < limit:
        return False
    if (limit := _number(f.get("fb"))) is not None and (d.get("feedback_score") or 0) < limit:
        return False
    if f.get("star") and not d.get("priority"):
        return False
    if f.get("photo") and d.get("photo_verdict") != "genuine":
        return False
    if f.get("verified") and not d.get("ebay_median_gbp"):
        return False
    return not (f.get("post") and not d.get("shipping_known"))
