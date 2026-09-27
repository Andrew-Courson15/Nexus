"""Visual Art Intelligence."""
import base64
import pytest

from app.database import SessionLocal
from app.models import ArtworkAnalysis
from app.services import ai, art, organizer
from tests.fake_ollama import ART_REPLY, FakeOllama

PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360000002000100e221bc330000000049454e44ae426082"
)


@pytest.fixture()
def vision(monkeypatch):
    """A local vision model (fake) for both organizing and art analysis."""
    fake = FakeOllama(models=["test-model"], capabilities=["completion", "vision"])
    provider = fake.provider()
    monkeypatch.setattr(organizer, "get_ai_provider", lambda: provider)
    monkeypatch.setattr(art, "get_ai_provider", lambda: provider)
    return fake


def upload(client, name="sketch.png", note=None, mime="image/png", data=PNG):
    r = client.post("/api/uploads", files={"file": (name, data, mime)}, data={"note": note} if note else {})
    assert r.status_code == 201, r.text
    return r.json()


def analyses(client, entry_id):
    return client.get(f"/api/entries/{entry_id}/art").json()


def test_new_images_get_a_layered_analysis_automatically(client, vision):
    e = upload(client, note="Mateo, first rough of his costume")
    [a] = analyses(client, e["id"])
    assert (a["status"], a["mode"], a["analysis_version"]) == ("done", "analysis", 1)
    assert a["visual_description"].startswith("A humanoid figure")
    assert a["composition_analysis"]["eye_flow"] == "Face, then down the blade."
    assert a["strengths"] and a["development_opportunities"] and a["experiments"]
    assert a["storytelling_analysis"]["possible_interpretation"].startswith("The character may")
    assert a["details"]["style_signals"] == ["heavy silhouettes", "dramatic lighting"]  # unknown signal dropped
    assert "unexpected_field" not in a["details"]
    assert a["images"][0]["url"].startswith("/api/uploads/")
    assert a["model"] == "ollama:test-model"
    chat = vision.art_chats[-1]
    assert len(chat["messages"][1]["images"]) == 1
    assert "Mateo, first rough of his costume" in chat["messages"][1]["content"]  # the artist's words as context
    assert "Stylization is not an error" in chat["messages"][0]["content"]


def test_no_automatic_analysis_without_a_vision_model(client):
    e = upload(client)  # tests default to the offline heuristic provider
    assert analyses(client, e["id"]) == []
    r = client.post(f"/api/entries/{e['id']}/art", json={"mode": "critique"})
    assert r.status_code == 200 and r.json()["status"] == "failed"
    assert "vision model" in r.json()["error"]


def test_modes_are_new_versions_and_keep_history(client, vision):
    e = upload(client)
    r = client.post(f"/api/entries/{e['id']}/art", json={"mode": "critique"})
    assert r.status_code == 200 and r.json()["status"] == "done"
    assert r.json()["details"]["critique"]["what_is_working"] == "Silhouette."
    r = client.post(f"/api/entries/{e['id']}/art",
                    json={"mode": "artist", "question": "Is the blade too big for him?"})
    assert "Is the blade too big for him?" in vision.art_chats[-1]["messages"][1]["content"]
    assert "ARTIST MODE" in vision.art_chats[-1]["messages"][1]["content"]
    versions = [(a["analysis_version"], a["mode"]) for a in analyses(client, e["id"])]
    assert versions == [(3, "artist"), (2, "critique"), (1, "analysis")]


def test_storyboard_mode_does_not_judge_rendering(client, vision):
    e = upload(client)
    client.post(f"/api/entries/{e['id']}/art", json={"mode": "storyboard"})
    assert "Do NOT criticise rendering" in vision.art_chats[-1]["messages"][1]["content"]
    assert "sequence" in vision.art_chats[-1]["format"]["properties"]


def test_compare_sends_both_images_in_order(client, vision):
    a = upload(client, "a.png")
    b = upload(client, "b.png")
    r = client.post(f"/api/entries/{a['id']}/art", json={"mode": "compare", "related_entry_ids": [b["id"]]}).json()
    assert r["status"] == "done" and [i["entry_id"] for i in r["images"]] == [a["id"], b["id"]]
    chat = vision.art_chats[-1]
    assert len(chat["messages"][1]["images"]) == 2
    assert "Image 1 is A and Image 2 is B" in chat["messages"][1]["content"]
    assert r["details"]["comparison"]["what_changed"] == ["B has a lower camera angle."]


@pytest.mark.parametrize("mode, related, message", [
    ("compare", [], "at least two"),
    ("sequence", [], "at least two"),
    ("critique", "other", "one image"),
    ("nonsense", [], "Unknown mode"),
])
def test_mode_validation(client, vision, mode, related, message):
    e = upload(client)
    other = upload(client, "b.png")
    ids = [other["id"]] if related == "other" else related
    r = client.post(f"/api/entries/{e['id']}/art", json={"mode": mode, "related_entry_ids": ids})
    assert r.status_code in (400, 422)
    if r.status_code == 400:
        assert message in r.json()["detail"]


def test_entries_without_images_or_with_unreadable_images(client, vision):
    t = client.post("/api/entries", json={"original_text": "Just text."}).json()
    assert "has no image" in client.post(f"/api/entries/{t['id']}/art", json={}).json()["detail"]
    heic = upload(client, "photo.heic", mime="image/heic", data=b"heic-bytes")
    assert "Couldn't read photo.heic" in client.post(f"/api/entries/{heic['id']}/art", json={}).json()["detail"]


def test_model_errors_are_recorded_not_lost(client, monkeypatch):
    fake = FakeOllama(models=["test-model"], capabilities=["completion"])  # text-only model
    monkeypatch.setattr(art, "get_ai_provider", lambda: fake.provider())
    e = upload(client)
    r = client.post(f"/api/entries/{e['id']}/art", json={"mode": "analysis"}).json()
    assert r["status"] == "failed" and "can't see images" in r["error"]


def test_observed_preferences_need_evidence_and_confirmation(client, vision):
    ids = [upload(client, f"s{i}.png")["id"] for i in range(3)]
    prefs = {p["statement"]: p for p in client.get("/api/art/preferences").json()}
    observed = prefs["heavy silhouettes"]
    assert observed["origin"] == "observed" and observed["status"] == "suggested"
    assert sorted(i["entry_id"] for i in observed["evidence"]) == sorted(ids)
    # unconfirmed observations are NOT given to the AI as the artist's preferences
    e = upload(client, "s4.png")
    client.post(f"/api/entries/{e['id']}/art", json={"mode": "critique"})
    assert "PREFERENCES THE ARTIST HAS CONFIRMED" not in vision.art_chats[-1]["messages"][1]["content"]
    # confirm one, reject the other
    client.patch(f"/api/art/preferences/{observed['id']}", json={"status": "confirmed"})
    client.patch(f"/api/art/preferences/{prefs['dramatic lighting']['id']}", json={"status": "rejected"})
    client.post("/api/art/preferences", json={"statement": "Cinematic compositions"})
    client.post(f"/api/entries/{e['id']}/art", json={"mode": "critique"})
    prompt = vision.art_chats[-1]["messages"][1]["content"]
    assert "- heavy silhouettes" in prompt and "- Cinematic compositions" in prompt
    assert "dramatic lighting\n" not in prompt.split("PREFERENCES THE ARTIST HAS CONFIRMED")[1].split("\n\n")[0]
    # a rejected pattern never comes back
    upload(client, "s5.png")
    statuses = {p["statement"]: p["status"] for p in client.get("/api/art/preferences").json()}
    assert "dramatic lighting" not in statuses


def test_art_library_grounds_the_critique(client, vision):
    k = client.post("/api/art/knowledge", json={
        "title": "Silhouette and readability", "topic": "character design", "source": "My character design course",
        "content": "A strong character reads as a solid silhouette.\n\nPush the shapes of the head, shoulders and "
                   "weapon so the pose is clear even in solid black."}).json()
    assert k["passages"] == 1
    reply = dict(ART_REPLY, knowledge_references=["Silhouette and readability", "Invented Book"])
    vision.art_reply = reply
    e = upload(client, note="character design for the swordsman")
    [a] = analyses(client, e["id"])
    prompt = vision.art_chats[-1]["messages"][1]["content"]
    assert "[Silhouette and readability] (My character design course)" in prompt
    assert a["details"]["knowledge_references"] == ["Silhouette and readability"]  # invented title dropped
    # deleting hides it from future analyses
    assert client.delete(f"/api/art/knowledge/{k['id']}").status_code == 204
    assert client.get("/api/art/knowledge").json() == []
    client.post(f"/api/entries/{e['id']}/art", json={"mode": "analysis"})
    assert "Silhouette and readability" not in vision.art_chats[-1]["messages"][1]["content"]


def test_knowledge_topic_is_validated(client):
    r = client.post("/api/art/knowledge", json={"title": "x", "topic": "astrology", "content": "y"})
    assert r.status_code == 422


def test_chunking_long_notes():
    text = "\n\n".join(f"Paragraph {i}. " + "Words about perspective. " * 20 for i in range(6))
    chunks = art.chunk_text(text, size=900)
    assert len(chunks) >= 3 and all(len(c) <= 900 for c in chunks)
    assert art.chunk_text("") == []


def test_evolution_timeline(client, vision):
    v1 = upload(client, "rough.png", note="Mateo rough")
    v2 = upload(client, "costume.png", note="Mateo costume redesign")
    v3 = upload(client, "sheet.png", note="Mateo final sheet")
    other = upload(client, "unrelated.png")
    for newer, older in ((v2, v1), (v3, v2)):
        r = client.post(f"/api/entries/{newer['id']}/relationships",
                        json={"target_entry_id": older["id"], "relationship_type": "evolved_from"})
        assert r.status_code == 201
    for probe in (v1, v2, v3):
        chain = [e["id"] for e in client.get(f"/api/entries/{probe['id']}/evolution").json()]
        assert chain == [v1["id"], v2["id"], v3["id"]]
    assert [e["id"] for e in client.get(f"/api/entries/{other['id']}/evolution").json()] == [other["id"]]
    r = client.post(f"/api/entries/{v1['id']}/art",
                    json={"mode": "evolution", "related_entry_ids": [v2["id"], v3["id"]]}).json()
    assert r["status"] == "done" and len(vision.art_chats[-1]["messages"][1]["images"]) == 3
    assert "Image 1 is the earliest" in vision.art_chats[-1]["messages"][1]["content"]


def test_gallery(client, vision):
    img = upload(client)
    client.post("/api/entries", json={"original_text": "text only"})
    g = client.get("/api/art/gallery").json()
    assert [x["entry"]["id"] for x in g] == [img["id"]]
    assert g[0]["analyses"] == 1 and g[0]["latest_status"] == "done"


def test_clean_keeps_only_schema_fields():
    schema = art.art_schema("compare")
    out = art.clean({"visual_description": "  lots   of   space ", "strengths": ["a", "a", "", {"x": 1}],
                     "sequential_art": {"shot_type": "Close-Up", "bogus": 1}, "extra": "x"}, schema)
    assert out["visual_description"] == "lots of space"
    assert out["strengths"] == ["a"]
    assert out["sequential_art"]["shot_type"] == "close-up" and "bogus" not in out["sequential_art"]
    assert "extra" not in out and "comparison" in out and "critique" not in out


def test_heuristic_provider_cannot_analyze_art():
    with pytest.raises(ai.AIProviderError, match="vision model"):
        ai.LocalHeuristicProvider().analyze_artwork(None)


def test_analyses_survive_in_the_database(client, vision):
    e = upload(client)
    client.post(f"/api/entries/{e['id']}/art", json={"mode": "critique"})
    with SessionLocal() as db:
        rows = db.query(ArtworkAnalysis).filter_by(entry_id=e["id"]).all()
        assert sorted(r.analysis_version for r in rows) == [1, 2]


def test_interrupted_art_analysis_resumes_after_restart(client, vision):
    from app.services import processing

    e = upload(client)
    with SessionLocal() as db:
        db.add(ArtworkAnalysis(entry_id=e["id"], analysis_version=2, mode="critique", status="running"))
        db.commit()  # as if NEXUS was closed mid-analysis
    assert processing.processing_queue.recover() == 1
    latest = analyses(client, e["id"])[0]
    assert (latest["analysis_version"], latest["status"]) == (2, "done")


def test_compare_stops_if_the_other_image_was_trashed(client, monkeypatch, vision):
    from app.services import processing

    a = upload(client, "a.png")
    b = upload(client, "b.png")
    real_submit = processing.processing_queue.submit
    held = []
    monkeypatch.setattr(processing.processing_queue, "submit", lambda job: held.append(job))  # queue, don't run
    r = client.post(f"/api/entries/{a['id']}/art", json={"mode": "compare", "related_entry_ids": [b["id"]]}).json()
    client.delete(f"/api/entries/{b['id']}")
    real_submit(held[-1])
    after = client.get(f"/api/art/analyses/{r['id']}").json()
    assert after["status"] == "failed" and "moved to the trash" in after["error"]


def test_saving_errors_mark_the_analysis_failed(client, monkeypatch, vision):
    e = upload(client)

    def broken(db):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(art, "refresh_observed_preferences", broken)
    r = client.post(f"/api/entries/{e['id']}/art", json={"mode": "critique"}).json()
    assert r["status"] == "failed" and "database is locked" in r["error"]


def test_oversized_images_are_analysed_through_a_smaller_preview(client, monkeypatch, vision):
    from app.services import images

    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (64, 48), "white").save(buf, "PNG")
    monkeypatch.setattr(images, "MAX_DIRECT_BYTES", 10)
    e = upload(client, data=buf.getvalue())
    assert e["attachments"][0]["preview_url"]
    r = client.post(f"/api/entries/{e['id']}/art", json={"mode": "critique"})
    assert r.status_code == 200 and r.json()["status"] == "done", r.text
    sent = vision.art_chats[-1]["messages"][1]["images"][0]
    assert base64.b64decode(sent)[:3] == b"\xff\xd8\xff" or base64.b64decode(sent)[:4] == b"\x89PNG"


def test_analyze_every_artwork_not_analysed_yet(client, vision, monkeypatch):
    from app.config import get_settings

    monkeypatch.setenv("NEXUS_ART_AUTO_ANALYZE", "false")  # older art, uploaded before analyses existed
    get_settings.cache_clear()
    a, b = upload(client, name="a.png"), upload(client, name="b.png")
    client.post("/api/entries", json={"original_text": "just words", "analyze": False})
    assert analyses(client, a["id"]) == [] and analyses(client, b["id"]) == []
    r = client.post("/api/art/analyze-missing").json()
    assert r == {"queued": 2, "skipped": []}
    assert [x["status"] for x in analyses(client, a["id"])] == ["done"]
    assert client.post("/api/art/analyze-missing").json()["queued"] == 0  # each once
    monkeypatch.undo()
    get_settings.cache_clear()


def test_analyze_missing_needs_a_vision_model(client, monkeypatch):
    monkeypatch.setattr(art, "get_ai_provider", lambda: ai.LocalHeuristicProvider())
    r = client.post("/api/art/analyze-missing")
    assert r.status_code == 400 and "see images" in r.json()["detail"]
