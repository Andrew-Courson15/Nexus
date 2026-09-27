"""File uploads: voice recordings, images/sketches and other documents.

Original files are written once, under a random name, and never modified.
"""
from __future__ import annotations

import hashlib
import mimetypes
import re
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models import Attachment, CreativeEntry, EntryType, Origin, Project, SourceType
from app.routes.entries import capture_time, existing_capture, load_entry
from app.schemas import EntryDetail, entry_detail
from app.services.history import record_revision
from app.services.images import classify_image, ensure_preview, thumbnail, visual_file
from app.services.processing import enqueue
from app.services.search import index_entry

router = APIRouter(prefix="/api/uploads", tags=["uploads"])

_CHUNK = 1024 * 1024
_EXT_RE = re.compile(r"^\.[A-Za-z0-9]{1,10}$")


def _classify(content_type: str | None, filename: str) -> tuple[str, str]:
    mime = (content_type or "").split(";")[0].strip().lower()
    if not mime or mime == "application/octet-stream":
        mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    if mime.startswith("audio/") or (mime == "video/webm" and "record" in filename.lower()):
        return "audio", mime
    if image_mime := classify_image(mime, filename):
        return "image", image_mime
    return "file", mime


async def _store(upload: UploadFile, dest_dir: Path, mime: str) -> tuple[Path, int, str]:
    settings = get_settings()
    limit = settings.max_upload_mb * 1024 * 1024
    ext = Path(upload.filename or "").suffix
    if not _EXT_RE.match(ext or ""):
        ext = mimetypes.guess_extension(mime) or ".bin"
    dest = dest_dir / f"{uuid.uuid4().hex}{ext.lower()}"
    digest, size = hashlib.sha256(), 0
    try:
        with dest.open("xb") as fh:
            while chunk := await upload.read(_CHUNK):
                size += len(chunk)
                if size > limit:
                    raise HTTPException(413, f"File exceeds {settings.max_upload_mb} MB limit")
                digest.update(chunk)
                fh.write(chunk)
    except BaseException:
        dest.unlink(missing_ok=True)
        raise
    if size == 0:
        dest.unlink(missing_ok=True)
        raise HTTPException(400, "Uploaded file is empty")
    return dest, size, digest.hexdigest()


@router.post("", response_model=EntryDetail, status_code=201)
async def upload_file(
    file: UploadFile = File(...),
    note: str | None = Form(None, description="Optional words from the user; stored as original text."),
    title: str | None = Form(None),
    entry_type: EntryType | None = Form(None),
    project_id: int | None = Form(None),
    analyze: bool = Form(True),
    client_id: str | None = Form(None, min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$"),
    captured_at: datetime | None = Form(None),
    response: Response = None,
    db: Session = Depends(get_db),
):
    if (dup := existing_capture(db, client_id)) is not None:
        response.status_code = 200  # a re-send of something already saved
        return entry_detail(dup)
    settings = get_settings()
    original_name = Path(file.filename or "upload").name[:500] or "upload"
    kind, mime = _classify(file.content_type, original_name)
    dest_dir, source_type = {
        "audio": (settings.audio_dir, SourceType.voice.value),
        "image": (settings.images_dir, SourceType.image.value),
        "file": (settings.uploads_dir, SourceType.file.value),
    }[kind]
    dest_dir.mkdir(parents=True, exist_ok=True)

    project = None
    if project_id is not None:
        project = db.get(Project, project_id)
        if project is None:
            raise HTTPException(400, f"Project {project_id} does not exist")

    path, size, sha = await _store(file, dest_dir, mime)
    rel_path = path.relative_to(settings.data_dir).as_posix()

    created = capture_time(captured_at)
    entry = CreativeEntry(
        original_text=note if note and note.strip() else None,
        source_type=source_type,
        source_file=rel_path,
        ai_metadata={},
        client_id=client_id,
        created_at=created,
        updated_at=created,
    )
    if title and title.strip():
        entry.title, entry.title_source = title.strip()[:300], Origin.user.value
    if entry_type:
        entry.entry_type, entry.entry_type_source = entry_type.value, Origin.user.value
    if project:
        entry.projects.append(project)
    attachment = Attachment(
        kind=kind, original_filename=original_name, stored_path=rel_path,
        mime_type=mime, size_bytes=size, sha256=sha,
    )
    entry.attachments.append(attachment)
    # A viewable JPEG/PNG copy for TIFF, PSD, SVG, PDF, HEIC ... (derived; the original stays as uploaded).
    if problem := await run_in_threadpool(ensure_preview, attachment):
        entry.ai_metadata = {"preview_error": problem}
    db.add(entry)
    try:
        db.flush()
    except IntegrityError:  # the same capture arrived twice at the same moment
        db.rollback()
        path.unlink(missing_ok=True)
        response.status_code = 200
        return entry_detail(existing_capture(db, client_id))
    record_revision(db, entry, f"Captured original {kind} upload")
    index_entry(db, entry)
    db.commit()  # the original file and entry are safe before any AI work

    if analyze or kind == "audio":
        # Transcription (voice) and organization run after the upload is saved.
        enqueue(db, entry, transcribe=True, analyze=analyze)
    return entry_detail(load_entry(db, entry.id))


_IMAGE_HEADERS = {"X-Content-Type-Options": "nosniff", "Content-Security-Policy": "sandbox"}


@router.get("/{attachment_id}/thumb")
def get_thumb(attachment_id: int, db: Session = Depends(get_db)):
    """A small picture for cards and grids (and the phone's offline copy)."""
    att = db.get(Attachment, attachment_id)
    if att is None:
        raise HTTPException(404, "File not found")
    if visual_file(att) is None or not visual_file(att)[0].is_file():
        att.preview_path = None
        if ensure_preview(att) is None:
            db.commit()
    found = thumbnail(att)
    if found is None:
        raise HTTPException(415, "No picture for this file")
    return FileResponse(found[0], media_type=found[1],
                        headers={**_IMAGE_HEADERS, "Cache-Control": "private, max-age=604800"})


@router.get("/{attachment_id}/preview")
def get_preview(attachment_id: int, db: Session = Depends(get_db)):
    """A JPEG/PNG to display: the original if the browser can show it, else a rendering of it."""
    att = db.get(Attachment, attachment_id)
    if att is None:
        raise HTTPException(404, "File not found")
    if visual_file(att) is None or not visual_file(att)[0].is_file():
        # Uploaded before previews existed, or the preview was deleted: make it now.
        att.preview_path = None
        if problem := ensure_preview(att):
            raise HTTPException(415, f"No preview: {problem}")
        db.commit()
    found = visual_file(att)
    if found is None:
        raise HTTPException(415, "No preview for this file type")
    path, mime = found
    data_dir = get_settings().data_dir.resolve()
    path = path.resolve()
    if not path.is_relative_to(data_dir) or not path.is_file():
        raise HTTPException(404, "File missing on disk")
    return FileResponse(path, media_type=mime,
                        headers={"X-Content-Type-Options": "nosniff", "Content-Security-Policy": "sandbox",
                                 "Cache-Control": "private, max-age=86400"})


@router.get("/{attachment_id}")
def get_file(attachment_id: int, db: Session = Depends(get_db)):
    """Serve an original file exactly as uploaded."""
    att = db.get(Attachment, attachment_id)
    if att is None:
        raise HTTPException(404, "File not found")
    data_dir = get_settings().data_dir.resolve()
    path = (data_dir / att.stored_path).resolve()
    if not path.is_relative_to(data_dir) or not path.is_file():
        raise HTTPException(404, "File missing on disk")
    inline = att.kind in ("image", "audio")
    return FileResponse(
        path,
        media_type=att.mime_type,
        filename=att.original_filename,
        content_disposition_type="inline" if inline else "attachment",
        headers={"X-Content-Type-Options": "nosniff", "Content-Security-Policy": "sandbox"},
    )
