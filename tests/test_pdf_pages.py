"""Splitting a multi-page PDF into one picture entry per page."""
import hashlib

import pytest

pymupdf = pytest.importorskip("pymupdf")


def _pdf(pages: int) -> bytes:
    doc = pymupdf.open()
    for i in range(pages):
        page = doc.new_page(width=200, height=280)
        page.insert_text((40, 60), f"Panel {i + 1}")
    data = doc.tobytes()
    doc.close()
    return data


def _upload(client, data, **form):
    return client.post("/api/uploads", files={"file": ("Night Wrestlers.pdf", data, "application/pdf")},
                       data={"analyze": "false", **form}).json()


def test_split_makes_linked_page_entries_and_keeps_the_pdf(client):
    from app.config import get_settings

    proj = client.post("/api/projects", json={"name": "Night Wrestlers"}).json()
    data = _pdf(3)
    pdf = _upload(client, data, project_id=str(proj["id"]))
    r = client.post(f"/api/entries/{pdf['id']}/split-pages")
    assert r.status_code == 200, r.text
    pages = r.json()
    assert [p["title"] for p in pages] == [f"Night Wrestlers — page {i}" for i in (1, 2, 3)]
    assert all(p["attachments"][0]["mime_type"] == "image/jpeg" and p["attachments"][0]["thumb_url"] for p in pages)
    assert all(p["projects"] == [{"id": proj["id"], "name": "Night Wrestlers"}] for p in pages)
    assert pages[1]["ai_metadata"]["page_of"] == {"id": pdf["id"], "page": 2, "pages": 3}
    rels = client.get(f"/api/entries/{pdf['id']}/relationships").json()
    assert sorted(r["other"]["id"] for r in rels) == [p["id"] for p in pages]
    assert {r["relationship_type"] for r in rels} == {"part_of"}
    # The PDF itself is untouched.
    stored = client.get(pdf["attachments"][0]["url"]).content
    assert hashlib.sha256(stored).hexdigest() == hashlib.sha256(data).hexdigest()
    # Repeating it doesn't make duplicates.
    again = client.post(f"/api/entries/{pdf['id']}/split-pages").json()
    assert [p["id"] for p in again] == [p["id"] for p in pages]
    assert client.get("/api/entries").json()["total"] == 4
    assert get_settings().images_dir.exists()


def test_single_page_and_non_pdf_are_refused(client):
    one = _upload(client, _pdf(1))
    r = client.post(f"/api/entries/{one['id']}/split-pages")
    assert r.status_code == 400 and "only one page" in r.json()["detail"]
    text = client.post("/api/entries", json={"original_text": "x", "analyze": False}).json()
    assert client.post(f"/api/entries/{text['id']}/split-pages").status_code == 400


def test_restoring_an_old_version_of_the_pdf_never_duplicates_pages(client):
    pdf = _upload(client, _pdf(2))
    pages = client.post(f"/api/entries/{pdf['id']}/split-pages").json()
    first = client.get(f"/api/entries/{pdf['id']}/revisions").json()[-1]["version_number"]
    client.post(f"/api/entries/{pdf['id']}/revisions/{first}/restore")
    again = client.post(f"/api/entries/{pdf['id']}/split-pages").json()
    assert [p["id"] for p in again] == [p["id"] for p in pages]
    assert client.get(f"/api/entries/{pdf['id']}").json()["ai_metadata"]["pages"] == [p["id"] for p in pages]
    assert client.get("/api/entries").json()["total"] == 3
