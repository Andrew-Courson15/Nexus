"""Develop this idea: directions and questions from the local AI, labelled and kept with the entry."""
import pytest

from app.services import develop
from tests.fake_ollama import DEVELOP_REPLY, FakeOllama


@pytest.fixture()
def model(monkeypatch):
    fake = FakeOllama(models=["test-model"])
    monkeypatch.setattr(develop, "get_ai_provider", lambda: fake.provider())
    return fake


def cap(client, text, **kw):
    return client.post("/api/entries", json={"original_text": text, "analyze": False, **kw}).json()


def test_develop_keeps_results_and_never_touches_your_words(client, model):
    e = cap(client, "Hero loses his memories whenever he crosses into another universe.")
    r = client.post(f"/api/entries/{e['id']}/develop")
    assert r.status_code == 200, r.text
    out = r.json()
    assert [d["name"] for d in out["directions"]] == [d["name"] for d in DEVELOP_REPLY["directions"]]
    assert out["questions"][0] == "Who does he lose first?" and out["next_step"].startswith("Sketch")
    assert out["model"].startswith("ollama") and out["created_at"]
    prompt = next(c for c in model.chats if "DEVELOP AN IDEA" in c["messages"][0]["content"])["messages"][1]["content"]
    assert "Hero loses his memories" in prompt
    client.post(f"/api/entries/{e['id']}/develop")
    detail = client.get(f"/api/entries/{e['id']}").json()
    assert len(detail["ai_metadata"]["developments"]) == 2  # every result kept, newest first
    assert detail["original_text"] == e["original_text"]


def test_needs_words_and_a_model(client, model, monkeypatch):
    empty = client.post("/api/entries", json={"original_text": " ", "user_notes": "", "analyze": False})
    if empty.status_code == 201:
        assert client.post(f"/api/entries/{empty.json()['id']}/develop").status_code == 400
    from app.services import ai

    monkeypatch.setattr(develop, "get_ai_provider", lambda: ai.LocalHeuristicProvider())
    e = cap(client, "Something")
    r = client.post(f"/api/entries/{e['id']}/develop")
    assert r.status_code == 400 and "local AI model" in r.json()["detail"]
