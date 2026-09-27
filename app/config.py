"""Application configuration, read from environment variables (and `.env`).

API keys are only ever read from the environment; they are never persisted.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def _resolve(path: str) -> Path:
    p = Path(path).expanduser()
    return p if p.is_absolute() else (BASE_DIR / p).resolve()


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
    semantic_min_similarity: float = 0.5
    semantic_window: float = 0.15  # keep meaning-matches within this distance of the best one
    # Visual Art Intelligence: analyse new images automatically (needs a vision model)
    art_auto_analyze: bool = True

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

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.uploads_dir, self.audio_dir, self.images_dir, self.exports_dir):
            d.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    data_dir = _resolve(os.getenv("NEXUS_DATA_DIR", "data"))
    database_url = os.getenv("NEXUS_DATABASE_URL") or f"sqlite:///{data_dir / 'nexus.db'}"
    allowed = os.getenv("NEXUS_ALLOWED_HOSTS", "localhost,127.0.0.1")
    return Settings(
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
        semantic_min_similarity=float(os.getenv("NEXUS_SEMANTIC_MIN_SIMILARITY", "0.5")),
        semantic_window=float(os.getenv("NEXUS_SEMANTIC_WINDOW", "0.15")),
        art_auto_analyze=os.getenv("NEXUS_ART_AUTO_ANALYZE", "true").strip().lower() not in ("0", "false", "no", "off"),
    )
