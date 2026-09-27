"""Preferences: everyday settings changed in the app instead of by editing `.env`.

Saved in <data>/preferences.json. A saved preference wins over `.env`; clearing it (null)
goes back to `.env` / the default. Changes apply straight away: the settings cache and the
cached AI / embedding providers are reset, so the next request uses the new choice.
"""
from __future__ import annotations

import json
import os
import re
import uuid

from app.config import PREFERENCE_TYPES, _resolve, get_settings, preferences_file, read_preferences

WHISPER_MODELS = ["tiny", "base", "small", "medium", "large-v3", "turbo"]
_MODEL_RE = re.compile(r"^[A-Za-z0-9._:/-]{1,120}$")

LABELS = {
    "ollama_model": "AI model", "ollama_vision_model": "Model for pictures", "embedding_model": "Search-by-meaning model",
    "whisper_model": "Speech-to-text model", "whisper_language": "Voice note language",
    "art_auto_analyze": "Analyze new pictures automatically", "semantic_min_similarity": "Search-by-meaning strictness",
    "backup_dir": "Backup folder", "backup_every_days": "Automatic backup every (days)",
    "backup_keep": "Backups to keep", "owner_name": "Your name",
}


class PreferenceError(ValueError):
    pass


def _validate(key: str, value):
    """Return the value to store, or raise PreferenceError with a message a person can act on."""
    label = LABELS[key]
    if key in ("ollama_model", "embedding_model"):
        value = str(value).strip()
        if not _MODEL_RE.match(value):
            raise PreferenceError(f"{label}: use a model name such as gemma3:12b.")
        return value
    if key == "ollama_vision_model":
        value = str(value).strip()
        if value and not _MODEL_RE.match(value):
            raise PreferenceError(f"{label}: use a model name such as gemma3:12b, or leave it empty.")
        return value
    if key == "whisper_model":
        if value not in WHISPER_MODELS:
            raise PreferenceError(f"{label}: choose one of {', '.join(WHISPER_MODELS)}.")
        return value
    if key == "whisper_language":
        value = str(value).strip().lower()
        if value and not re.fullmatch(r"[a-z]{2,3}", value):
            raise PreferenceError(f"{label}: a two-letter code such as en or es, or empty to detect it.")
        return value
    if key == "art_auto_analyze":
        if not isinstance(value, bool):
            raise PreferenceError(f"{label}: on or off.")
        return value
    if key == "semantic_min_similarity":
        try:
            value = round(float(value), 3)
        except (TypeError, ValueError) as exc:
            raise PreferenceError(f"{label}: a number between 0.4 and 0.85.") from exc
        if not 0.4 <= value <= 0.85:
            raise PreferenceError(f"{label}: a number between 0.4 and 0.85.")
        return value
    if key == "backup_dir":
        value = str(value).strip()
        if not value:
            return ""
        folder = _resolve(value)
        try:
            folder.mkdir(parents=True, exist_ok=True)
            probe = folder / f".nexus-write-test-{uuid.uuid4().hex}"
            probe.write_bytes(b"ok")
            probe.unlink()
        except OSError as exc:
            raise PreferenceError(f"{label}: NEXUS can't write to {folder} ({exc.strerror or exc}).") from exc
        return str(folder)
    if key == "backup_every_days":
        try:
            value = float(value)
        except (TypeError, ValueError) as exc:
            raise PreferenceError(f"{label}: a number of days (0 turns automatic backups off).") from exc
        if not 0 <= value <= 365:
            raise PreferenceError(f"{label}: between 0 and 365 days.")
        return value
    if key == "backup_keep":
        try:
            value = int(value)
        except (TypeError, ValueError) as exc:
            raise PreferenceError(f"{label}: a whole number.") from exc
        if not 1 <= value <= 100:
            raise PreferenceError(f"{label}: between 1 and 100.")
        return value
    if key == "owner_name":
        return str(value).strip()[:80]
    raise PreferenceError(f"Unknown preference: {key}")


def _apply() -> None:
    """Make the new values take effect now."""
    from app.services import ai, embeddings

    get_settings.cache_clear()
    ai._instances.clear()
    embeddings._instances.clear()


def update(changes: dict) -> dict:
    """Validate and save; a value of None removes that preference (back to .env / default)."""
    unknown = [k for k in changes if k not in PREFERENCE_TYPES]
    if unknown:
        raise PreferenceError(f"Unknown preference: {', '.join(unknown)}")
    data_dir = get_settings().data_dir
    saved = read_preferences(data_dir)
    for key, value in changes.items():
        if value is None:
            saved.pop(key, None)
        else:
            saved[key] = _validate(key, value)
    path = preferences_file(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(saved, indent=2), encoding="utf-8")
    os.replace(tmp, path)  # never a half-written file
    _apply()
    return describe()


def describe() -> dict:
    """Current values, where each comes from, and the choices to offer."""
    s = get_settings()
    saved = read_preferences(s.data_dir)
    values = {k: getattr(s, k) for k in PREFERENCE_TYPES}
    values["backup_dir"] = str(s.backups_dir)
    installed: list[str] = []
    try:
        from app.services.ollama import OllamaClient

        client = OllamaClient(s.ollama_url, 3)
        try:
            installed = sorted(client.list_models(timeout=2))
        finally:
            client._http.close()
    except Exception:
        pass
    return {
        "values": values,
        "from_app": sorted(saved),
        "labels": LABELS,
        "choices": {"installed_models": installed, "whisper_models": WHISPER_MODELS},
        "file": str(preferences_file(s.data_dir)),
    }

