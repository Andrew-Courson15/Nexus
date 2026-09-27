"""Project analysis: a whole-project overview that cites its entries."""
import json

from app.services import project_analysis
from tests.fake_ollama import FakeOllama


def setup_project(client):
    p = client.post("/api/projects", json={"name": "Night Wrestlers", "project_type": "comic",
                                           "description": "Luchadors who fight spirits on beaches."}).json()
    a = client.post("/api/entries", json={"original_text": "El Faro: a luchador whose mask glows like a lighthouse.",
                                          "entry_type": "character", "project_ids": [p["id"]]}).json()
    b = client.post("/api/entries", json={"original_text": "Opening: the first fight at midnight on the beach.",
                                          "project_ids": [p["id"]]}).json()
    outside = client.post("/api/entries", json={"original_text": "Lemon cake recipe."}).json()
    return p, a, b, outside


def test_model_overview_is_cleaned_and_cites_only_project_entries(client, monkeypatch):
    p, a, b, outside = setup_project(client)
    reply = {
        "overview": "A comic about luchadors fighting spirits.",
        "characters": [{"name": "El Faro", "description": "Mask glows like a lighthouse.", "entries": [a["id"], outside["id"], 999]}],
        "story_threads": [{"thread": "The midnight beach fight", "entries": [b["id"]]}],
        "world": [], "strongest_ideas": [{"idea": "The glowing mask", "why": "Instantly readable.", "entries": [a["id"]]}],
        "contradictions": [], "open_questions": ["Who are the spirits?"], "gaps": ["No villain yet."],
        "next_steps": ["Sketch the spirits."], "uncertainties": [], "extra": "ignored",
    }
    fake = FakeOllama(raw_reply=json.dumps(reply))
    monkeypatch.setattr(project_analysis, "get_ai_provider", lambda: fake.provider())
    r = client.post(f"/api/projects/{p['id']}/analyses")
    assert r.status_code == 200 and r.json()["status"] == "done", r.text
    res = r.json()["result"]
    assert res["characters"][0]["entries"] == [a["id"]]      # not the cake, not an invented id
    assert "extra" not in res and res["gaps"] == ["No villain yet."]
    assert sorted(r.json()["entry_ids"]) == sorted([a["id"], b["id"]])
    prompt = fake.chats[0]["messages"][1]["content"]
    assert "USER'S WORDS: El Faro" in prompt and "Lemon cake" not in prompt
    assert "THE CREATOR'S DESCRIPTION: Luchadors" in prompt

    again = client.post(f"/api/projects/{p['id']}/analyses").json()
    versions = client.get(f"/api/projects/{p['id']}/analyses").json()
    assert [v["version"] for v in versions] == [2, 1] and again["version"] == 2  # earlier kept


def test_without_a_model_a_modest_overview_is_given(client):
    p, a, _, _ = setup_project(client)
    res = client.post(f"/api/projects/{p['id']}/analyses").json()
    assert res["status"] == "done" and "2 entries" in res["result"]["overview"]
    assert res["result"]["characters"][0]["entries"] == [a["id"]]
    assert res["result"]["uncertainties"]


def test_model_errors_are_recorded(client, monkeypatch):
    p, *_ = setup_project(client)
    fake = FakeOllama(chat_status=500)
    monkeypatch.setattr(project_analysis, "get_ai_provider", lambda: fake.provider())
    res = client.post(f"/api/projects/{p['id']}/analyses").json()
    assert res["status"] == "failed" and res["error"]


def test_empty_or_trashed_projects(client):
    p = client.post("/api/projects", json={"name": "Empty"}).json()
    assert client.post(f"/api/projects/{p['id']}/analyses").status_code == 400
    client.delete(f"/api/projects/{p['id']}")
    assert client.post(f"/api/projects/{p['id']}/analyses").status_code == 404


def test_trashed_entries_are_left_out(client, monkeypatch):
    p, a, b, _ = setup_project(client)
    client.delete(f"/api/entries/{b['id']}")
    fake = FakeOllama(raw_reply=json.dumps({"overview": "x"}))
    monkeypatch.setattr(project_analysis, "get_ai_provider", lambda: fake.provider())
    res = client.post(f"/api/projects/{p['id']}/analyses").json()
    assert res["entry_ids"] == [a["id"]]
    assert "Opening: the first fight" not in fake.chats[0]["messages"][1]["content"]
