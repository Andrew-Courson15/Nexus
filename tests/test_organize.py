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


def test_delete_forever_only_from_the_trash_and_files_go_too(client):
    from app.config import get_settings
    from tests.test_images import _pil

    img = client.post("/api/uploads", files={"file": ("scan.tiff", _pil("TIFF"), "image/tiff")}).json()
    other = cap(client, "Keeps its link target")
    client.post(f"/api/entries/{other['id']}/relationships", json={"target_entry_id": img["id"]})
    client.get(img["attachments"][0]["thumb_url"])
    data = get_settings().data_dir
    original = data / img["source_file"]
    assert original.is_file() and list((data / "previews" / "thumbs").glob(f"{img['attachments'][0]['id']}-*"))
    assert client.delete(f"/api/trash/entries/{img['id']}").status_code == 400  # not in the trash yet
    client.delete(f"/api/entries/{img['id']}")
    assert client.delete(f"/api/trash/entries/{img['id']}").json() == {"deleted": 1}
    assert not original.exists() and not list((data / "previews" / "thumbs").glob(f"{img['attachments'][0]['id']}-*"))
    assert client.get(f"/api/entries/{img['id']}").status_code == 404
    assert client.get(f"/api/entries/{other['id']}/relationships").json() == []
    assert client.get(f"/api/entries/{other['id']}").status_code == 200  # other entries untouched


def test_empty_trash(client):
    a, b = cap(client, "Keep"), cap(client, "Toss")
    p = client.post("/api/projects", json={"name": "Old project"}).json()
    client.delete(f"/api/entries/{b['id']}")
    client.delete(f"/api/projects/{p['id']}")
    assert client.post("/api/trash/empty").json() == {"entries": 1, "projects": 1}
    assert client.get("/api/trash").json()["total"] == 0 and client.get("/api/projects?trashed=true").json() == []
    assert [e["id"] for e in client.get("/api/entries").json()["items"]] == [a["id"]]


def test_uploading_the_same_file_twice_is_kept_but_flagged(client):
    from tests.test_images import _pil

    data = _pil("PNG")
    first = client.post("/api/uploads", files={"file": ("a.png", data, "image/png")}).json()
    second = client.post("/api/uploads", files={"file": ("copy.png", data, "image/png")}).json()
    assert "duplicate_of" not in first["ai_metadata"]
    assert second["ai_metadata"]["duplicate_of"]["id"] == first["id"]
    assert client.get("/api/entries").json()["total"] == 2


def test_duplicates_are_grouped_oldest_first(client):
    import io

    from PIL import Image

    def png(color):
        b = io.BytesIO()
        Image.new("RGB", (10, 10), color).save(b, "PNG")
        return b.getvalue()

    up = lambda name, data, **kw: client.post("/api/uploads", files={"file": (name, data, "image/png")},  # noqa: E731
                                              data={"analyze": "false", **kw}).json()
    a = up("faro.png", png("red"))
    b = up("faro copy.png", png("red"), note="second upload, with words")
    c = up("faro again.png", png("red"))
    up("other.png", png("blue"))
    d = client.get("/api/duplicates").json()
    assert d["extra_copies"] == 2 and len(d["groups"]) == 1
    g = d["groups"][0]
    assert [e["id"] for e in g["entries"]] == [a["id"], b["id"], c["id"]] and g["filename"] == "faro.png"
    assert [e["has_notes"] for e in g["entries"]] == [False, True, False]
    client.post("/api/entries/bulk", json={"entry_ids": [b["id"], c["id"]], "action": "trash"})
    assert client.get("/api/duplicates").json() == {"groups": [], "extra_copies": 0}
