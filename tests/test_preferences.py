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
    import io, json, zipfile
    data = client.get("/api/share/export", params={"entry_ids": str(e["id"])}).content
    assert json.loads(zipfile.ZipFile(io.BytesIO(data)).read("manifest.json"))["by"] == "Andrew"
