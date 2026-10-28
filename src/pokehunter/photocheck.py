"""Ask Claude to look at a listing's photos before we alert on it.

Fakes (gold/metal foil, wrong fonts, stickers, "credit card skins", fan art) usually have a
perfectly normal title and price; only the photos give them away.
"""

from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass

import anthropic
import httpx

from .config import Settings
from .db import Database

log = logging.getLogger(__name__)

REJECT = {"fake", "not_a_card", "wrong_card"}
CHEAP_PHOTO_MODEL = "claude-haiku-5-5"
MAX_IMAGE_BYTES = 4_500_000
MEDIA_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}

SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {
            "type": "string",
            "enum": ["genuine", "fake", "not_a_card", "wrong_card", "unsure"],
        },
        "reason": {"type": "string"},
    },
    "required": ["verdict", "reason"],
    "additionalProperties": False,
}

SYSTEM = """You check eBay listing photos of Pokémon trading cards for a parent buying \
cards for their children. Decide whether the photos show a genuine, officially printed \
English Pokémon TCG card that matches the card the listing claims to be.

Verdicts:
- "fake": a counterfeit or unofficial card. Common signs: the whole card is gold, silver, \
black or rainbow metal/foil (real gold "secret rare" cards have normal card layout, \
textured foil and crisp official text); wrong or blurry fonts; misspellings; wrong energy \
symbols, HP or attack values; odd colours or borders; a glossy plastic look; a wrong or \
missing card back; "proxy"/"orica" style custom art.
- "not_a_card": a sticker, card skin, print, art piece, toy, box or anything that is not \
a single physical card.
- "wrong_card": a genuine card, but clearly a different card, set or language than claimed.
- "genuine": looks like the real printed card claimed.
- "unsure": photos are too small, blurry or stock images to tell.

Only say "fake", "not_a_card" or "wrong_card" when the photos clearly show it. Keep the \
reason to one short sentence a parent can understand."""


@dataclass
class PhotoVerdict:
    verdict: str
    reason: str

    @property
    def rejected(self) -> bool:
        return self.verdict in REJECT


class PhotoChecker:
    def __init__(self, settings: Settings, http: httpx.AsyncClient, db: Database) -> None:
        self.settings = settings
        self.http = http
        self.db = db
        self._client: anthropic.AsyncAnthropic | None = None
        self._client_key: str | None = None

    def api_key(self) -> str | None:
        """Key from the website's Settings tab, else ANTHROPIC_API_KEY on the server."""
        stored = self.db.get_kv("anthropic_api_key")
        if stored:
            return stored
        if self.settings.anthropic_api_key:
            return self.settings.anthropic_api_key.get_secret_value()
        return None

    @property
    def ready(self) -> bool:
        return bool(self.api_key())

    def client(self) -> anthropic.AsyncAnthropic:
        key = self.api_key()
        if self._client is None or key != self._client_key:
            self._client = anthropic.AsyncAnthropic(api_key=key, timeout=120.0)
            self._client_key = key
        return self._client

    async def _download(self, url: str) -> dict | None:
        try:
            response = await self.http.get(url, timeout=20, follow_redirects=True)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            log.warning("could not download listing photo: %s", exc)
            return None
        media_type = response.headers.get("content-type", "").split(";")[0].strip()
        if media_type not in MEDIA_TYPES or len(response.content) > MAX_IMAGE_BYTES:
            return None
        return {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": media_type,
                "data": base64.standard_b64encode(response.content).decode(),
            },
        }

    async def check(
        self,
        claimed: str,
        title: str,
        image_urls: list[str],
        examples: list[str] | None = None,
        model: str | None = None,
    ) -> PhotoVerdict:
        images = [img for url in image_urls[:3] if (img := await self._download(url))]
        if not images:
            return PhotoVerdict("unsure", "No photos could be loaded")
        prompt = f"The listing claims to be: {claimed}\nListing title: {title}\n"
        if examples:
            reasons = "\n".join(f"- {r}" for r in dict.fromkeys(examples[-8:]))
            prompt += (
                "The family has previously reported fakes of this card. What they noticed:\n"
                f"{reasons}\n"
            )
        prompt += "Look at the photos above and give your verdict."
        try:
            model = model or self.settings.photo_check_model
            request = {
                "model": model,
                "max_tokens": 4000,
                "system": SYSTEM,
                "output_config": {
                    "effort": "medium",
                    "format": {"type": "json_schema", "schema": SCHEMA},
                },
                "messages": [
                    {"role": "user", "content": [*images, {"type": "text", "text": prompt}]}
                ],
            }
            if model.startswith("claude-haiku"):
                # Haiku has no server-side refusal fallback; a refusal is handled below.
                message = await self.client().messages.create(**request)
            else:
                message = await self.client().beta.messages.create(
                    **request, betas=["server-side-fallback-2026-07-01"], fallbacks="default"
                )
        except anthropic.AuthenticationError:
            return PhotoVerdict("unsure", "Photo check key was rejected; check it in Settings")
        except anthropic.RateLimitError:
            return PhotoVerdict("unsure", "Photo check is busy; not checked")
        except anthropic.APIStatusError as exc:
            log.warning("photo check failed with HTTP %s", exc.status_code)
            return PhotoVerdict("unsure", "Photo check unavailable right now")
        except anthropic.APIConnectionError:
            return PhotoVerdict("unsure", "Photo check could not connect")
        if message.stop_reason == "refusal":
            return PhotoVerdict("unsure", "Photo check declined to answer")
        text = next((b.text for b in message.content if b.type == "text"), "")
        try:
            result = json.loads(text)
            return PhotoVerdict(str(result["verdict"]), str(result.get("reason", ""))[:300])
        except (json.JSONDecodeError, KeyError, TypeError):
            return PhotoVerdict("unsure", "Photo check gave an unreadable answer")
