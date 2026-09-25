import hashlib
from pathlib import Path

from app.config import get_settings

PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360000002000100e221bc330000000049454e44ae426082"
)


def test_image_upload_stores_original_untouched(client):
    r = client.post("/api/uploads", files={"file": ("my sketch.png", PNG, "image/png")})
    assert r.status_code == 201, r.text
    e = r.json()
    assert e["source_type"] == "image"
    assert e["source_file"].startswith("images/")
    att = e["attachments"][0]
    assert att["original_filename"] == "my sketch.png"
    assert att["mime_type"] == "image/png"
    assert att["sha256"] == hashlib.sha256(PNG).hexdigest()
    stored = get_settings().data_dir / e["source_file"]
    assert stored.read_bytes() == PNG
    # visual metadata exists but is UNKNOWN, and flagged as such
    assert e["ai_metadata"]["visual"]["visual_subjects"] is None
    assert any("UNKNOWN" in u for u in e["ai_metadata"]["uncertainties"])
    served = client.get(att["url"])
    assert served.status_code == 200 and served.content == PNG
    assert served.headers["content-security-policy"] == "sandbox"


def test_audio_upload_with_placeholder_transcription(client):
    r = client.post(
        "/api/uploads",
        files={"file": ("recording-1.webm", b"fake-opus-bytes", "audio/webm;codecs=opus")},
        data={"note": "Monster living in the subway"},
    )
    assert r.status_code == 201, r.text
    e = r.json()
    assert e["source_type"] == "voice"
    assert e["source_file"].startswith("audio/")
    assert e["original_text"] == "Monster living in the subway"
    assert e["transcript"] is None  # placeholder never invents words
    assert e["ai_metadata"]["transcription"]["status"] == "unavailable"
    assert "monsters" in e["ai_metadata"]["concepts"]


def test_generic_file_and_path_safety(client):
    r = client.post("/api/uploads", files={"file": ("../../etc/passwd.txt", b"hello", "text/plain")})
    assert r.status_code == 201
    e = r.json()
    assert e["source_type"] == "file"
    assert e["attachments"][0]["original_filename"] == "passwd.txt"
    path = (get_settings().data_dir / e["source_file"]).resolve()
    assert path.is_relative_to(get_settings().uploads_dir.resolve())
    assert "attachment" in client.get(e["attachments"][0]["url"]).headers["content-disposition"]


def test_empty_upload_rejected(client):
    assert client.post("/api/uploads", files={"file": ("x.png", b"", "image/png")}).status_code == 400


def test_upload_into_project_with_user_type(client):
    p = client.post("/api/projects", json={"name": "Bestiary"}).json()
    e = client.post("/api/uploads", files={"file": ("m.png", PNG, "image/png")},
                    data={"project_id": str(p["id"]), "entry_type": "artwork", "title": "Subway monster"}).json()
    assert e["entry_type"] == "artwork" and e["entry_type_source"] == "user"
    assert e["title"] == "Subway monster" and e["projects"][0]["id"] == p["id"]


def test_upload_size_limit(client, monkeypatch):
    from app import config
    s = config.get_settings()
    monkeypatch.setattr(config, "get_settings", lambda: type(s)(**{**s.__dict__, "max_upload_mb": 0}))
    from app.routes import uploads
    monkeypatch.setattr(uploads, "get_settings", config.get_settings)
    before = set(Path(s.images_dir).iterdir())
    r = client.post("/api/uploads", files={"file": ("big.png", PNG, "image/png")})
    assert r.status_code == 413
    assert set(Path(s.images_dir).iterdir()) == before  # partial file removed
