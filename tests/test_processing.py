import dataclasses
from types import SimpleNamespace

from sqlalchemy import create_engine, inspect, text

from app.config import get_settings
from app.database import Base, SessionLocal, add_missing_columns
from app.models import CreativeEntry
from app.services import organizer, processing, transcription
from app.services.transcription import LocalWhisperProvider, TranscriptionResult

WEBM = b"\x1aE\xdf\xa3fake-webm"


class FakeTranscriber(transcription.TranscriptionProvider):
    name = "fake-whisper"

    def __init__(self, text="A villain who steals memories and hides them in a lighthouse."):
        self.text = text
        self.calls = 0

    def transcribe(self, audio_path):
        self.calls += 1
        assert audio_path.is_file()
        return TranscriptionResult(self.text, self.name, "completed" if self.text else "empty", "en")


def upload_voice(client, **data):
    r = client.post("/api/uploads", files={"file": ("recording-1.webm", WEBM, "audio/webm")}, data=data)
    assert r.status_code == 201, r.text
    return r.json()


def test_voice_is_transcribed_then_organized(client, monkeypatch):
    fake = FakeTranscriber()
    monkeypatch.setattr(transcription, "get_transcription_provider", lambda: fake)
    e = upload_voice(client)
    assert e["processing_status"] == "done"
    assert e["transcript"] == fake.text
    assert e["original_text"] is None                  # a transcript is not "user said"
    assert e["ai_metadata"]["transcription"]["status"] == "completed"
    assert "memory" in e["ai_metadata"]["concepts"]    # analysis used the transcript
    revs = [r["change_description"] for r in client.get(f"/api/entries/{e['id']}/revisions").json()]
    assert "Transcribed (fake-whisper)" in revs
    assert client.post("/api/search", json={"query": "lighthouse"}).json()["results"][0]["entry"]["id"] == e["id"]


def test_empty_recording(client, monkeypatch):
    monkeypatch.setattr(transcription, "get_transcription_provider", lambda: FakeTranscriber(text=None))
    e = upload_voice(client)
    assert e["transcript"] is None
    assert e["ai_metadata"]["transcription"]["status"] == "empty"
    assert e["processing_status"] == "done"


def test_transcribe_endpoint_rules(client, monkeypatch):
    fake = FakeTranscriber()
    monkeypatch.setattr(transcription, "get_transcription_provider", lambda: fake)
    text_entry = client.post("/api/entries", json={"original_text": "hello"}).json()
    assert client.post(f"/api/entries/{text_entry['id']}/transcribe").status_code == 400

    e = upload_voice(client)
    client.put(f"/api/entries/{e['id']}", json={"transcript": "My own corrected words."})
    assert client.post(f"/api/entries/{e['id']}/transcribe").status_code == 409
    r = client.post(f"/api/entries/{e['id']}/transcribe?force=true")
    assert r.status_code == 200 and r.json()["transcript"] == fake.text
    # the hand-corrected version is still in the history
    snaps = [r["content_snapshot"]["transcript"] for r in client.get(f"/api/entries/{e['id']}/revisions").json()]
    assert "My own corrected words." in snaps


def test_pipeline_failure_keeps_original(client, monkeypatch):
    class Boom:
        name = "boom"

        def analyze_entry(self, entry):
            raise KeyError("unexpected")

    monkeypatch.setattr(organizer, "get_ai_provider", lambda: Boom())
    r = client.post("/api/entries", json={"original_text": "Precious idea."})
    assert r.status_code == 201
    e = r.json()
    assert e["original_text"] == "Precious idea."
    assert e["processing_status"] == "failed"
    assert "unexpected" in e["ai_metadata"]["analysis_error"]


def test_background_mode(client, monkeypatch):
    s = get_settings()
    monkeypatch.setattr(processing, "get_settings", lambda: dataclasses.replace(s, processing_mode="background"))
    q = processing.processing_queue
    q.start()
    try:
        e = client.post("/api/entries", json={"original_text": "A dragon that hoards forgotten memories."}).json()
        assert e["processing_status"] in ("queued", "analyzing", "done")
        assert e["original_text"] == "A dragon that hoards forgotten memories."
        assert q.wait_idle(10)
        done = client.get(f"/api/entries/{e['id']}").json()
        assert done["processing_status"] == "done" and done["ai_summary"]
        r = client.post(f"/api/entries/{e['id']}/analyze")
        assert r.status_code in (200, 202)
        assert q.wait_idle(10)
    finally:
        q.stop()


def test_recover_interrupted_work(client):
    e = client.post("/api/entries", json={"original_text": "Half-finished thought", "analyze": False}).json()
    assert e["processing_status"] is None and e["ai_summary"] is None
    with SessionLocal() as db:
        db.get(CreativeEntry, e["id"]).processing_status = "analyzing"  # as if the app crashed mid-way
        db.commit()
    assert processing.processing_queue.recover() == 1
    after = client.get(f"/api/entries/{e['id']}").json()
    assert after["processing_status"] == "done" and after["ai_summary"]


def test_local_whisper_provider_with_stub_model(tmp_path):
    audio = tmp_path / "a.webm"
    audio.write_bytes(WEBM)

    class StubModel:
        def __init__(self, segments):
            self.segments = segments

        def transcribe(self, path, **kw):
            assert kw["vad_filter"] is True
            info = SimpleNamespace(language="en", language_probability=0.97, duration=3.2)
            return (SimpleNamespace(text=t) for t in self.segments), info

    p = LocalWhisperProvider(model_size="stub-1")
    LocalWhisperProvider._models[("stub-1", p.device, p.compute_type)] = StubModel([" Hello", " world. "])
    r = p.transcribe(audio)
    assert (r.text, r.status, r.language) == ("Hello world.", "completed", "en")
    assert "97%" in r.message

    p2 = LocalWhisperProvider(model_size="stub-2")
    LocalWhisperProvider._models[("stub-2", p2.device, p2.compute_type)] = StubModel([])
    assert p2.transcribe(audio).status == "empty"


def test_whisper_falls_back_to_cpu_when_gpu_libraries_missing(tmp_path, monkeypatch):
    audio = tmp_path / "a.webm"
    audio.write_bytes(WEBM)

    class BrokenGPU:
        def transcribe(self, path, **kw):
            raise RuntimeError("Library cublas64_12.dll is not found or cannot be loaded")

    class CPUModel:
        def transcribe(self, path, **kw):
            return iter([SimpleNamespace(text="hi there")]), SimpleNamespace(language="en", duration=1.0)

    monkeypatch.setattr(LocalWhisperProvider, "_cpu_fallback", False)
    p = LocalWhisperProvider(model_size="stub-gpu", device="auto", compute_type="auto")
    LocalWhisperProvider._models[("stub-gpu", "auto", "auto")] = BrokenGPU()
    LocalWhisperProvider._models[("stub-gpu", "cpu", "int8")] = CPUModel()
    r = p.transcribe(audio)
    assert r.text == "hi there" and "CPU" in r.message
    assert LocalWhisperProvider._cpu_fallback is True


def test_whisper_reports_missing_install(monkeypatch, tmp_path):
    monkeypatch.setattr(LocalWhisperProvider, "installed", staticmethod(lambda: False))
    p = LocalWhisperProvider()
    assert "requirements-local.txt" in p.health()["detail"]
    audio = tmp_path / "a.webm"
    audio.write_bytes(WEBM)
    assert p.transcribe(audio).status == "unavailable"


def test_add_missing_columns_migrates_old_database(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE creative_entries DROP COLUMN processing_status"))
    with engine.begin() as conn:
        assert add_missing_columns(conn, Base.metadata) == ["creative_entries.processing_status"]
    cols = {c["name"] for c in inspect(engine).get_columns("creative_entries")}
    assert "processing_status" in cols
    engine.dispose()
