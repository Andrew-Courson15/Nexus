"""A small made-up archive for App Store screenshots (used by ios/ci_app_store_screenshots.sh).

    python ios/demo_archive.py http://127.0.0.1:8000

Everything here is invented for the screenshots: no one's real ideas.
"""
from __future__ import annotations

import io
import math
import random
import sys

import httpx
from PIL import Image, ImageDraw, ImageFilter

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
http = httpx.Client(base_url=BASE, timeout=60, trust_env=False)

IDEAS = [
    ("The Night Keeper", "character", ["lighthouse", "night"],
     "A lighthouse keeper who can't sleep, so she writes letters to the ships that never arrive. "
     "Every storm, one of them writes back."),
    ("Tide Spirits", "worldbuilding", ["ocean", "spirits"],
     "The tide spirits only come ashore on the full moon. They trade shells for forgotten names."),
    ("Opening scene: the flooded library", "scene", ["library", "water"],
     "Morning light through water. Books float like jellyfish. Mara swims between the shelves looking "
     "for the one book that stayed dry."),
    ("\"You kept the light on for me.\"", "dialogue", ["lighthouse"],
     "MARA: You kept the light on for me.\nKEEPER: I kept it on for everyone. You're just the first to come back."),
    ("Short film: 90 seconds of rain", "short_film", ["rain", "city"],
     "One shot. A rooftop in the rain; a kid teaches an old man to dance with an umbrella. No dialogue."),
    ("Comic idea: the cartographer's cat", "comic", ["cat", "maps"],
     "A cat who walks across wet ink and accidentally draws a map to a place that doesn't exist yet."),
    ("Brain dump after the museum", "brain_dump", ["colour", "museum"],
     "Deep blues next to rust orange. Tiny figures in huge rooms. What if every panel had one light source?"),
]
PICTURES = [
    ("Keeper, first sketch", (18, 38, 70), (230, 170, 80)),
    ("Tide spirit colours", (10, 70, 90), (120, 220, 210)),
    ("Library underwater", (20, 50, 60), (190, 230, 240)),
    ("Rooftop dance, thumbnail", (40, 40, 60), (240, 120, 90)),
    ("Map cat", (60, 45, 30), (240, 210, 150)),
    ("Storm study", (25, 30, 45), (160, 170, 200)),
]


def painting(dark, light, seed) -> bytes:
    """An abstract, painterly picture: soft gradient, a glowing circle and brush-like strokes."""
    rnd = random.Random(seed)
    w, h = 900, 1100
    img = Image.new("RGB", (w, h))
    px = img.load()
    for y in range(h):
        t = y / h
        for x in range(0, w):
            k = t * 0.8 + 0.2 * math.sin(x / 140 + seed)
            px[x, y] = tuple(int(d + (l - d) * max(0, min(1, k))) for d, l in zip(dark, light))
    d = ImageDraw.Draw(img, "RGBA")
    cx, cy, r = rnd.randint(200, 700), rnd.randint(200, 500), rnd.randint(90, 170)
    for i in range(12, 0, -1):
        d.ellipse([cx - r * i / 6, cy - r * i / 6, cx + r * i / 6, cy + r * i / 6], fill=(*light, 10))
    d.ellipse([cx - r / 2, cy - r / 2, cx + r / 2, cy + r / 2], fill=(255, 245, 225, 230))
    for _ in range(40):
        x, y = rnd.randint(0, w), rnd.randint(h // 2, h)
        d.line([x, y, x + rnd.randint(-160, 160), y + rnd.randint(-40, 40)],
               fill=(*[max(0, c - 20) for c in dark], 160), width=rnd.randint(6, 22))
    img = img.filter(ImageFilter.GaussianBlur(1.2))
    out = io.BytesIO()
    img.save(out, "JPEG", quality=88)
    return out.getvalue()


def main() -> None:
    project = http.post("/api/projects", json={
        "name": "The Night Keeper", "project_type": "comic",
        "description": "A graphic novel about a lighthouse keeper, the tide spirits and a flooded library."}).json()
    ids = []
    for n, (title, kind, tags, text) in enumerate(IDEAS):
        r = http.post("/api/entries", json={"original_text": text, "title": title, "entry_type": kind, "tags": tags,
                                            "status": "developing", "analyze": False,
                                            "project_ids": [project["id"]] if n < 4 else []})
        r.raise_for_status()
        ids.append(r.json()["id"])
    for n, (title, dark, light) in enumerate(PICTURES):
        r = http.post("/api/uploads", files={"file": (f"art-{n}.jpg", painting(dark, light, n), "image/jpeg")},
                      data={"title": title, "entry_type": "artwork", "analyze": "false", "project_id": str(project["id"])})
        r.raise_for_status()
        ids.append(r.json()["id"])
    print(f"Demo archive: {len(ids)} entries, project {project['id']}")


if __name__ == "__main__":
    main()
