"""The phone's offline copy (snapshot of everything needed to read the archive)."""
import gzip
import json

from tests.test_images import _pil


def test_snapshot_has_entries_relationships_and_projects(client):
    p = client.post("/api/projects", json={"name": "Hollow Sun"}).json()
    a = client.post("/api/entries", json={"original_text": "A sword that eats memories.", "project_ids": [p["id"]]}).json()
    b = client.post("/api/entries", json={"original_text": "The memory thief's origin."}).json()
    client.post(f"/api/entries/{a['id']}/relationships", json={"target_entry_id": b["id"], "relationship_type": "related_to"})
    gone = client.post("/api/entries", json={"original_text": "Trashed idea"}).json()
    client.delete(f"/api/entries/{gone['id']}")

    snap = client.get("/api/offline/snapshot").json()
    ids = [e["id"] for e in snap["entries"]]
    assert ids == [b["id"], a["id"]]  # newest first, trash left out
    first = snap["entries"][1]
    assert first["original_text"] == "A sword that eats memories." and first["projects"][0]["name"] == "Hollow Sun"
    for e in (a, b):  # exactly what the entry page shows when NEXUS is on
        assert snap["relationships"][str(e["id"])] == client.get(f"/api/entries/{e['id']}/relationships").json()
    assert b["id"] in [r["other"]["id"] for r in snap["relationships"][str(a["id"])]]
    assert snap["entries"][0] == client.get(f"/api/entries/{b['id']}").json()
    assert snap["projects"][0]["name"] == "Hollow Sun" and snap["projects"][0]["entry_count"] == 1
    assert snap["generated_at"]


def test_snapshot_includes_thumbnails_and_art(client):
    e = client.post("/api/uploads", files={"file": ("s.png", _pil("PNG"), "image/png")}).json()
    snap = client.get("/api/offline/snapshot").json()
    att = snap["entries"][0]["attachments"][0]
    assert att["thumb_url"].startswith(f"/api/uploads/{att['id']}/thumb?v=")
    assert snap["art"].get(str(e["id"]), []) == []  # no vision model in tests: no analyses


def test_snapshot_is_compressed_when_the_phone_accepts_it(client):
    for i in range(20):
        client.post("/api/entries", json={"original_text": f"Idea number {i} about dragons and memory."})
    r = client.get("/api/offline/snapshot", headers={"Accept-Encoding": "gzip"})
    assert r.headers.get("content-encoding") == "gzip"
    assert len(json.loads(r.content)["entries"]) == 20  # the client decompresses transparently
    raw = client.get("/api/offline/snapshot", headers={"Accept-Encoding": "identity"})
    assert "content-encoding" not in raw.headers
    assert len(gzip.compress(raw.content)) < len(raw.content)


def test_snapshot_carries_art_analyses_exactly_as_the_app_shows_them(client, monkeypatch):
    from app.services import art, organizer
    from tests.fake_ollama import FakeOllama

    fake = FakeOllama(models=["test-model"], capabilities=["completion", "vision"])
    provider = fake.provider()
    monkeypatch.setattr(organizer, "get_ai_provider", lambda: provider)
    monkeypatch.setattr(art, "get_ai_provider", lambda: provider)
    e = client.post("/api/uploads", files={"file": ("hero.png", _pil("PNG"), "image/png")}).json()
    client.post(f"/api/entries/{e['id']}/art", json={"mode": "critique"})
    shown = client.get(f"/api/entries/{e['id']}/art").json()
    assert len(shown) == 2 and shown[0]["status"] == "done"
    snap = client.get("/api/offline/snapshot").json()
    assert snap["art"][str(e["id"])] == shown


def test_one_broken_record_never_blocks_the_whole_copy(client, monkeypatch):
    from app.routes import offline

    good = client.post("/api/entries", json={"original_text": "fine"}).json()
    bad = client.post("/api/entries", json={"original_text": "odd"}).json()
    real = offline.entry_detail

    def flaky(e):
        if e.id == bad["id"]:
            raise ValueError("unexpected data")
        return real(e)
    monkeypatch.setattr(offline, "entry_detail", flaky)
    snap = client.get("/api/offline/snapshot").json()
    assert [x["id"] for x in snap["entries"]] == [good["id"]]
