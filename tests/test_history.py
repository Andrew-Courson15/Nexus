def cap(client, text, **kw):
    r = client.post("/api/entries", json={"original_text": text, **kw})
    assert r.status_code == 201, r.text
    return r.json()


MEMORY_BLADE = (
    "I just had an idea for a comic where a guy can cut through dimensions with a sword, "
    "but every time he does it he loses one of his memories."
)


def test_trash_lists_and_restores(client):
    keep = cap(client, "Keep me.")
    gone = cap(client, "Throw me away.")
    assert client.get("/api/trash").json()["total"] == 0
    client.delete(f"/api/entries/{gone['id']}")
    trash = client.get("/api/trash").json()
    assert [e["id"] for e in trash["items"]] == [gone["id"]] and trash["items"][0]["deleted_at"]
    assert [e["id"] for e in client.get("/api/entries").json()["items"]] == [keep["id"]]
    client.post(f"/api/entries/{gone['id']}/restore")
    assert client.get("/api/trash").json()["total"] == 0
    assert client.get(f"/api/entries/{gone['id']}").json()["original_text"] == "Throw me away."


def test_restore_an_earlier_version(client):
    e = cap(client, MEMORY_BLADE)
    ai_title = e["title"]
    tags = [t["name"] for t in e["tags"]]
    assert "weapons" in tags
    client.put(f"/api/entries/{e['id']}", json={"title": "The Memory Blade", "status": "developing",
                                                "user_notes": "Trilogy?",
                                                "tags": [t for t in tags if t != "weapons"]})
    revs = client.get(f"/api/entries/{e['id']}/revisions").json()
    analysed = next(r for r in revs if r["change_description"].startswith("AI analysis"))

    r = client.post(f"/api/entries/{e['id']}/revisions/{analysed['version_number']}/restore")
    assert r.status_code == 200
    back = r.json()
    assert back["title"] == ai_title and back["title_source"] == "ai"
    assert back["status"] == "raw" and back["user_notes"] is None
    assert "weapons" in [t["name"] for t in back["tags"]]
    assert "weapons" not in back["ai_metadata"].get("rejected_tags", [])
    assert back["original_text"] == MEMORY_BLADE

    revs = client.get(f"/api/entries/{e['id']}/revisions").json()
    assert revs[0]["change_description"] == f"Restored version {analysed['version_number']}"
    # nothing was erased: the edited version is still in the history
    assert any(r["content_snapshot"]["title"] == "The Memory Blade" for r in revs)
    # and you can go forward again
    edited = next(r for r in revs if r["content_snapshot"]["title"] == "The Memory Blade")
    again = client.post(f"/api/entries/{e['id']}/revisions/{edited['version_number']}/restore").json()
    assert again["title"] == "The Memory Blade" and again["user_notes"] == "Trilogy?"


def test_restore_same_version_is_a_no_op(client):
    e = cap(client, "Just one idea.")
    revs = client.get(f"/api/entries/{e['id']}/revisions").json()
    client.post(f"/api/entries/{e['id']}/revisions/{revs[0]['version_number']}/restore")
    assert len(client.get(f"/api/entries/{e['id']}/revisions").json()) == len(revs)


def test_restore_unknown_version(client):
    e = cap(client, "Just one idea.")
    assert client.post(f"/api/entries/{e['id']}/revisions/99/restore").status_code == 404


def test_trash_and_restore_a_project_keeps_its_entries(client):
    p = client.post("/api/projects", json={"name": "The Hollow Sun"}).json()
    e = cap(client, "A sun priest who forgets prayers.", project_ids=[p["id"]])
    assert client.delete(f"/api/projects/{p['id']}").status_code == 204
    # hidden everywhere...
    assert client.get("/api/projects").json() == []
    assert client.get(f"/api/projects/{p['id']}").status_code == 404
    assert client.get(f"/api/entries/{e['id']}").json()["projects"] == []
    assert client.post(f"/api/projects/{p['id']}/entries", json={"entry_id": e["id"]}).status_code == 404
    # ...but the entry itself is untouched, and the project is in the trash
    assert client.get(f"/api/entries/{e['id']}").json()["original_text"] == "A sun priest who forgets prayers."
    trashed = client.get("/api/projects?trashed=true").json()
    assert [t["id"] for t in trashed] == [p["id"]] and trashed[0]["deleted_at"] and trashed[0]["entry_count"] == 1
    # restore brings back the project and its membership
    back = client.post(f"/api/projects/{p['id']}/restore").json()
    assert back["entry_count"] == 1
    assert [x["name"] for x in client.get(f"/api/entries/{e['id']}").json()["projects"]] == ["The Hollow Sun"]
