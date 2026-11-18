"""Application configuration, read from environment variables (and `.env`).

Some everyday choices (which AI model, backups, your name ...) can also be changed in the app
(Settings > Preferences). Those are kept in <data>/preferences.json and win over `.env`.
API keys are only ever read from the environment; they are never persisted.
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

#: True when NEXUS runs as a packaged app (one download, no Python needed), not from the source folder.
FROZEN = bool(getattr(sys, "frozen", False))


def _user_dir() -> Path:
    """Where a packaged NEXUS keeps your data and .env: the usual per-user app folder."""
    if sys.platform == "win32":
        root = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    elif sys.platform == "darwin":
        root = Path.home() / "Library" / "Application Support"
    else:
        root = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return root / "NEXUS"


# BASE_DIR: NEXUS's own files (app, static, scripts). USER_DIR: your data folder and .env live here.
# From the source folder both are that folder, exactly as before.
BASE_DIR = Path(getattr(sys, "_MEIPASS", "")).resolve() if FROZEN else Path(__file__).resolve().parent.parent
USER_DIR = Path(os.environ.get("NEXUS_HOME") or _user_dir()).expanduser() if FROZEN else BASE_DIR
load_dotenv(USER_DIR / ".env")


def _resolve(path: str) -> Path:
    p = Path(path).expanduser()
    return p if p.is_absolute() else (USER_DIR / p).resolve()


@dataclass(frozen=True)
class Settings:
    host: str
    port: int
    allowed_hosts: list[str]
    data_dir: Path
    database_url: str
    max_upload_mb: int
    ai_provider: str
    transcription_provider: str
    relationship_threshold: float
    # "background" (default): AI work runs after the capture is saved.
    # "sync": AI work runs inside the request (used by tests).
    processing_mode: str = "background"
    # Local model via Ollama
    ollama_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "gemma3:12b"
    ollama_vision_model: str = ""  # empty = use ollama_model for images too
    ollama_timeout: float = 300.0
    ollama_keep_alive: str = "10m"
    # Local Whisper (faster-whisper)
    whisper_model: str = "small"
    whisper_device: str = "auto"
    whisper_compute_type: str = "auto"
    whisper_language: str = ""  # empty = auto-detect
    # Search by meaning (embeddings via Ollama)
    embedding_provider: str = "ollama"  # "ollama" | "none"
    embedding_model: str = "nomic-embed-text"
    # nomic-embed-text scores unrelated text around 0.45-0.55, so a meaning match must clear
    # an absolute floor AND stand out from the rest of the archive (see embeddings.confident_matches).
    semantic_min_similarity: float = 0.6
    semantic_margin: float = 0.08   # how far above the archive's typical (median) score a match must be
    semantic_window: float = 0.12   # keep meaning-matches within this distance of the best one
    # Visual Art Intelligence: analyse new images automatically (needs a vision model)
    art_auto_analyze: bool = True
    # Backups: a zip of the database and every original file (see services/backup.py)
    backup_dir: Path | None = None   # default: <data_dir>/backups; better: another drive or a cloud folder
    backup_every_days: float = 7.0   # automatic backup interval; 0 turns automatic backups off
    backup_keep: int = 4             # how many backups to keep (older ones are removed)
    owner_name: str = ""             # shown to people you share with (.nexus files)

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def audio_dir(self) -> Path:
        return self.data_dir / "audio"

    @property
    def images_dir(self) -> Path:
        return self.data_dir / "images"

    @property
    def exports_dir(self) -> Path:
        return self.data_dir / "exports"

    @property
    def backups_dir(self) -> Path:
        return self.backup_dir or self.data_dir / "backups"

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.uploads_dir, self.audio_dir, self.images_dir, self.exports_dir):
            d.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    data_dir = _resolve(os.getenv("NEXUS_DATA_DIR", "data"))
    database_url = os.getenv("NEXUS_DATABASE_URL") or f"sqlite:///{data_dir / 'nexus.db'}"
    allowed = os.getenv("NEXUS_ALLOWED_HOSTS", "localhost,127.0.0.1")
    base = Settings(
        host=os.getenv("NEXUS_HOST", "127.0.0.1"),
        port=int(os.getenv("NEXUS_PORT", "8000")),
        allowed_hosts=[h.strip() for h in allowed.split(",") if h.strip()],
        data_dir=data_dir,
        database_url=database_url,
        max_upload_mb=int(os.getenv("NEXUS_MAX_UPLOAD_MB", "100")),
        ai_provider=os.getenv("NEXUS_AI_PROVIDER", "ollama").strip().lower(),
        transcription_provider=os.getenv("NEXUS_TRANSCRIPTION_PROVIDER", "whisper_local").strip().lower(),
        relationship_threshold=float(os.getenv("NEXUS_RELATIONSHIP_THRESHOLD", "0.25")),
        processing_mode=os.getenv("NEXUS_PROCESSING_MODE", "background").strip().lower(),
        ollama_url=os.getenv("NEXUS_OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/"),
        ollama_model=os.getenv("NEXUS_OLLAMA_MODEL", "gemma3:12b").strip(),
        ollama_vision_model=os.getenv("NEXUS_OLLAMA_VISION_MODEL", "").strip(),
        ollama_timeout=float(os.getenv("NEXUS_OLLAMA_TIMEOUT", "300")),
        ollama_keep_alive=os.getenv("NEXUS_OLLAMA_KEEP_ALIVE", "10m").strip(),
        whisper_model=os.getenv("NEXUS_WHISPER_MODEL", "small").strip(),
        whisper_device=os.getenv("NEXUS_WHISPER_DEVICE", "auto").strip(),
        whisper_compute_type=os.getenv("NEXUS_WHISPER_COMPUTE_TYPE", "auto").strip(),
        whisper_language=os.getenv("NEXUS_WHISPER_LANGUAGE", "").strip(),
        embedding_provider=os.getenv("NEXUS_EMBEDDING_PROVIDER", "ollama").strip().lower(),
        embedding_model=os.getenv("NEXUS_EMBEDDING_MODEL", "nomic-embed-text").strip(),
        semantic_min_similarity=float(os.getenv("NEXUS_SEMANTIC_MIN_SIMILARITY", "0.6")),
        semantic_margin=float(os.getenv("NEXUS_SEMANTIC_MARGIN", "0.08")),
        semantic_window=float(os.getenv("NEXUS_SEMANTIC_WINDOW", "0.12")),
        art_auto_analyze=os.getenv("NEXUS_ART_AUTO_ANALYZE", "true").strip().lower() not in ("0", "false", "no", "off"),
        backup_dir=_resolve(os.getenv("NEXUS_BACKUP_DIR")) if os.getenv("NEXUS_BACKUP_DIR", "").strip() else None,
        backup_every_days=float(os.getenv("NEXUS_BACKUP_EVERY_DAYS", "7")),
        backup_keep=max(1, int(os.getenv("NEXUS_BACKUP_KEEP", "4"))),
        owner_name=os.getenv("NEXUS_OWNER_NAME", "").strip()[:80],
    )
    return _with_phone_host(_with_preferences(base))


def _with_phone_host(base: Settings) -> Settings:
    """The private phone address turned on in Settings → Your phone (kept in <data>/phone.json)."""
    try:
        host = json.loads((base.data_dir / "phone.json").read_text(encoding="utf-8")).get("host")
    except (OSError, ValueError, AttributeError):
        return base
    if not isinstance(host, str) or not host.endswith(".ts.net") or host in base.allowed_hosts:
        return base
    return replace(base, allowed_hosts=[*base.allowed_hosts, host])


# Settings the app itself may change (Settings > Preferences), with how to read them back.
PREFERENCE_TYPES = {
    "ollama_model": str, "ollama_vision_model": str, "embedding_model": str,
    "whisper_model": str, "whisper_language": str, "art_auto_analyze": bool,
    "semantic_min_similarity": float, "backup_dir": Path, "backup_every_days": float, "backup_keep": int,
    "owner_name": str,
}


def preferences_file(data_dir: Path) -> Path:
    return data_dir / "preferences.json"


def read_preferences(data_dir: Path) -> dict:
    """The preferences saved from the app (only known keys; a damaged file counts as empty)."""
    try:
        raw = json.loads(preferences_file(data_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in raw.items() if k in PREFERENCE_TYPES} if isinstance(raw, dict) else {}


def _with_preferences(base: Settings) -> Settings:
    changes = {}
    for key, value in read_preferences(base.data_dir).items():
        kind = PREFERENCE_TYPES[key]
        try:
            if kind is Path:
                changes[key] = _resolve(value) if value else None
            elif kind is bool:
                changes[key] = bool(value)
            else:
                changes[key] = kind(value)
        except (TypeError, ValueError):
            continue  # ignore a bad value rather than refuse to start
    return replace(base, **changes) if changes else base
