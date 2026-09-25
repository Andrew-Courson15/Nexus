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
        ai_provider=os.getenv("NEXUS_AI_PROVIDER", "local").strip().lower(),
        transcription_provider=os.getenv("NEXUS_TRANSCRIPTION_PROVIDER", "placeholder").strip().lower(),
        relationship_threshold=float(os.getenv("NEXUS_RELATIONSHIP_THRESHOLD", "0.25")),
    )
