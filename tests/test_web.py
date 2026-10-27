from __future__ import annotations

import base64

from fastapi.testclient import TestClient

from pokehunter.web import create_app


def client(settings):
    return TestClient(create_app(settings, run_loop=False))


def test_password_required(catalog_db, settings):
    settings.site_password = "kids"  # type: ignore[assignment]
    from pydantic import SecretStr

    settings.site_password = SecretStr("kids")
    with client(settings) as c:
        assert c.get("/api/status").status_code == 401
        assert c.get("/healthz").status_code == 200
        token = base64.b64encode(b"family:kids").decode()
        assert c.get("/api/status", headers={"Authorization": f"Basic {token}"}).status_code == 200


def test_checklist_tick_and_star(catalog_db, settings):
    with client(settings) as c:
        assert c.get("/").status_code == 200
        items = c.get("/api/cards").json()["items"]
        assert items[0]["card_id"] == "base1-4"  # oldest set first
        r = c.post(
            "/api/items/base1-4:holo", json={"owned": True, "priority": True, "target_price": "250"}
        )
        assert r.json()["owned"] == 1 and r.json()["target_price"] == 250.0
        status = c.get("/api/status").json()
        charizard = next(p for p in status["progress"] if p["pokemon"] == "Charizard")
        assert charizard["owned"] == 1
        assert c.post("/api/items/nope", json={"owned": True}).status_code == 404
        assert c.get("/api/deals").json()["deals"] == []


def test_prefs_roundtrip(catalog_db, settings):
    with client(settings) as c:
        prefs = c.post(
            "/api/prefs", json={"min_discount_pct": "30", "alerts_enabled": False, "bogus": 1}
        ).json()
        assert prefs["min_discount_pct"] == 30 and prefs["alerts_enabled"] is False
        assert "bogus" not in c.get("/api/prefs").json()


def test_add_and_remove_custom_card(catalog_db, settings):
    with client(settings) as c:
        r = c.post(
            "/api/cards/custom",
            json={"pokemon": "Mew", "name": "Ancient Mew", "local_id": "1", "variants": ["holo"]},
        )
        card_id = r.json()["id"]
        items = c.get("/api/cards").json()["items"]
        added = [i for i in items if i["card_id"] == card_id]
        assert added and added[0]["variant_label"] == "Holo" and added[0]["custom"] == 1
        assert c.post("/api/cards/custom", json={"pokemon": "Pikachu"}).status_code == 400
        assert c.delete(f"/api/cards/custom/{card_id}").json() == {"ok": True}
        assert c.delete("/api/cards/custom/base1-4").status_code == 404


def test_photo_key_is_stored_but_never_returned(catalog_db, settings):
    with client(settings) as c:
        assert c.get("/api/status").json()["photo_check_ready"] is False
        assert c.post("/api/photo-check-key", json={"key": "nope"}).status_code == 400
        r = c.post("/api/photo-check-key", json={"key": "sk-ant-test-123"})
        assert r.json() == {"photo_check_ready": True}
        assert "sk-ant" not in c.get("/api/status").text + c.get("/api/prefs").text


def test_all_listings_view(catalog_db, settings):
    with client(settings) as c:
        body = c.get("/api/deals?everything=true").json()
        assert body["deals"] == [] and "watch" in body
