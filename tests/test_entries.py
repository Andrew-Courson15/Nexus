from pathlib import Path

from app.config import get_settings

MEMORY_BLADE = (
    "I just had an idea for a comic where a guy can cut through dimensions with a sword, "
    "but every time he does it he loses one of his memories."
)


def capture(client, text, **extra):
    r = client.post("/api/entries", json={"original_text": text, **extra})
    assert r.status_code == 201, r.text
    return r.json()


def test_capture_preserves_original_exactly(client):
    raw = "  An idea with   odd spacing,\nnew lines\tand tabs.  "
    e = capture(client, raw)
    assert e["original_text"] == raw
    assert client.get(f"/api/entries/{e['id']}").json()["original_text"] == raw


def test_blank_capture_rejected(client):
    assert client.post("/api/entries", json={"original_text": "   "}).status_code == 422


def test_organizer_generates_labelled_ai_data(client):
    e = capture(client, MEMORY_BLADE)
    assert e["entry_type"] == "comic"
    assert e["entry_type_source"] == "ai"
    assert e["title_source"] == "ai" and e["title"]
    assert e["status"] == "raw"
    assert "comic concept" in e["ai_summary"]
    meta = e["ai_metadata"]
    assert "memory" in meta["concepts"] and "alternate realities" in meta["concepts"]
    assert {"identity", "loss"} <= set(meta["themes"])
    assert "not stated by you" in e["ai_inference"]
    assert all(t["source"] == "ai" for t in e["tags"])
    assert e["ai_provider"] == "local-heuristic-v1"
    # original untouched by analysis
    assert e["original_text"] == MEMORY_BLADE


def test_user_title_and_type_survive_reanalysis(client):
    e = capture(client, MEMORY_BLADE, title="The Memory Blade", entry_type="story")
    assert (e["title"], e["title_source"]) == ("The Memory Blade", "user")
    r = client.post(f"/api/entries/{e['id']}/analyze").json()
    assert (r["title"], r["entry_type"]) == ("The Memory Blade", "story")
    assert r["ai_metadata"]["suggested_entry_type"] == "comic"  # AI opinion kept separately


def test_update_cannot_touch_original_and_creates_revisions(client):
    e = capture(client, MEMORY_BLADE)
    r = client.put(f"/api/entries/{e['id']}", json={"original_text": "overwritten!"})
    assert r.status_code == 422
    r = client.put(f"/api/entries/{e['id']}", json={"status": "exploring", "user_notes": "Maybe a trilogy."})
    assert r.status_code == 200
    body = r.json()
    assert body["original_text"] == MEMORY_BLADE
    assert body["status"] == "exploring" and body["user_notes"] == "Maybe a trilogy."
    revs = client.get(f"/api/entries/{e['id']}/revisions").json()
    assert [r["version_number"] for r in revs] == [3, 2, 1]
    assert revs[-1]["change_description"] == "Captured original material"
    assert revs[-1]["content_snapshot"]["original_text"] == MEMORY_BLADE


def test_removed_ai_tag_is_not_reintroduced(client):
    e = capture(client, MEMORY_BLADE)
    names = [t["name"] for t in e["tags"]]
    assert "weapons" in names
    keep = [n for n in names if n != "weapons"] + ["hollow sun"]
    r = client.put(f"/api/entries/{e['id']}", json={"tags": keep}).json()
    assert "weapons" not in [t["name"] for t in r["tags"]]
    assert {"name": "hollow sun", "source": "user"} in r["tags"]
    r = client.post(f"/api/entries/{e['id']}/analyze").json()
    assert "weapons" not in [t["name"] for t in r["tags"]]
    assert {"name": "hollow sun", "source": "user"} in r["tags"]


def test_confirm_locks_ai_classification(client):
    e = capture(client, MEMORY_BLADE)
    r = client.put(f"/api/entries/{e['id']}", json={"user_confirmed": True}).json()
    assert r["user_confirmed"] and r["title_source"] == "user" and r["entry_type_source"] == "user"


def test_soft_delete_and_restore(client):
    e = capture(client, MEMORY_BLADE)
    assert client.delete(f"/api/entries/{e['id']}").status_code == 204
    assert client.get(f"/api/entries/{e['id']}").status_code == 404
    assert client.get("/api/entries").json()["total"] == 0
    assert client.post("/api/search", json={"query": "memories"}).json()["results"] == []
    r = client.post(f"/api/entries/{e['id']}/restore")
    assert r.status_code == 200 and r.json()["original_text"] == MEMORY_BLADE
    assert len(client.post("/api/search", json={"query": "memories"}).json()["results"]) == 1


def test_list_filters_and_sorting(client):
    a = capture(client, "A story about a lighthouse keeper.", title="Beta")
    b = capture(client, MEMORY_BLADE, title="Alpha")
    client.put(f"/api/entries/{a['id']}", json={"status": "developing"})
    items = client.get("/api/entries?sort=alpha").json()["items"]
    assert [i["title"] for i in items] == ["Alpha", "Beta"]
    assert client.get("/api/entries?sort=oldest").json()["items"][0]["id"] == a["id"]
    assert client.get("/api/entries?sort=modified").json()["items"][0]["id"] == a["id"]
    assert [i["id"] for i in client.get("/api/entries?entry_type=comic").json()["items"]] == [b["id"]]
    assert [i["id"] for i in client.get("/api/entries?status=raw,exploring").json()["items"]] == [b["id"]]
    assert [i["id"] for i in client.get("/api/entries?tag=memory").json()["items"]] == [b["id"]]


def test_relationship_suggestions_are_reviewable(client):
    villain = capture(client, "Villain who collects memories.")
    hero = capture(client, "Hero loses memories every time he travels between dimensions.")
    rels = client.get(f"/api/entries/{hero['id']}/relationships").json()
    assert len(rels) == 1
    rel = rels[0]
    assert rel["other"]["id"] == villain["id"]
    assert rel["origin"] == "ai" and rel["status"] == "suggested"
    assert rel["label"] == "AI suggested relationship"
    assert 0 < rel["confidence"] < 1 and "memory" in rel["rationale"]
    # visible from the other side too
    assert client.get(f"/api/entries/{villain['id']}/relationships").json()[0]["direction"] == "incoming"

    r = client.patch(f"/api/relationships/{rel['id']}", json={"status": "rejected"})
    assert r.json()["status"] == "rejected"
    # rejected links are hidden and never re-suggested
    client.post(f"/api/entries/{hero['id']}/analyze")
    assert client.get(f"/api/entries/{hero['id']}/relationships").json() == []
    assert len(client.get(f"/api/entries/{hero['id']}/relationships?include_rejected=true").json()) == 1


def test_user_relationship(client):
    a = capture(client, "A lighthouse.", analyze=False)
    b = capture(client, "A storm.", analyze=False)
    r = client.post(f"/api/entries/{a['id']}/relationships",
                    json={"target_entry_id": b["id"], "relationship_type": "inspired_by"})
    assert r.status_code == 201
    assert r.json()["origin"] == "user" and r.json()["status"] == "accepted"


def test_projects(client):
    p = client.post("/api/projects", json={"name": "The Hollow Sun", "project_type": "comic",
                                           "description": "A dying sun and the memory thieves."}).json()
    e1 = capture(client, MEMORY_BLADE)
    e2 = capture(client, "A character sketch for the sun priest.", project_ids=[p["id"]])
    r = client.post(f"/api/projects/{p['id']}/entries", json={"entry_id": e1["id"]})
    assert r.status_code == 200 and r.json()["entry_count"] == 2
    q = client.post("/api/projects", json={"name": "Side Quest"}).json()
    client.post(f"/api/projects/{q['id']}/entries", json={"entry_id": e1["id"]})
    detail = client.get(f"/api/entries/{e1['id']}").json()
    assert {x["name"] for x in detail["projects"]} == {"The Hollow Sun", "Side Quest"}
    assert [p_["entry_count"] for p_ in client.get("/api/projects").json()]
    assert client.get(f"/api/entries?project_id={p['id']}").json()["total"] == 2
    assert client.post("/api/entries", json={"original_text": "x", "project_ids": [999]}).status_code == 400
    assert e2["projects"][0]["name"] == "The Hollow Sun"


def test_potential_projects_are_suggestions_only(client):
    p = client.post("/api/projects", json={"name": "Memory Thieves", "description": "Dimensions and memory."}).json()
    e = capture(client, MEMORY_BLADE)
    assert e["projects"] == []
    assert e["ai_metadata"]["potential_projects"][0]["id"] == p["id"]


def test_patterns_require_evidence(client):
    for text in ["A memory thief.", "Forgetting your name on purpose.", "A town where nobody can remember Mondays."]:
        capture(client, text)
    pats = client.get("/api/insights/patterns").json()["patterns"]
    mem = next(p for p in pats if p["name"] == "memory")
    assert mem["count"] == 3 and len(mem["evidence"]) == 3


def test_settings_never_expose_keys(client, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret-123")
    body = client.get("/api/settings").text
    assert "sk-secret-123" not in body


def test_unimplemented_provider_does_not_lose_capture(client, monkeypatch):
    from app.services import ai, organizer

    monkeypatch.setattr(organizer, "get_ai_provider", lambda: ai.AnthropicProvider())
    e = capture(client, MEMORY_BLADE)
    assert e["original_text"] == MEMORY_BLADE
    assert "not implemented" in e["ai_metadata"]["analysis_error"]
    assert client.post(f"/api/entries/{e['id']}/analyze").status_code == 502


def test_host_header_is_restricted(client):
    r = client.get("/api/entries", headers={"host": "evil.example"})
    assert r.status_code == 400


def test_index_page(client):
    r = client.get("/")
    assert r.status_code == 200 and "NEXUS" in r.text
    assert client.get("/static/js/app.js").status_code == 200


def test_data_dirs_exist(client):
    s = get_settings()
    for d in (s.uploads_dir, s.audio_dir, s.images_dir, s.exports_dir):
        assert Path(d).is_dir()


def test_blank_project_names_are_rejected(client):
    assert client.post("/api/projects", json={"name": "   "}).status_code == 422
    p = client.post("/api/projects", json={"name": "  The   Hollow Sun "}).json()
    assert p["name"] == "The Hollow Sun"
    assert client.put(f"/api/projects/{p['id']}", json={"name": "  "}).status_code == 422


def test_project_bible_has_full_entries_oldest_first(client):
    p = client.post("/api/projects", json={"name": "Night Wrestlers"}).json()
    long_text = "El Faro " + "keeps the light burning. " * 30
    a = client.post("/api/entries", json={"original_text": long_text, "project_ids": [p["id"]], "analyze": False}).json()
    b = client.post("/api/entries", json={"original_text": "Second", "project_ids": [p["id"]], "analyze": False}).json()
    client.post("/api/entries", json={"original_text": "Not in it", "analyze": False})
    bible = client.get(f"/api/projects/{p['id']}/bible").json()
    assert bible["project"]["name"] == "Night Wrestlers"
    assert [e["id"] for e in bible["entries"]] == [a["id"], b["id"]]
    assert bible["entries"][0]["original_text"] == long_text  # in full, not an excerpt
