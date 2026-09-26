"""Transcription abstraction.

The transcript is DERIVED data: it is stored in `CreativeEntry.transcript`,
separately from the original audio file, and can be regenerated at any time.

Select a provider with `NEXUS_TRANSCRIPTION_PROVIDER`:

whisper_local   Whisper running on this computer via faster-whisper (default).
                Install with: pip install -r requirements-local.txt
                The speech model downloads once on first use, then works offline.
placeholder     Stores audio and leaves the transcript empty (UNKNOWN).
openai_whisper  Reserved for a future cloud option.
"""
from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.config import get_settings

INSTALL_HINT = "Install local transcription with: pip install -r requirements-local.txt"


@dataclass
class TranscriptionResult:
    text: str | None          # None = no transcript produced (UNKNOWN)
    provider: str
    status: str               # "completed" | "empty" | "unavailable" | "failed"
    language: str | None = None
    message: str | None = None
    duration: float | None = None


class TranscriptionProvider(ABC):
    name = "base"
    external = False

    @abstractmethod
    def transcribe(self, audio_path: Path) -> TranscriptionResult: ...

    def health(self) -> dict[str, Any]:
        return {"ok": True, "detail": "Ready."}


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

    def health(self) -> dict[str, Any]:
        return {"ok": False, "detail": "Placeholder only: recordings are kept but not transcribed."}


class LocalWhisperProvider(TranscriptionProvider):
    """Whisper on this machine via faster-whisper (CPU on Mac, CUDA on NVIDIA)."""

    external = False
    _models: dict[tuple, Any] = {}
    _lock = threading.Lock()
    _cpu_fallback = False  # set once the GPU path has failed in this process

    def __init__(self, model_size: str | None = None, device: str | None = None,
                 compute_type: str | None = None, language: str | None = None):
        s = get_settings()
        self.model_size = model_size or s.whisper_model
        self.device = device or s.whisper_device
        self.compute_type = compute_type or s.whisper_compute_type
        self.language = (language if language is not None else s.whisper_language) or None

    @property
    def name(self) -> str:  # type: ignore[override]
        return f"whisper_local:{self.model_size}"

    @staticmethod
    def installed() -> bool:
        try:
            import faster_whisper  # noqa: F401
        except ImportError:
            return False
        return True

    def _effective(self) -> tuple[str, str]:
        if self.device == "auto" and LocalWhisperProvider._cpu_fallback:
            return "cpu", ("int8" if self.compute_type == "auto" else self.compute_type)
        return self.device, self.compute_type

    def load(self):
        """Load (and on first use, download) the speech model. Cached per process."""
        device, compute_type = self._effective()
        key = (self.model_size, device, compute_type)
        with self._lock:
            if key not in self._models:
                from faster_whisper import WhisperModel

                try:
                    self._models[key] = WhisperModel(self.model_size, device=device, compute_type=compute_type)
                except Exception as exc:
                    if _gpu_error(exc):
                        raise
                    raise RuntimeError(
                        f"Couldn't load the Whisper '{self.model_size}' speech model. The first time, it needs an "
                        "internet connection to download it (about 0.5 GB for 'small'); after that it works "
                        f"offline. Use 'Transcribe now' to try again. ({str(exc)[:160]})"
                    ) from exc
            return self._models[key]

    def _run(self, audio_path: Path):
        model = self.load()
        segments, info = model.transcribe(str(audio_path), language=self.language, vad_filter=True, beam_size=5)
        text = " ".join(seg.text.strip() for seg in segments).strip()  # decoding happens while iterating
        return text, info

    def transcribe(self, audio_path: Path) -> TranscriptionResult:
        if not self.installed():
            return TranscriptionResult(None, self.name, "unavailable", message=f"faster-whisper isn't installed. {INSTALL_HINT}")
        note = ""
        try:
            text, info = self._run(audio_path)
        except Exception as exc:
            # NVIDIA PCs without the CUDA libraries (common on Windows): use the CPU instead.
            if self.device != "auto" or LocalWhisperProvider._cpu_fallback or not _gpu_error(exc):
                raise
            LocalWhisperProvider._cpu_fallback = True
            text, info = self._run(audio_path)
            note = " (ran on the CPU: GPU libraries for Whisper aren't installed)"
        language = getattr(info, "language", None)
        duration = getattr(info, "duration", None)
        if not text:
            return TranscriptionResult(None, self.name, "empty", language,
                                       "No speech was detected in the recording." + note, duration)
        prob = getattr(info, "language_probability", None)
        msg = f"Detected language: {language}" + (f" ({prob:.0%})" if isinstance(prob, float) else "") + note
        return TranscriptionResult(text, self.name, "completed", language, msg, duration)

    def health(self) -> dict[str, Any]:
        if not self.installed():
            return {"ok": False, "detail": f"faster-whisper isn't installed. {INSTALL_HINT}"}
        loaded = any(k[0] == self.model_size for k in self._models)
        return {"ok": True, "detail": f"Whisper '{self.model_size}' ready"
                + ("" if loaded else " (the model downloads/loads on first recording)") + "."}


def _gpu_error(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return any(k in msg for k in ("cublas", "cudnn", "cuda", "libcu", ".dll", "gpu"))


class OpenAIWhisperProvider(TranscriptionProvider):
    name = "openai_whisper"
    external = True

    def transcribe(self, audio_path: Path) -> TranscriptionResult:
        return TranscriptionResult(None, self.name, "unavailable",
                                   message="The 'openai_whisper' provider is reserved for a future version.")

    def health(self) -> dict[str, Any]:
        return {"ok": False, "detail": "Not implemented yet."}


PROVIDERS: dict[str, Callable[[], TranscriptionProvider]] = {
    "whisper_local": LocalWhisperProvider,
    "placeholder": PlaceholderTranscriptionProvider,
    "openai_whisper": OpenAIWhisperProvider,
}


def get_transcription_provider(name: str | None = None) -> TranscriptionProvider:
    key = (name or get_settings().transcription_provider or "whisper_local").lower()
    return PROVIDERS.get(key, PlaceholderTranscriptionProvider)()


class TranscriptionService:
    """Entry point used by the rest of the app; hides which provider is active."""

    def __init__(self, provider: TranscriptionProvider | None = None):
        self.provider = provider or get_transcription_provider()

    def transcribe(self, audio_path: str | Path) -> TranscriptionResult:
        path = Path(audio_path)
        if not path.is_file():
            return TranscriptionResult(None, self.provider.name, "failed", message="Audio file not found.")
        try:
            return self.provider.transcribe(path)
        except Exception as exc:  # a failed transcription must never lose the recording
            msg = str(exc) if isinstance(exc, RuntimeError) else f"Transcription failed: {exc}"
            return TranscriptionResult(None, self.provider.name, "failed", message=msg)
