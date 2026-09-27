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
