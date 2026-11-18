"""The archive must stay writable while the AI is thinking."""
import sqlite3
import uuid

from app.config import get_settings
from app.services import organizer
from app.services.ai import LocalHeuristicProvider


def _db_path() -> str:
    return get_settings().database_url.replace("sqlite:///", "")


def _write_like_a_new_capture(sql: str = "INSERT INTO tags (name) VALUES (?)", params=None) -> None:
    conn = sqlite3.connect(_db_path(), timeout=0.5)  # fails fast if something holds the write lock
    try:
        conn.execute(sql, params or (f"probe-{uuid.uuid4().hex}",))
        conn.commit()
    finally:
        conn.close()


class ProbeProvider(LocalHeuristicProvider):
    """Writes to the database from 'another user' in the middle of each slow AI call."""

    def __init__(self, on_analyze=None):
        self.calls = []
        self.on_analyze = on_analyze

    def analyze_entry(self, entry):
        _write_like_a_new_capture()
        if self.on_analyze:
            self.on_analyze(entry)
        self.calls.append("analyze")
        return super().analyze_entry(entry)

    def find_relationships(self, entry, existing_entries, **kw):
        _write_like_a_new_capture()
        self.calls.append("relationships")
        return super().find_relationships(entry, existing_entries, **kw)


def test_no_write_lock_is_held_during_ai_calls(client, monkeypatch):
    probe = ProbeProvider()
    monkeypatch.setattr(organizer, "get_ai_provider", lambda: probe)
    client.post("/api/entries", json={"original_text": "A villain who collects memories."})
    r = client.post("/api/entries", json={"original_text": "A hero who loses memories in other dimensions."})
    assert r.status_code == 201
    assert r.json()["processing_status"] == "done"
    assert probe.calls.count("relationships") >= 1  # both slow calls happened, each with a concurrent write


def test_edits_made_while_the_ai_thinks_are_kept(client, monkeypatch):
    def user_renames(entry):
        _write_like_a_new_capture(
            "UPDATE creative_entries SET title = ?, title_source = 'user' WHERE id = ?", ("My Own Title", entry.id))

    monkeypatch.setattr(organizer, "get_ai_provider", lambda: ProbeProvider(on_analyze=user_renames))
    e = client.post("/api/entries", json={"original_text": "A comic about a sword that eats memories."}).json()
    assert e["title"] == "My Own Title" and e["title_source"] == "user"
    assert e["ai_summary"]  # the AI's work was still saved
