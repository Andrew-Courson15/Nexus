"""Picture formats: every upload keeps its original; formats the browser/model can't read get a preview."""
import hashlib
import io
import struct
import zipfile

import pytest
from PIL import Image

from app.config import get_settings
from app.services import images, organizer
from tests.fake_ollama import FakeOllama


def _pil(fmt: str, mode: str = "RGB", size=(40, 30), **kw) -> bytes:
    buf = io.BytesIO()
    Image.new(mode, size, (200, 40, 40) if mode == "RGB" else 128).save(buf, fmt, **kw)
    return buf.getvalue()


def _png() -> bytes:
    return _pil("PNG")


def _psd(w=40, h=30) -> bytes:
    """A minimal flattened Photoshop file (RGB, 8-bit, raw composite)."""
    head = b"8BPS" + struct.pack(">H6xHIIHH", 1, 3, h, w, 8, 3)
    body = struct.pack(">III", 0, 0, 0) + struct.pack(">H", 0)
    planes = bytes([200]) * (w * h) + bytes([40]) * (w * h) + bytes([40]) * (w * h)
    return head + body + planes


def _heic() -> bytes:
    import pillow_heif

    pillow_heif.register_heif_opener()
    return _pil("HEIF")


def _svg() -> bytes:
    return (b'<svg xmlns="http://www.w3.org/2000/svg" width="80" height="60">'
            b'<circle cx="40" cy="30" r="20" fill="#f2b84b"/></svg>')


def _pdf() -> bytes:
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page(width=200, height=150)
    page.draw_rect(pymupdf.Rect(20, 20, 180, 130), color=(1, 0, 0), fill=(1, 0.5, 0))
    data = doc.tobytes()
    doc.close()
    return data


def _zip(inner: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("Document.archive", b"layers...")
        zf.writestr(inner, _png())
    return buf.getvalue()


# (filename, bytes, the content type a browser typically sends)
FORMATS = [
    ("scan.tiff", lambda: _pil("TIFF"), "image/tiff"),
    ("scan16.tif", lambda: _pil("TIFF", mode="I;16"), "image/tiff"),
    ("old.bmp", lambda: _pil("BMP"), "image/bmp"),
    ("texture.tga", lambda: _pil("TGA"), "application/octet-stream"),
    ("web.webp", lambda: _pil("WEBP"), "image/webp"),
    ("anim.gif", lambda: _pil("GIF"), "image/gif"),
    ("icon.ico", lambda: _pil("ICO", size=(32, 32)), "image/x-icon"),
    ("print.jpg", lambda: _pil("JPEG", mode="CMYK"), "image/jpeg"),
    ("painting.psd", _psd, "application/octet-stream"),
    ("phone.heic", _heic, "image/heic"),
    ("logo.svg", _svg, "image/svg+xml"),
    ("vector.ai", _pdf, "application/postscript"),
    ("canvas.procreate", lambda: _zip("QuickLook/Thumbnail.png"), "application/octet-stream"),
    ("canvas.kra", lambda: _zip("mergedimage.png"), "application/octet-stream"),
    ("canvas.ora", lambda: _zip("mergedimage.png"), "application/octet-stream"),
]


@pytest.mark.parametrize("name,make,sent_type", FORMATS, ids=[f[0] for f in FORMATS])
def test_picture_formats_get_a_viewable_preview_and_keep_the_original(client, name, make, sent_type):
    data = make()
    r = client.post("/api/uploads", files={"file": (name, data, sent_type)})
    assert r.status_code == 201, r.text
    e = r.json()
    assert e["source_type"] == "image", e
    att = e["attachments"][0]
    assert "preview_error" not in e["ai_metadata"], e["ai_metadata"]
    # the original is stored and served byte-for-byte
    assert att["sha256"] == hashlib.sha256(data).hexdigest()
    assert client.get(att["url"]).content == data
    # the preview is a real JPEG/PNG
    p = client.get(att["preview_url"])
    assert p.status_code == 200, p.text
    assert p.headers["content-type"] in ("image/jpeg", "image/png")
    Image.open(io.BytesIO(p.content)).verify()


def test_jpeg_and_png_are_shown_as_they_are(client):
    data = _png()
    e = client.post("/api/uploads", files={"file": ("s.png", data, "image/png")}).json()
    assert client.get(e["attachments"][0]["preview_url"]).content == data


def test_huge_images_are_scaled_down_for_the_preview(client, monkeypatch):
    monkeypatch.setattr(images, "MAX_DIRECT_BYTES", 10)
    e = client.post("/api/uploads", files={"file": ("big.png", _pil("PNG", size=(5000, 1000)), "image/png")}).json()
    im = Image.open(io.BytesIO(client.get(e["attachments"][0]["preview_url"]).content))
    assert max(im.size) == images.PREVIEW_MAX_SIDE


def test_pdf_stays_a_document_but_gets_a_first_page_preview(client):
    e = client.post("/api/uploads", files={"file": ("notes.pdf", _pdf(), "application/pdf")}).json()
    assert e["source_type"] == "file"
    p = client.get(e["attachments"][0]["preview_url"])
    assert p.status_code == 200 and p.headers["content-type"] in ("image/jpeg", "image/png")


def test_unreadable_picture_is_kept_and_reported(client):
    e = client.post("/api/uploads", files={"file": ("broken.psd", b"not a psd", "application/octet-stream")}).json()
    assert e["source_type"] == "image"
    assert "broken.psd" in e["ai_metadata"]["preview_error"]
    assert client.get(e["attachments"][0]["url"]).content == b"not a psd"
    assert client.get(e["attachments"][0]["preview_url"]).status_code == 415


def test_missing_preview_is_rebuilt_on_request(client):
    e = client.post("/api/uploads", files={"file": ("scan.tiff", _pil("TIFF"), "image/tiff")}).json()
    for f in (get_settings().data_dir / "previews").glob("*"):
        f.unlink()
    assert client.get(e["attachments"][0]["preview_url"]).status_code == 200


def test_vision_model_receives_the_preview_not_the_original(client, monkeypatch):
    fake = FakeOllama(models=["test-model"], capabilities=["completion", "vision"])
    monkeypatch.setattr(organizer, "get_ai_provider", lambda: fake.provider())
    r = client.post("/api/uploads", files={"file": ("painting.psd", _psd(), "application/octet-stream")})
    assert r.status_code == 201
    import base64

    sent = base64.b64decode(fake.chats[0]["messages"][1]["images"][0])
    assert sent[:3] == b"\xff\xd8\xff"  # a JPEG rendering of the PSD
    assert "(with image)" in r.json()["ai_metadata"]["method"]
