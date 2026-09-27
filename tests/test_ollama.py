import base64

import pytest

from app.services import organizer
from app.services.ai import EntryContext
from app.services.ollama import parse_json_reply
from tests.fake_ollama import DEFAULT_ANALYSIS, FakeOllama

MEMORY_BLADE = (
    "I just had an idea for a comic where a guy can cut through dimensions with a sword, "
    "but every time he does it he loses one of his memories."
)
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360000002000100e221bc330000000049454e44ae426082"
)


def ctx(text, **kw):
    return EntryContext(id=kw.pop("id", None), original_text=text, **kw)


def test_structured_analysis_is_validated_and_mapped():
    fake = FakeOllama(analysis={**DEFAULT_ANALYSIS, "entry_type": "Film", "type_confidence": 7})
    r = fake.provider().analyze_entry(ctx(MEMORY_BLADE))
    assert r.title == "The Memory Blade"
    assert r.entry_type == "movie"            # synonym mapped onto the allowed vocabulary
    assert r.type_confidence == 1.0           # clamped to 0..1
    assert r.tags == ["comic", "memory", "dimensions", "sword"]  # lower-cased
    assert r.concepts[0] == "dimensional travel"
    assert "Inferred — not stated by you" in r.inference
    assert "may explore" in r.inference
    assert r.uncertainties == ["The protagonist's name and world are not specified."]
    assert r.provider_name == "ollama:test-model"
    chat = fake.chats[0]
    assert chat["format"]["properties"]["entry_type"]["enum"]   # JSON schema sent
    assert chat["stream"] is False
    assert MEMORY_BLADE in chat["messages"][1]["content"]
    assert "images" not in chat["messages"][1]


def test_unknown_type_becomes_other():
    fake = FakeOllama(analysis={**DEFAULT_ANALYSIS, "entry_type": "podcast"})
    r = fake.provider().analyze_entry(ctx("A podcast about ghosts."))
    assert r.entry_type == "other" and r.type_confidence <= 0.3
    assert any("unknown type" in u for u in r.uncertainties)


@pytest.mark.parametrize("fake, expect", [
    (FakeOllama(reachable=False), "not reachable"),
    (FakeOllama(models=["other-model"]), "ollama pull test-model"),
    (FakeOllama(chat_status=500), "Ollama error (500)"),
    (FakeOllama(raw_reply="I think this is a comic!"), "valid JSON"),
])
def test_falls_back_to_heuristics_with_reason(fake, expect):
    r = fake.provider().analyze_entry(ctx(MEMORY_BLADE))
    assert r.entry_type == "comic"  # heuristic still classifies it
    assert r.provider_name == "local-heuristic-v1 (fallback)"
    assert expect in r.uncertainties[0]


def test_json_reply_parsing_tolerates_fences():
    assert parse_json_reply('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json_reply('Sure! {"a": 2} hope that helps') == {"a": 2}


def test_image_sent_to_vision_model(tmp_path):
    img = tmp_path / "s.png"
    img.write_bytes(PNG)
    visual = {"possible_type": "sketch", "visual_subjects": ["monster", "subway"], "style": "ink sketch",
              "characters": ["tentacled creature"], "objects": ["train"], "colors": ["black", "white"],
              "setting": "underground station"}
    fake = FakeOllama(capabilities=["completion", "vision"], analysis={**DEFAULT_ANALYSIS, "visual": visual})
    entry = EntryContext(id=1, source_type="image", attachments=[
        {"kind": "image", "mime_type": "image/png", "original_filename": "s.png", "path": str(img)}])
    r = fake.provider().analyze_entry(entry)
    chat = fake.chats[0]
    assert chat["messages"][1]["images"] == [base64.b64encode(PNG).decode()]
    assert "visual" in chat["format"]["properties"]
    assert r.visual["visual_subjects"] == ["monster", "subway"]
    assert r.visual["setting"] == "underground station"
    assert "(with image)" in r.method


def test_text_only_model_does_not_receive_images(tmp_path):
    img = tmp_path / "s.png"
    img.write_bytes(PNG)
    fake = FakeOllama(capabilities=["completion"])
    entry = EntryContext(id=1, source_type="image", original_text="a monster in the subway", attachments=[
        {"kind": "image", "mime_type": "image/png", "original_filename": "s.png", "path": str(img)}])
    r = fake.provider().analyze_entry(entry)
    assert "images" not in fake.chats[0]["messages"][1]
    assert any("can't see images" in u for u in r.uncertainties)
    assert all(v is None for v in r.visual.values())  # visual content stays UNKNOWN


def test_unsupported_image_format_is_reported(tmp_path):
    img = tmp_path / "s.heic"
    img.write_bytes(b"heic")
    fake = FakeOllama(capabilities=["completion", "vision"])
    entry = EntryContext(id=1, source_type="image", original_text="photo of my sketchbook", attachments=[
        {"kind": "image", "mime_type": "image/heic", "original_filename": "s.heic", "path": str(img)}])
    r = fake.provider().analyze_entry(entry)
    assert "images" not in fake.chats[0]["messages"][1]
    assert any("couldn't be converted" in u for u in r.uncertainties)


def test_relationships_are_judged_and_invented_ids_dropped():
    fake = FakeOllama(relationships=[
        {"candidate_id": 1, "relationship_type": "similar_to", "confidence": 0.8, "rationale": "Both about memory theft."},
        {"candidate_id": 999, "relationship_type": "related_to", "confidence": 0.9, "rationale": "Invented."},
        {"candidate_id": 2, "relationship_type": "made_up_type", "confidence": 0.1, "rationale": "Too weak."},
    ])
    new = ctx("Hero loses memories every time he travels between dimensions.", id=3)
    existing = [ctx("Villain who collects memories.", id=1), ctx("Dimensions of memory and loss.", id=2),
                ctx("Lemon cake recipe.", id=4)]
    rels = fake.provider().find_relationships(new, existing)
    assert [(r.target_id, r.relationship_type) for r in rels] == [(1, "similar_to")]
    listing = fake.chats[0]["messages"][1]["content"]
    assert "CANDIDATE 1" in listing and "CANDIDATE 4" not in listing  # pre-filtered


def test_relationships_fall_back_when_model_fails():
    fake = FakeOllama(reachable=False)
    rels = fake.provider().find_relationships(
        ctx("Hero loses memories.", id=2), [ctx("Villain who collects memories.", id=1)])
    assert [r.target_id for r in rels] == [1]


def test_health_messages():
    assert FakeOllama().provider().health()["ok"] is True
    missing = FakeOllama(models=["llama3:8b"]).provider().health()
    assert missing["ok"] is False and "ollama pull test-model" in missing["detail"]
    down = FakeOllama(reachable=False).provider().health()
    assert down["ok"] is False and "not reachable" in down["detail"]


def test_answer_question_and_summary():
    p = FakeOllama().provider()
    assert "#1" in p.answer_question("what about memory?", [ctx("memory thief", id=1)])
    assert p.summarize_entry(ctx("anything")) == "Short summary."


def test_end_to_end_capture_with_local_model(client, monkeypatch):
    fake = FakeOllama(capabilities=["completion", "vision"], analysis={
        **DEFAULT_ANALYSIS,
        "visual": {"possible_type": "sketch", "visual_subjects": ["monster"], "style": "pencil",
                   "characters": [], "objects": [], "colors": [], "setting": "subway"},
    })
    monkeypatch.setattr(organizer, "get_ai_provider", lambda: fake.provider())
    e = client.post("/api/entries", json={"original_text": MEMORY_BLADE}).json()
    assert e["processing_status"] == "done"
    assert e["title"] == "The Memory Blade" and e["title_source"] == "ai"
    assert e["ai_provider"] == "ollama:test-model"
    assert e["original_text"] == MEMORY_BLADE
    assert "Everything stayed on this computer" in e["ai_metadata"]["method"]

    img = client.post("/api/uploads", files={"file": ("sketch.png", PNG, "image/png")}).json()
    assert img["ai_metadata"]["visual"]["visual_subjects"] == ["monster"]
    # what the model saw in the picture is searchable
    hits = client.post("/api/search", json={"query": "sketches with monsters"}).json()["results"]
    assert img["id"] in [h["entry"]["id"] for h in hits]


def test_vision_check_is_retried_after_ollama_comes_up(tmp_path):
    fake = FakeOllama(reachable=False, capabilities=["completion", "vision"])
    p = fake.provider()
    assert p._supports_vision("test-model") is None
    fake.reachable = True
    assert p._supports_vision("test-model") is True
