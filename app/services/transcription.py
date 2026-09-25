"""Transcription abstraction.

The transcript is DERIVED data: it is stored in `CreativeEntry.transcript`,
separately from the original audio file, and can be regenerated at any time.

Select a provider with `NEXUS_TRANSCRIPTION_PROVIDER`. Version 1 ships only a
placeholder that stores nothing (the transcript stays UNKNOWN). Future
providers (local Whisper, OpenAI Whisper, ...) implement
`TranscriptionProvider.transcribe` and register in `PROVIDERS`.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from app.config import get_settings


@dataclass
class TranscriptionResult:
    text: str | None          # None = no transcript produced (UNKNOWN)
    provider: str
    status: str               # "completed" | "unavailable" | "failed"
    language: str | None = None
    message: str | None = None


class TranscriptionProvider(ABC):
    name = "base"
    external = False

    @abstractmethod
    def transcribe(self, audio_path: Path) -> TranscriptionResult: ...


class PlaceholderTranscriptionProvider(TranscriptionProvider):
    """Keeps the audio and records that no transcript exists yet.

    It deliberately does NOT invent text: a fabricated transcript would be
    indistinguishable from something the user actually said.
    """

    name = "placeholder"

    def transcribe(self, audio_path: Path) -> TranscriptionResult:
        return TranscriptionResult(
            text=None,
            provider=self.name,
            status="unavailable",
            message="No transcription provider is configured. The audio is stored and can be transcribed later.",
        )


class _ReservedProvider(TranscriptionProvider):
    def transcribe(self, audio_path: Path) -> TranscriptionResult:
        return TranscriptionResult(
            text=None,
            provider=self.name,
            status="unavailable",
            message=f"The '{self.name}' transcription provider is reserved for a future version.",
        )


class LocalWhisperProvider(_ReservedProvider):
    name = "whisper_local"


class OpenAIWhisperProvider(_ReservedProvider):
    name = "openai_whisper"
    external = True


PROVIDERS: dict[str, type[TranscriptionProvider]] = {
    "placeholder": PlaceholderTranscriptionProvider,
    "whisper_local": LocalWhisperProvider,
    "openai_whisper": OpenAIWhisperProvider,
}


class TranscriptionService:
    """Entry point used by the rest of the app; hides which provider is active."""

    def __init__(self, provider: TranscriptionProvider | None = None):
        if provider is None:
            key = get_settings().transcription_provider
            provider = PROVIDERS.get(key, PlaceholderTranscriptionProvider)()
        self.provider = provider

    def transcribe(self, audio_path: str | Path) -> TranscriptionResult:
        path = Path(audio_path)
        if not path.is_file():
            return TranscriptionResult(None, self.provider.name, "failed", message="Audio file not found.")
        try:
            return self.provider.transcribe(path)
        except Exception as exc:  # a failed transcription must never lose the recording
            return TranscriptionResult(None, self.provider.name, "failed", message=str(exc))
