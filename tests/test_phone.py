"""Phone app support: installable app files and safe offline re-sends."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, inspect, text

from app.database import Base, add_missing_columns, ensure_unique_indexes

PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360000002000100e221bc330000000049454e44ae426082"
)


def test_resending_a_text_capture_saves_it_once(client):
    body = {"original_text": "An idea from the train.", "client_id": "phone-abc123def",
            "captured_at": "2026-09-20T08:15:00Z"}
    first = client.post("/api/entries", json=body)
    again = client.post("/api/entries", json=body)
    assert first.status_code == 201 and again.status_code == 200
    assert first.json()["id"] == again.json()["id"]
    assert client.get("/api/entries").json()["total"] == 1
    # keeps the time the idea was actually captured on the phone
    assert first.json()["created_at"].startswith("2026-09-20T08:15:00")


def test_resending_an_upload_saves_it_once(client):
    def send():
        return client.post("/api/uploads", files={"file": ("photo.png", PNG, "image/png")},
                           data={"client_id": "phone-upload-0001", "captured_at": "2026-09-21T10:00:00Z"})
    a, b = send(), send()
    assert a.status_code == 201 and b.status_code == 200 and a.json()["id"] == b.json()["id"]
    assert client.get("/api/entries").json()["total"] == 1


def test_capture_time_from_the_future_is_clamped(client):
    future = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
    e = client.post("/api/entries", json={"original_text": "Clock is wrong.", "client_id": "phone-future-1",
                                          "captured_at": future}).json()
    assert datetime.fromisoformat(e["created_at"].replace("Z", "+00:00")) <= datetime.now(timezone.utc)


def test_bad_client_ids_are_rejected(client):
    r = client.post("/api/entries", json={"original_text": "x", "client_id": "../../etc"})
    assert r.status_code == 422


def test_trashed_duplicate_is_not_recreated(client):
    body = {"original_text": "Sent, then trashed.", "client_id": "phone-trash-0001"}
    e = client.post("/api/entries", json=body).json()
    client.delete(f"/api/entries/{e['id']}")
    again = client.post("/api/entries", json=body)
    assert again.status_code == 200 and again.json()["id"] == e["id"]
    assert client.get("/api/entries").json()["total"] == 0  # still in the trash, not duplicated


def test_installable_app_files(client):
    sw = client.get("/sw.js")
    assert sw.status_code == 200 and sw.headers["service-worker-allowed"] == "/"
    assert "__VERSION__" not in sw.text and "nexus-" in sw.text
    manifest = client.get("/static/manifest.webmanifest").json()
    assert manifest["display"] == "standalone" and manifest["start_url"] == "/"
    for icon in manifest["icons"]:
        assert client.get(icon["src"]).status_code == 200
    page = client.get("/").text
    assert 'rel="manifest"' in page and "apple-touch-icon" in page and "viewport-fit=cover" in page


def test_client_id_is_added_and_unique_on_old_databases(tmp_path):
    """A database made before phone support gains the column and its uniqueness rule."""
    import re

    from sqlalchemy.schema import CreateTable

    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    entries = Base.metadata.tables["creative_entries"]
    ddl = str(CreateTable(entries).compile(engine))
    ddl = re.sub(r"\s*client_id VARCHAR\(64\),", "", ddl)
    ddl = re.sub(r",\s*UNIQUE \(client_id\)", "", ddl)
    assert "client_id" not in ddl
    with engine.begin() as conn:
        conn.execute(text(ddl))
        Base.metadata.create_all(conn)  # the other tables
        assert "client_id" not in {c["name"] for c in inspect(conn).get_columns("creative_entries")}
    with engine.begin() as conn:
        assert "creative_entries.client_id" in add_missing_columns(conn, Base.metadata)
        ensure_unique_indexes(conn, Base.metadata)
        conn.execute(text("INSERT INTO creative_entries (title, title_source, entry_type, entry_type_source, status, "
                          "ai_metadata, user_confirmed, source_type, client_id, created_at, updated_at) VALUES "
                          "('a','ai','idea','ai','raw','{}',0,'text','dup-client-1','2026-01-01','2026-01-01')"))
    import pytest
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(text("INSERT INTO creative_entries (title, title_source, entry_type, entry_type_source, status, "
                          "ai_metadata, user_confirmed, source_type, client_id, created_at, updated_at) VALUES "
                          "('b','ai','idea','ai','raw','{}',0,'text','dup-client-1','2026-01-01','2026-01-01')"))
    engine.dispose()
