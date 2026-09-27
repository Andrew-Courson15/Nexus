"""Sharing between separate NEXUS installs (.nexus files)."""
import io
import json
import zipfile

import pytest

from app.config import get_settings
from app.services import share
from tests.test_images import _pil


@pytest.fixture()
def other_install():
    """After exporting, pretend to be a different NEXUS (as if the file was sent to a friend)."""
    path = get_settings().data_dir / "install_id"

    def switch():
        path.write_text("f" * 32)
    yield switch
    path.unlink(missing_ok=True)


def export(client, **params):
    r = client.get("/api/share/export", params=params)
    assert r.status_code == 200, r.text
    assert ".nexus" in r.headers["content-disposition"]
    return r.content


def do_import(client, data, name="from-sebas.nexus"):
    return client.post("/api/share/import", files={"file": (name, data, "application/octet-stream")})


def test_project_round_trip_keeps_their_material_and_says_whose_it_is(client, other_install):
    proj = client.post("/api/projects", json={"name": "Hollow Sun", "project_type": "comic"}).json()
    a = client.post("/api/entries", json={"original_text": "The Archivist erases towns.", "title": "The Archivist",
                                          "tags": ["villain"], "project_ids": [proj["id"]]}).json()
    img = client.post("/api/uploads", files={"file": ("guardian.png", _pil("PNG"), "image/png")},
                      data={"note": "Guardian sketch", "project_id": proj["id"]}).json()
    client.post(f"/api/entries/{a['id']}/relationships", json={"target_entry_id": img["id"], "relationship_type": "related_to"})
    data = export(client, project_id=proj["id"], by="Sebas")
    other_install()

    r = do_import(client, data)
    assert r.status_code == 200, r.text
    res = r.json()
    assert res["by"] == "Sebas" and len(res["imported"]) == 2 and res["already_had"] == []
    assert res["project_name"] == "Hollow Sun"  # joined the existing project of the same name
    new = {e["original_text"] or "": e for e in (client.get(f"/api/entries/{i}").json() for i in res["imported"])}
    arch = new["The Archivist erases towns."]
    assert arch["title"] == "The Archivist" and arch["title_source"] == "user"
    assert arch["shared_from"]["by"] == "Sebas" and arch["shared_from"]["entry_id"] == a["id"]
    assert "villain" in [t["name"] for t in arch["tags"] if t["source"] == "user"]
    pic = new["Guardian sketch"]
    assert pic["source_type"] == "image" and pic["attachments"][0]["sha256"] == img["attachments"][0]["sha256"]
    assert client.get(pic["attachments"][0]["url"]).content == client.get(img["attachments"][0]["url"]).content
    assert client.get(pic["attachments"][0]["thumb_url"]).status_code == 200
    rels = client.get(f"/api/entries/{arch['id']}/relationships").json()
    assert any(x["other"]["id"] == pic["id"] and x["origin"] == "user" for x in rels)
    assert pic["processing_status"] == "done"  # our own AI organized it

    again = do_import(client, data).json()  # sending the same file twice adds nothing
    assert again["imported"] == [] and sorted(again["already_had"]) == sorted(res["imported"])


def test_ai_guesses_are_not_exported_as_if_they_were_the_users(client):
    e = client.post("/api/entries", json={"original_text": "A lighthouse keeper who collects storms."}).json()
    assert e["title_source"] == "ai"
    data = export(client, entry_ids=str(e["id"]))
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        item = json.loads(zf.read("manifest.json"))["entries"][0]
    assert item["title"] is None and item["entry_type"] is None
    assert item["original_text"] == "A lighthouse keeper who collects storms."
    assert "ai_summary" not in item


def test_importing_your_own_export_is_refused(client):
    e = client.post("/api/entries", json={"original_text": "Mine."}).json()
    r = do_import(client, export(client, entry_ids=str(e["id"])))
    assert r.status_code == 400 and "same NEXUS" in r.json()["detail"]


def test_damaged_files_are_refused_and_leave_nothing_behind(client, other_install):
    img = client.post("/api/uploads", files={"file": ("a.png", _pil("PNG"), "image/png")}).json()
    data = export(client, entry_ids=str(img["id"]))
    other_install()
    # change the picture inside the file: its fingerprint no longer matches
    src, out = zipfile.ZipFile(io.BytesIO(data)), io.BytesIO()
    with zipfile.ZipFile(out, "w") as zf:
        for info in src.infolist():
            body = src.read(info.filename)
            zf.writestr(info.filename, body + b"tampered" if info.filename.startswith("files/") else body)
    before = sorted(get_settings().images_dir.iterdir())
    r = do_import(client, out.getvalue())
    assert r.status_code == 400 and "damaged" in r.json()["detail"]
    assert sorted(get_settings().images_dir.iterdir()) == before
    assert client.get("/api/entries").json()["total"] == 1


def test_unsafe_paths_and_non_share_files_are_refused(client, other_install):
    other_install()
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as zf:
        zf.writestr("manifest.json", json.dumps({
            "format": "nexus-share", "version": 1, "install_id": "a" * 32, "by": "x",
            "entries": [{"entry_id": 1, "attachments": [{"file": "files/../../evil.txt", "kind": "file"}]}]}))
        zf.writestr("files/../../evil.txt", "x")
    assert do_import(client, out.getvalue()).status_code == 400
    assert do_import(client, b"not a zip").status_code == 400
    plain = io.BytesIO()
    with zipfile.ZipFile(plain, "w") as zf:
        zf.writestr("hello.txt", "hi")
    assert do_import(client, plain.getvalue()).status_code == 400


def test_nothing_to_export(client):
    assert client.get("/api/share/export", params={"entry_ids": "999"}).status_code == 400
    assert client.get("/api/share/export", params={"project_id": 999}).status_code == 400


def test_their_notes_stay_theirs(client, other_install):
    e = client.post("/api/entries", json={"original_text": "Sky whales migrate through cities."}).json()
    client.put(f"/api/entries/{e['id']}", json={"user_notes": "Maybe they sing at dawn."})
    data = export(client, entry_ids=str(e["id"]), by="Sebas")
    other_install()
    new = client.get(f"/api/entries/{do_import(client, data).json()['imported'][0]}").json()
    assert new["user_notes"] is None                          # "your notes" are still only yours
    assert new["shared_from"]["notes"] == "Maybe they sing at dawn."
    assert "Maybe they sing at dawn." in {r["entry"]["id"]: r for r in client.post(
        "/api/search", json={"query": "dawn"}).json()["results"]}[new["id"]]["snippet"].replace("\x01", "").replace("\x02", "")


def test_imported_files_get_their_type_from_what_they_are_not_the_label(client, other_install):
    other_install()
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as zf:
        zf.writestr("manifest.json", json.dumps({
            "format": "nexus-share", "version": 1, "install_id": "a" * 32, "by": "x",
            "entries": [{"entry_id": 1, "source_type": "image", "attachments": [
                {"file": "files/1/0-page.html", "kind": "image", "mime_type": "image/png", "original_filename": "page.html"}]}]}))
        zf.writestr("files/1/0-page.html", "<script>alert(1)</script>")
    r = do_import(client, out.getvalue())
    assert r.status_code == 200, r.text
    att = client.get(f"/api/entries/{r.json()['imported'][0]}").json()["attachments"][0]
    assert att["kind"] == "file" and att["mime_type"] == "text/html"
    served = client.get(att["url"])
    assert "attachment" in served.headers["content-disposition"] and served.headers["content-security-policy"] == "sandbox"
