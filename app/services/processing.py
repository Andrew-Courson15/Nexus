"""Background processing: transcription and AI organization after capture.

Captures are saved first and the request returns immediately; slow local-model
work happens here, one entry at a time, in a single worker thread (so Ollama
and Whisper are never asked to do several things at once).

Status lives in `CreativeEntry.processing_status`:
    queued -> transcribing -> analyzing -> done | failed
Entries left unfinished by a shutdown are picked up again on the next start.

Set NEXUS_PROCESSING_MODE=sync to run the pipeline inside the request instead
(used by the test suite).
"""
from __future__ import annotations

import logging
import queue
import threading
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.config import get_settings
from app.database import SessionLocal
from app.models import ACTIVE_PROCESSING, CreativeEntry, EntryTag, SourceType
from app.services.history import record_revision
from app.services.organizer import Organizer
from app.services.search import index_entry
from app.services.transcription import TranscriptionService

log = logging.getLogger("nexus.processing")


@dataclass(frozen=True)
class Job:
    entry_id: int
    transcribe: bool = True
    analyze: bool = True
    force_transcribe: bool = False


def _load(db: Session, entry_id: int) -> CreativeEntry | None:
    return db.scalar(
        select(CreativeEntry).where(CreativeEntry.id == entry_id).options(
            selectinload(CreativeEntry.entry_tags).selectinload(EntryTag.tag),
            selectinload(CreativeEntry.attachments), selectinload(CreativeEntry.projects),
        )
    )


def transcribe_entry(db: Session, entry: CreativeEntry, force: bool = False) -> bool:
    """Transcribe a voice entry's audio. Returns True if a transcript was stored."""
    if entry.source_type != SourceType.voice.value:
        return False
    if entry.transcript and not force:
        return False
    audio = next((a for a in entry.attachments if a.kind == "audio"), None)
    if audio is None:
        return False
    result = TranscriptionService().transcribe(get_settings().data_dir / audio.stored_path)
    meta = dict(entry.ai_metadata or {})
    meta["transcription"] = {"provider": result.provider, "status": result.status, "language": result.language,
                             "message": result.message, "duration": result.duration}
    if result.text:
        entry.transcript = result.text  # derived, stored separately from the audio
        meta.pop("transcript_source", None)
    entry.ai_metadata = meta
    if result.text:
        record_revision(db, entry, f"Transcribed ({result.provider})")
    return bool(result.text)


def run_job(job: Job) -> str:
    """Run one job in its own session. Returns the final status."""
    with SessionLocal() as db:
        entry = _load(db, job.entry_id)
        if entry is None or entry.deleted_at is not None:
            return "skipped"
        try:
            if job.transcribe and entry.source_type == SourceType.voice.value:
                entry.processing_status = "transcribing"
                db.commit()
                transcribe_entry(db, entry, force=job.force_transcribe)
                index_entry(db, entry)
                db.commit()
            ok = True
            if job.analyze:
                entry.processing_status = "analyzing"
                db.commit()
                ok = Organizer(db).organize(entry).ok
            entry.processing_status = "done" if ok else "failed"
            db.commit()
            return entry.processing_status
        except Exception as exc:  # never let one entry kill the worker
            log.exception("Processing entry %s failed", job.entry_id)
            db.rollback()
            entry = _load(db, job.entry_id)
            if entry is not None:
                meta = dict(entry.ai_metadata or {})
                meta["analysis_error"] = f"Processing failed: {exc}"
                entry.ai_metadata = meta
                entry.processing_status = "failed"
                db.commit()
            return "failed"


class ProcessingQueue:
    def __init__(self):
        self._queue: queue.Queue[Job | None] = queue.Queue()
        self._pending: set[int] = set()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    @property
    def sync(self) -> bool:
        return get_settings().processing_mode == "sync"

    def start(self) -> None:
        if self.sync or (self._thread and self._thread.is_alive()):
            return
        self._thread = threading.Thread(target=self._worker, name="nexus-processing", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        if self._thread and self._thread.is_alive():
            self._queue.put(None)
            self._thread.join(timeout)
        self._thread = None

    def submit(self, job: Job) -> str | None:
        """Queue a job (or run it now in sync mode). The caller must have set status to 'queued'."""
        if self.sync:
            return run_job(job)
        with self._lock:
            if job.entry_id in self._pending:
                return None
            self._pending.add(job.entry_id)
        self._queue.put(job)
        return None

    def wait_idle(self, timeout: float = 10.0) -> bool:
        """For tests/tools: block until the queue is drained."""
        import time

        end = time.monotonic() + timeout
        while time.monotonic() < end:
            with self._lock:
                if not self._pending:
                    return True
            time.sleep(0.02)
        return False

    def recover(self) -> int:
        """Re-queue entries left unfinished by a previous shutdown."""
        with SessionLocal() as db:
            ids = db.scalars(select(CreativeEntry.id).where(
                CreativeEntry.processing_status.in_(ACTIVE_PROCESSING), CreativeEntry.deleted_at.is_(None)
            ).order_by(CreativeEntry.id)).all()
            for entry_id in ids:
                db.get(CreativeEntry, entry_id).processing_status = "queued"
            db.commit()
        for entry_id in ids:
            self.submit(Job(entry_id))
        return len(ids)

    def _worker(self) -> None:
        while True:
            job = self._queue.get()
            if job is None:
                return
            try:
                run_job(job)
            finally:
                with self._lock:
                    self._pending.discard(job.entry_id)


processing_queue = ProcessingQueue()


def enqueue(db: Session, entry: CreativeEntry, *, transcribe: bool = True, analyze: bool = True,
            force_transcribe: bool = False) -> None:
    """Mark an entry as queued, commit, and hand it to the worker."""
    entry.processing_status = "queued"
    db.commit()
    processing_queue.submit(Job(entry.id, transcribe, analyze, force_transcribe))
    db.expire_all()  # sync mode changed the row in another session
