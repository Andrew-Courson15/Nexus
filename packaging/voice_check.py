"""Smoke check for voice notes in a built NEXUS: upload a short recording, wait for the transcript.

    python packaging/voice_check.py http://127.0.0.1:8765

Passes when the speech-to-text engine ran (a transcript, or "empty" for a recording without
words); fails when it's unavailable or failed, which means something is missing from the app.
"""
import io
import json
import math
import struct
import sys
import time
import urllib.request
import uuid
import wave

base = sys.argv[1].rstrip("/")

buf = io.BytesIO()
with wave.open(buf, "wb") as w:  # 2 seconds of a soft, wavering tone
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(16000)
    w.writeframes(b"".join(struct.pack("<h", int(3000 * math.sin(2 * math.pi * (220 + 40 * math.sin(i / 800)) * i / 16000)))
                           for i in range(32000)))
boundary = uuid.uuid4().hex
body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"analyze\"\r\n\r\nfalse\r\n"
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"voice-check.wav\"\r\n"
        "Content-Type: audio/wav\r\n\r\n").encode() + buf.getvalue() + f"\r\n--{boundary}--\r\n".encode()
req = urllib.request.Request(base + "/api/uploads", data=body, method="POST",
                             headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
entry = json.load(urllib.request.urlopen(req, timeout=60))
print("Uploaded a recording: entry", entry["id"], entry.get("source_type"))

deadline = time.time() + 900  # the speech model downloads on first use
while time.time() < deadline:
    e = json.load(urllib.request.urlopen(f"{base}/api/entries/{entry['id']}", timeout=30))
    t = (e.get("ai_metadata") or {}).get("transcription")
    if t:
        print("Transcription:", t)
        if t.get("status") in ("completed", "empty"):
            print("Voice notes work in this build.")
            sys.exit(0)
        sys.exit(f"Voice notes don't work in this build: {t}")
    time.sleep(3)
sys.exit("No transcription within 15 minutes")
