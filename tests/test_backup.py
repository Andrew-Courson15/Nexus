"""Backups: everything that can't be rebuilt, in one zip; restore never deletes current data."""
import json
import shutil
import sqlite3
import zipfile

import pytest

from app.config import get_settings
from app.services import backup
from tests.test_images import _pil


@pytest.fixture(autouse=True)
def clean_backups():
    shutil.rmtree(get_settings().backups_dir, ignore_errors=True)
    yield
    shutil.rmtree(get_settings().backups_dir, ignore_errors=True)


def test_backup_holds_the_database_and_every_original(client):
    client.post("/api/entries", json={"original_text": "A dragon who hoards memories."})
    img = client.post("/api/uploads", files={"file": ("scan.tiff", _pil("TIFF"), "image/tiff")}).json()
    r = client.post("/api/backups")
    assert r.status_code == 200 and r.json()["started"] and not r.json()["last_error"]
    listing = client.get("/api/backups").json()
    assert len(listing["backups"]) == 1 and not listing["running"] and listing["folder_is_inside_data"]
    name = listing["backups"][0]["name"]
    data = client.get(f"/api/backups/{name}")
    assert data.status_code == 200 and data.headers["content-type"] == "application/zip"
    path = get_settings().backups_dir / name
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
        assert "nexus.db" in names and "backup.json" in names
        assert img["source_file"] in names                      # the original TIFF, exactly as uploaded
        assert zf.read(img["source_file"]) == (get_settings().data_dir / img["source_file"]).read_bytes()
        assert not any(n.startswith("previews/") for n in names)  # derived: rebuilt, not backed up
        assert json.loads(zf.read("backup.json"))["original_files"] >= 1
        zf.extract("nexus.db", path.parent / "check")
    con = sqlite3.connect(path.parent / "check" / "nexus.db")
    texts = [r[0] for r in con.execute("SELECT original_text FROM creative_entries")]
    con.close()
    assert "A dragon who hoards memories." in texts


def test_only_real_backup_names_can_be_downloaded(client):
    for bad in ("..%2F..%2Fnexus.db", "nexus.db", "nexus-backup-x.zip"):
        assert client.get(f"/api/backups/{bad}").status_code == 404


def test_old_backups_are_pruned(client, monkeypatch):
    monkeypatch.setenv("NEXUS_BACKUP_KEEP", "2")
    get_settings.cache_clear()
    try:
        folder = get_settings().backups_dir
        folder.mkdir(parents=True, exist_ok=True)
        for stamp in ("20240101-000000", "20240201-000000-auto", "20240301-000000"):
            (folder / f"nexus-backup-{stamp}.zip").write_bytes(b"old")
        backup.create_backup()
        names = [b["name"] for b in backup.list_backups()]
        assert len(names) == 2 and names[1] == "nexus-backup-20240301-000000.zip"
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()


def test_automatic_backup_is_due_only_when_old_enough(client):
    assert backup.runner.due()
    backup.create_backup(automatic=True)
    assert not backup.runner.due()
    assert backup.list_backups()[0]["automatic"]


def test_restore_moves_current_data_aside_and_brings_the_backup_back(tmp_path, monkeypatch):
    data = tmp_path / "data"
    (data / "images").mkdir(parents=True)
    monkeypatch.setenv("NEXUS_DATA_DIR", str(data))
    monkeypatch.delenv("NEXUS_DATABASE_URL", raising=False)
    get_settings.cache_clear()
    try:
        con = sqlite3.connect(data / "nexus.db")
        con.execute("CREATE TABLE t (v TEXT)")
        con.execute("INSERT INTO t VALUES ('before')")
        con.commit()
        (data / "images" / "a.png").write_bytes(b"original picture")
        zipped = backup.create_backup()
        # things change after the backup...
        con.execute("UPDATE t SET v = 'after'")
        con.commit()
        con.close()
        (data / "images" / "b.png").write_bytes(b"new picture")

        aside = backup.restore(zipped)
        con = sqlite3.connect(data / "nexus.db")
        assert con.execute("SELECT v FROM t").fetchone()[0] == "before"
        con.close()
        assert (data / "images" / "a.png").read_bytes() == b"original picture"
        assert not (data / "images" / "b.png").exists()
        assert (aside / "images" / "b.png").read_bytes() == b"new picture"  # nothing is ever deleted
        assert (aside / "nexus.db").exists()
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()


def test_restore_refuses_files_that_are_not_backups(tmp_path):
    bad = tmp_path / "x.zip"
    with zipfile.ZipFile(bad, "w") as zf:
        zf.writestr("hello.txt", "hi")
    with pytest.raises(backup.BackupError):
        backup.restore(bad)
