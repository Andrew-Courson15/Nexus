"""Preferences changed in the app: saved, validated, applied straight away."""
import pytest

from app.config import get_settings, preferences_file
from app.services import ai


@pytest.fixture(autouse=True)
def clean_preferences():
    preferences_file(get_settings().data_dir).unlink(missing_ok=True)
    get_settings.cache_clear()
    yield
    preferences_file(get_settings().data_dir).unlink(missing_ok=True)
    get_settings.cache_clear()
    ai._instances.clear()


def test_saved_preferences_apply_at_once_and_can_be_reset(client):
    d = client.get("/api/preferences").json()
    assert d["values"]["ollama_model"] == "gemma3:12b" and d["from_app"] == []
    r = client.put("/api/preferences", json={"ollama_model": "gemma3:4b", "art_auto_analyze": False,
                                             "owner_name": "  Andrew ", "backup_keep": 7})
    assert r.status_code == 200, r.text
    assert get_settings().ollama_model == "gemma3:4b" and get_settings().art_auto_analyze is False
    assert get_settings().owner_name == "Andrew" and get_settings().backup_keep == 7
    assert sorted(r.json()["from_app"]) == ["art_auto_analyze", "backup_keep", "ollama_model", "owner_name"]
    assert client.get("/api/settings").json()["owner_name"] == "Andrew"
    client.put("/api/preferences", json={"ollama_model": None})
    assert get_settings().ollama_model == "gemma3:12b"  # back to .env / default


def test_new_model_choice_reaches_the_ai_provider(client, monkeypatch):
    monkeypatch.setenv("NEXUS_AI_PROVIDER", "ollama")
    get_settings.cache_clear()
    ai._instances.clear()
    assert ai.get_ai_provider().model == "gemma3:12b"
    client.put("/api/preferences", json={"ollama_model": "llama3.2:3b"})
    assert ai.get_ai_provider().model == "llama3.2:3b"


@pytest.mark.parametrize("bad", [
    {"ollama_model": "rm -rf /"}, {"whisper_model": "gigantic"}, {"semantic_min_similarity": 2},
    {"backup_keep": 0}, {"backup_every_days": -1}, {"whisper_language": "english"}, {"art_auto_analyze": "yes"},
    {"data_dir": "/tmp"},
])
def test_bad_values_are_refused_with_a_reason(client, bad):
    r = client.put("/api/preferences", json=bad)
    assert r.status_code == 400 and r.json()["detail"]
    assert client.get("/api/preferences").json()["from_app"] == []


def test_backup_folder_must_be_writable(client, tmp_path):
    ok = tmp_path / "Backups"
    assert client.put("/api/preferences", json={"backup_dir": str(ok)}).status_code == 200
    assert get_settings().backups_dir == ok and ok.is_dir()
    blocker = tmp_path / "file"
    blocker.write_text("x")
    r = client.put("/api/preferences", json={"backup_dir": str(blocker / "sub")})
    assert r.status_code == 400 and "can't write" in r.json()["detail"]


def test_a_damaged_preferences_file_is_ignored(client):
    preferences_file(get_settings().data_dir).write_text("{not json")
    get_settings.cache_clear()
    assert get_settings().ollama_model == "gemma3:12b"
    assert client.get("/api/preferences").status_code == 200


def test_shares_are_signed_with_your_name(client):
    client.put("/api/preferences", json={"owner_name": "Andrew"})
    e = client.post("/api/entries", json={"original_text": "x"}).json()
    import io
    import json
    import zipfile
    data = client.get("/api/share/export", params={"entry_ids": str(e["id"])}).content
    assert json.loads(zipfile.ZipFile(io.BytesIO(data)).read("manifest.json"))["by"] == "Andrew"


def test_phone_link_with_qr_code(client, monkeypatch):
    assert client.get("/api/phone-link").json()["url"] is None  # phone setup not done yet
    monkeypatch.setenv("NEXUS_ALLOWED_HOSTS", "testserver,localhost,127.0.0.1,gaming-pc.tail1234.ts.net")
    get_settings.cache_clear()
    d = client.get("/api/phone-link").json()
    assert d["url"] == "https://gaming-pc.tail1234.ts.net"
    assert d["qr_svg"].startswith("<svg") and "<script" not in d["qr_svg"]


def test_downloading_a_model_reports_progress(client, monkeypatch):
    import time

    from app.services import ollama, preferences

    def fake_pull(self, model, on_progress):
        on_progress("pulling manifest", None, None)
        on_progress("downloading", 50, 100)
        on_progress("downloading", 100, 100)
    monkeypatch.setattr(ollama.OllamaClient, "pull", fake_pull)
    r = client.post("/api/models/pull", json={"name": "gemma3:4b"})
    assert r.status_code == 202
    for _ in range(50):
        state = {p["name"]: p for p in client.get("/api/models/pulls").json()}["gemma3:4b"]
        if state["state"] != "downloading":
            break
        time.sleep(0.02)
    assert state["state"] == "done" and state["completed"] == state["total"] == 100
    assert client.post("/api/models/pull", json={"name": "bad name!"}).status_code == 400
    preferences._pulls.clear()


def test_failed_downloads_say_why(client, monkeypatch):
    import time

    from app.services import ollama, preferences

    def broken(self, model, on_progress):
        raise ollama.OllamaError("pull model manifest: file does not exist")
    monkeypatch.setattr(ollama.OllamaClient, "pull", broken)
    client.post("/api/models/pull", json={"name": "nosuchmodel"})
    for _ in range(50):
        state = client.get("/api/models/pulls").json()[0]
        if state["state"] != "downloading":
            break
        time.sleep(0.02)
    assert state["state"] == "failed" and "does not exist" in state["error"]
    preferences._pulls.clear()


def test_problem_report_has_health_and_log_but_not_entry_text(client):
    from app.config import get_settings

    client.post("/api/entries", json={"original_text": "my secret comic plot", "analyze": False})
    (get_settings().data_dir / "nexus.log").write_text("line one\nERROR something broke\n")
    text = client.get("/api/problem-report").json()["text"]
    assert text.startswith("NEXUS ") and "Entries: 1" in text and "ERROR something broke" in text
    assert "secret comic plot" not in text
