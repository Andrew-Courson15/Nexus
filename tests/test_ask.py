"""Ask NEXUS: answers come only from the archive, with real citations."""
from app.services import ask as ask_service
from tests.fake_ollama import FakeOllama


def cap(client, text):
    return client.post("/api/entries", json={"original_text": text}).json()


def test_answer_cites_real_entries_and_lists_sources(client, monkeypatch):
    villain = cap(client, "Villain idea: the Archivist erases whole towns from maps.")
    cap(client, "Lemon cake recipe for Sunday.")
    fake = FakeOllama(raw_reply=None)
    captured = {}

    def fake_provider():
        p = fake.provider()
        orig = p.answer_question

        def spy(question, context):
            captured["ids"] = [c.id for c in context]
            fake.raw_reply = f"Your main villain is the Archivist [#{villain['id']}]. Also a stranger [#999]."
            return orig(question, context)
        p.answer_question = spy
        return p

    monkeypatch.setattr(ask_service, "get_ai_provider", fake_provider)
    r = client.post("/api/ask", json={"question": "who is my villain?"})
    assert r.status_code == 200, r.text
    data = r.json()
    assert villain["id"] in captured["ids"]
    assert f"[#{villain['id']}]" in data["answer"]
    assert "[#999]" not in data["answer"] and "[unknown entry]" in data["answer"]  # invented ids aren't links
    assert data["sources"][0]["entry"]["id"] == villain["id"] and data["sources"][0]["cited"]
    prompt = fake.chats[0]["messages"][1]["content"]
    assert "USER'S WORDS: Villain idea" in prompt and "QUESTION: who is my villain?" in prompt


def test_falls_back_to_recent_entries_when_nothing_matches(client):
    e = cap(client, "Beach wrestling match at night.")
    data = client.post("/api/ask", json={"question": "what have I been up to lately?"}).json()
    assert data["note"] and "most recent" in data["note"]
    assert [s["entry"]["id"] for s in data["sources"]] == [e["id"]]


def test_empty_archive_says_so(client):
    data = client.post("/api/ask", json={"question": "what villains do I have?"}).json()
    assert data["sources"] == [] and "nothing" in data["answer"].lower()


def test_question_is_required(client):
    for blank in ("", "   ", "?"):
        assert client.post("/api/ask", json={"question": blank}).status_code == 422
