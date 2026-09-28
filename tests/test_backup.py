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


def _own_data(tmp_path, monkeypatch):
    data = tmp_path / "data"
    (data / "images").mkdir(parents=True)
    monkeypatch.setenv("NEXUS_DATA_DIR", str(data))
    monkeypatch.delenv("NEXUS_DATABASE_URL", raising=False)
    get_settings.cache_clear()
    con = sqlite3.connect(data / "nexus.db")
    con.execute("CREATE TABLE t (v TEXT)")
    con.execute("INSERT INTO t VALUES ('before')")
    con.commit()
    con.close()
    (data / "images" / "a.png").write_bytes(b"original picture")
    return data


def test_a_failed_restore_puts_everything_back(tmp_path, monkeypatch):
    try:
        data = _own_data(tmp_path, monkeypatch)
        zipped = backup.create_backup()
        (data / "images" / "b.png").write_bytes(b"new picture")

        def broken(*_a, **_k):
            raise OSError("disk full")
        monkeypatch.setattr(backup.shutil, "copyfileobj", broken)
        with pytest.raises(OSError):
            backup.restore(zipped)
        assert (data / "images" / "b.png").read_bytes() == b"new picture"
        assert (data / "nexus.db").exists()
        assert not list(tmp_path.glob("data-before-restore-*"))
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()


def test_a_staged_backup_is_restored_when_nexus_starts(tmp_path, monkeypatch):
    try:
        data = _own_data(tmp_path, monkeypatch)
        zipped = backup.create_backup()
        con = sqlite3.connect(data / "nexus.db")
        con.execute("UPDATE t SET v = 'after'")
        con.commit()
        con.close()
        with open(zipped, "rb") as f:
            info = backup.stage_restore(f, "old-nexus.zip")
        assert info["filename"] == "old-nexus.zip" and backup.pending_restore()["filename"] == "old-nexus.zip"

        result = backup.apply_pending_restore()
        assert result["ok"] and backup.pending_restore() is None
        con = sqlite3.connect(data / "nexus.db")
        assert con.execute("SELECT v FROM t").fetchone()[0] == "before"
        con.close()
        assert backup.last_restore()["previous_data"] == result["previous_data"]
        assert backup.apply_pending_restore() is None  # only once
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()


def test_restore_from_a_file_in_settings(client, monkeypatch):
    from app.services import lock

    monkeypatch.setattr(lock, "LOOPBACK", lock.LOOPBACK | {"testclient"})
    monkeypatch.setattr(lock, "_LOCAL_NAMES", lock._LOCAL_NAMES | {"testserver"})
    backup.create_backup()
    path = get_settings().backups_dir / backup.list_backups()[0]["name"]
    try:
        r = client.post("/api/backups/restore", files={"file": ("mine.zip", path.read_bytes(), "application/zip")})
        assert r.status_code == 200 and r.json()["restore_pending"]["filename"] == "mine.zip"
        assert client.get("/api/backups").json()["restore_pending"]["filename"] == "mine.zip"
        assert client.delete("/api/backups/restore").json() == {"restore_pending": None}
        assert client.get("/api/backups").json()["restore_pending"] is None

        r = client.post("/api/backups/restore", files={"file": ("x.zip", b"not a zip", "application/zip")})
        assert r.status_code == 400 and "zip" in r.json()["detail"]
        assert backup.pending_restore() is None

        phone = {"host": "localhost", "x-forwarded-for": "100.64.0.7"}
        r = client.post("/api/backups/restore", files={"file": ("mine.zip", path.read_bytes(), "application/zip")},
                        headers=phone)
        assert r.status_code == 403 and backup.pending_restore() is None
    finally:
        backup.cancel_restore()


def test_backups_with_unexpected_or_unsafe_contents_are_refused(tmp_path):
    for extra in ("../evil.txt", "C:/evil.txt", "notes/other.txt"):
        bad = tmp_path / "bad.zip"
        with zipfile.ZipFile(bad, "w") as zf:
            zf.writestr("nexus.db", backup.SQLITE_MAGIC + b"rest")
            zf.writestr("backup.json", json.dumps({"app": "NEXUS"}))
            zf.writestr(extra, "x")
        with pytest.raises(backup.BackupError):
            backup.check_backup(bad)


def test_the_app_finds_an_archive_in_a_nexus_folder_and_brings_it(client, tmp_path, monkeypatch):
    from app.services import lock

    monkeypatch.setattr(lock, "LOOPBACK", lock.LOOPBACK | {"testclient"})
    monkeypatch.setattr(lock, "_LOCAL_NAMES", lock._LOCAL_NAMES | {"testserver"})
    old = tmp_path / "nexus" / "data"
    (old / "images").mkdir(parents=True)
    con = sqlite3.connect(old / "nexus.db")
    con.execute("CREATE TABLE creative_entries (id INTEGER PRIMARY KEY, original_text TEXT)")
    con.executemany("INSERT INTO creative_entries (original_text) VALUES (?)", [("one",), ("two",)])
    con.commit()
    con.close()
    (old / "images" / "sketch.png").write_bytes(b"a sketch")
    monkeypatch.setattr(backup, "_candidate_dirs", lambda: [tmp_path / "missing" / "data", old, old])

    assert client.get("/api/backups/found").json() == {"archives": []}  # a NEXUS folder doesn't look
    monkeypatch.setattr(backup, "LOOK_FOR_OTHER_ARCHIVES", True)
    found = client.get("/api/backups/found").json()["archives"]
    assert [(a["folder"], a["entries"]) for a in found] == [(str((tmp_path / "nexus").resolve()), 2)]
    try:
        assert client.post("/api/backups/found", json={"folder": "/etc"}).status_code == 400
        r = client.post("/api/backups/found", json={"folder": found[0]["folder"]})
        assert r.status_code == 200 and r.json()["restore_pending"]["entries"] == 2
        with zipfile.ZipFile(get_settings().data_dir / "restore-pending.zip") as zf:
            assert zf.read("images/sketch.png") == b"a sketch"
        assert (old / "nexus.db").exists() and (old / "images" / "sketch.png").exists()  # the folder isn't touched
        assert client.get("/api/backups").json()["can_restart"] is False
        assert client.post("/api/restart").status_code == 400
    finally:
        backup.cancel_restore()


def test_restart_from_the_page_uses_the_apps_hook(client, monkeypatch):
    import time

    from app.services import lifecycle, lock

    monkeypatch.setattr(lock, "LOOPBACK", lock.LOOPBACK | {"testclient"})
    monkeypatch.setattr(lock, "_LOCAL_NAMES", lock._LOCAL_NAMES | {"testserver"})
    calls = []
    monkeypatch.setattr(lifecycle, "restart_hook", lambda: calls.append(1))
    assert client.post("/api/restart", headers={"x-forwarded-for": "100.64.0.7"}).status_code == 403
    assert client.post("/api/restart").status_code == 202
    for _ in range(50):
        if calls:
            break
        time.sleep(0.05)
    assert calls == [1]


def test_settings_come_along_and_a_folders_env_fills_the_gaps(tmp_path, monkeypatch):
    try:
        data = _own_data(tmp_path, monkeypatch)
        (data / "lock.json").write_text('{"hash": "old"}')
        (data / "preferences.json").write_text('{"ollama_model": "llama3.2"}')
        zipped = backup.create_backup()
        with zipfile.ZipFile(zipped) as zf:
            assert {"settings/lock.json", "settings/preferences.json"} <= set(zf.namelist())
        (data / "lock.json").write_text('{"hash": "new"}')
        (data / "phone.json").write_text('{"host": "other.tail1.ts.net"}')  # not in the backup: left alone
        aside = backup.restore(zipped)
        assert (data / "lock.json").read_text() == '{"hash": "old"}' and (aside / "lock.json").read_text() == '{"hash": "new"}'
        assert (data / "phone.json").exists()

        folder = tmp_path / "nexus"
        (folder / "data").mkdir(parents=True)
        shutil.copy(data / "nexus.db", folder / "data" / "nexus.db")
        (folder / "data" / "preferences.json").write_text('{"ollama_model": "llama3.2"}')
        (folder / ".env").write_text("NEXUS_ALLOWED_HOSTS=localhost,gaming-pc.tail1234.ts.net\nNEXUS_OWNER_NAME=Andrew\n")
        got = backup._settings_from_env(folder, folder / "data")
        assert json.loads(got["phone.json"]) == {"host": "gaming-pc.tail1234.ts.net"}
        assert json.loads(got["preferences.json"]) == {"ollama_model": "llama3.2", "owner_name": "Andrew"}
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()
