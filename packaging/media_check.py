"""Smoke check for pictures in a built NEXUS: an iPhone photo (HEIC) and a PDF both get previews.

    python packaging/media_check.py http://127.0.0.1:8765

Needs pillow-heif and pymupdf here to make the test files (they're in requirements.txt).
"""
import io
import json
import sys
import urllib.request
import uuid

import pillow_heif
import pymupdf
from PIL import Image

base = sys.argv[1].rstrip("/")


def heic() -> bytes:
    pillow_heif.register_heif_opener()
    out = io.BytesIO()
    Image.new("RGB", (640, 480), (200, 120, 60)).save(out, format="HEIF")
    return out.getvalue()


def pdf() -> bytes:
    doc = pymupdf.open()
    for n in (1, 2):
        doc.new_page().insert_text((72, 72), f"Storyboard page {n}")
    return doc.tobytes()


def upload(name: str, data: bytes, mime: str) -> dict:
    boundary = uuid.uuid4().hex
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"analyze\"\r\n\r\nfalse\r\n"
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{name}\"\r\n"
            f"Content-Type: {mime}\r\n\r\n").encode() + data + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(base + "/api/uploads", data=body, method="POST",
                                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    return json.load(urllib.request.urlopen(req, timeout=60))


for name, data, mime in [("photo.heic", heic(), "image/heic"), ("storyboard.pdf", pdf(), "application/pdf")]:
    entry = upload(name, data, mime)
    att = entry["attachments"][0]["id"]
    with urllib.request.urlopen(f"{base}/api/uploads/{att}/preview", timeout=60) as r:
        kind, size = r.headers.get("Content-Type"), len(r.read())
    if not kind.startswith("image/") or size < 200:
        sys.exit(f"{name}: no preview ({kind}, {size} bytes)")
    print(f"{name}: preview {kind}, {size} bytes")
print("iPhone photos and PDFs work in this build.")
