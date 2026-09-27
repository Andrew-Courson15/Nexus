"""Bulk actions on entries, and tag management."""


def cap(client, text, **kw):
    return client.post("/api/entries", json={"original_text": text, "analyze": False, **kw}).json()


def test_bulk_actions_touch_only_chosen_live_entries_and_keep_history(client):
    p = client.post("/api/projects", json={"name": "Hollow Sun"}).json()
    a, b, c = cap(client, "One"), cap(client, "Two"), cap(client, "Three")
    r = client.post("/api/entries/bulk", json={"entry_ids": [a["id"], b["id"]], "action": "add_to_project", "project_id": p["id"]})
    assert r.json()["changed"] == 2
    assert client.get(f"/api/projects/{p['id']}").json()["entry_count"] == 2
    again = client.post("/api/entries/bulk", json={"entry_ids": [a["id"], b["id"]], "action": "add_to_project", "project_id": p["id"]})
    assert again.json()["changed"] == 0  # nothing to do twice
    client.post("/api/entries/bulk", json={"entry_ids": [a["id"], c["id"]], "action": "set_status", "status": "developing"})
    client.post("/api/entries/bulk", json={"entry_ids": [a["id"], b["id"], c["id"]], "action": "add_tag", "tag": " Night Wrestlers "})
    ea = client.get(f"/api/entries/{a['id']}").json()
    assert ea["status"] == "developing" and {"name": "night wrestlers", "source": "user"} in ea["tags"]
    revs = [r["change_description"] for r in client.get(f"/api/entries/{a['id']}/revisions").json()]
    assert any("Added to project" in x for x in revs) and any("Tagged" in x for x in revs)
    client.post("/api/entries/bulk", json={"entry_ids": [b["id"], c["id"]], "action": "trash"})
    assert [e["id"] for e in client.get("/api/entries").json()["items"]] == [a["id"]]
    assert client.get("/api/trash").json()["total"] == 2
    assert client.post("/api/entries/bulk", json={"entry_ids": [a["id"]], "action": "remove_from_project",
                                                  "project_id": p["id"]}).json()["changed"] == 1


def test_bulk_needs_what_it_needs(client):
    a = cap(client, "One")
    assert client.post("/api/entries/bulk", json={"entry_ids": [a["id"]], "action": "add_to_project"}).status_code == 400
    assert client.post("/api/entries/bulk", json={"entry_ids": [a["id"]], "action": "set_status"}).status_code == 400
    assert client.post("/api/entries/bulk", json={"entry_ids": [a["id"]], "action": "add_tag", "tag": "  "}).status_code == 400
    assert client.post("/api/entries/bulk", json={"entry_ids": [], "action": "trash"}).status_code == 422


def test_rename_and_merge_tags(client):
    a = cap(client, "One", tags=["villian"])
    b = cap(client, "Two", tags=["villain", "villian"])
    r = client.patch("/api/tags/villian", json={"new_name": "Villain"})
    assert r.status_code == 200 and r.json() == {"changed": 2, "merged": True}
    tags = {t["name"]: t["count"] for t in client.get("/api/tags").json()}
    assert "villian" not in tags and tags["villain"] == 2
    assert [t["name"] for t in client.get(f"/api/entries/{b['id']}").json()["tags"]].count("villain") == 1
    assert client.patch("/api/tags/villain", json={"new_name": "antagonist"}).json() == {"changed": 2, "merged": False}
    res = client.post("/api/search", json={"query": "antagonist"}).json()["results"]
    assert {x["entry"]["id"] for x in res} == {a["id"], b["id"]}  # the word index follows the rename


def test_deleting_an_ai_tag_keeps_it_from_coming_back(client):
    e = client.post("/api/entries", json={"original_text": "A monster made of memories and dimensions."}).json()
    ai_tag = next(t["name"] for t in e["tags"] if t["source"] == "ai")
    assert client.delete(f"/api/tags/{ai_tag}").json()["changed"] == 1
    client.post(f"/api/entries/{e['id']}/analyze")
    again = client.get(f"/api/entries/{e['id']}").json()
    assert ai_tag not in [t["name"] for t in again["tags"]]
    assert client.delete("/api/tags/does-not-exist").status_code == 404
