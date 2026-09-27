"""Readable export: Markdown files + originals, with the same provenance labels as the app."""
import io
import zipfile

from tests.test_images import _pil


def test_export_has_readable_entries_projects_and_originals(client):
    p = client.post("/api/projects", json={"name": "Night Wrestlers", "description": "Luchadors vs spirits"}).json()
    a = client.post("/api/entries", json={"original_text": "El Faro wears a lighthouse mask.", "title": "El Faro",
                                          "tags": ["hero"], "project_ids": [p["id"]]}).json()
    client.put(f"/api/entries/{a['id']}", json={"user_notes": "Maybe he's old."})
    img = client.post("/api/uploads", files={"file": ("mask v2.png", _pil("PNG"), "image/png")}).json()
    client.post(f"/api/entries/{a['id']}/relationships", json={"target_entry_id": img["id"], "relationship_type": "related_to"})
    trashed = client.post("/api/entries", json={"original_text": "Gone"}).json()
    client.delete(f"/api/entries/{trashed['id']}")

    r = client.get("/api/export/markdown")
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    names = zf.namelist()
    root = names[0].split("/")[0]
    assert root.startswith("NEXUS export")
    entry_md = next(n for n in names if n.endswith(f"El Faro (#{a['id']}).md"))
    text = zf.read(entry_md).decode()
    assert 'title: "El Faro"' in text and "title_by: you" in text and '"hero"' in text.split("tags:")[1].split("\n")[0]
    assert f"%23{img['id']}%29.md)" in text  # links survive the '#' in file names
    assert "## Your words\n*exactly as captured, never modified*\n\nEl Faro wears a lighthouse mask." in text
    assert "## Notes\nMaybe he's old." in text
    assert "## AI summary\n*written by the AI, not by you*" in text
    assert "## Related" in text and "(you)" in text
    assert f"{root}/files/{img['id']}/1-mask v2.png" in names
    assert zf.read(f"{root}/files/{img['id']}/1-mask v2.png") == client.get(img["attachments"][0]["url"]).content
    assert any(n.endswith("projects/Night Wrestlers.md") for n in names)
    readme = zf.read(f"{root}/README.md").decode()
    assert "El Faro" in readme and "Gone" not in readme and "Night Wrestlers" in readme


def test_export_follows_project_order_and_labels_ai_suggestions(client):
    import io
    import zipfile

    from app.database import SessionLocal
    from app.models import CreativeEntry

    p = client.post("/api/projects", json={"name": "Night Wrestlers"}).json()
    a = client.post("/api/entries", json={"original_text": "Cover", "title": "Cover", "project_ids": [p["id"]], "analyze": False}).json()
    b = client.post("/api/entries", json={"original_text": "Page one", "title": "Page one", "project_ids": [p["id"]], "analyze": False}).json()
    client.put(f"/api/projects/{p['id']}/order", json={"entry_ids": [a["id"], b["id"]]})
    with SessionLocal() as db:
        e = db.get(CreativeEntry, b["id"])
        e.ai_metadata = {**(e.ai_metadata or {}), "developments": [{"directions": [{"name": "Told backwards", "description": "Start at the end."}],
                                                                     "questions": ["Who wins?"], "next_step": "Sketch it.", "created_at": "2026-09-27T09:00:00"}]}
        db.commit()
    zf = zipfile.ZipFile(io.BytesIO(client.get("/api/export/markdown").content))
    project = next(zf.read(n).decode() for n in zf.namelist() if n.endswith("projects/Night Wrestlers.md"))
    assert project.index("Cover") < project.index("Page one")  # your order, not newest first
    page = next(zf.read(n).decode() for n in zf.namelist() if "/entries/" in n and "Page one" in zf.read(n).decode())
    assert "AI suggestions to develop this idea" in page and "options from the AI, not your words" in page
