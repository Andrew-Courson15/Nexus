"""Split a multi-page PDF (a comic, a sketchbook scan, a storyboard) into one picture entry per page.

The PDF itself is never changed. Each page is rendered to a new JPEG and saved as its own entry,
linked to the PDF entry ("part of"), in the same projects, so pages can be viewed, analyzed,
compared and linked one by one.
"""
from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Attachment, CreativeEntry, EntryType, Origin, Relationship, SourceType, utcnow
from app.services.history import record_revision
from app.services.images import PREVIEW_MAX_SIDE, thumbnail
from app.services.search import index_entry

MAX_PAGES = 80


class SplitError(RuntimeError):
    pass


def pdf_attachment(entry: CreativeEntry) -> Attachment | None:
    return next((a for a in entry.attachments if a.mime_type == "application/pdf"), None)


def page_count(path: Path) -> int:
    try:
        import pymupdf
    except ImportError as exc:
        raise SplitError("PDF support isn't installed (pip install pymupdf)") from exc
    try:
        with pymupdf.open(path, filetype="pdf") as doc:
            return doc.page_count
    except Exception as exc:  # damaged or encrypted file
        raise SplitError(f"the PDF couldn't be opened ({exc})") from exc


def existing_pages(db: Session, entry: CreativeEntry) -> list[CreativeEntry]:
    """Page entries made from this PDF earlier (found by their links, so it survives restoring an
    older version of the PDF entry), in page order."""
    rows = db.scalars(select(CreativeEntry).join(Relationship, Relationship.source_entry_id == CreativeEntry.id)
                      .where(Relationship.target_entry_id == entry.id, Relationship.relationship_type == "part_of",
                             CreativeEntry.deleted_at.is_(None))).all()
    pages = [p for p in rows if ((p.ai_metadata or {}).get("page_of") or {}).get("id") == entry.id]
    return sorted(pages, key=lambda p: p.ai_metadata["page_of"].get("page", 0))


def split_pdf(db: Session, entry: CreativeEntry) -> list[CreativeEntry]:
    """Create the page entries (once). Returns them in page order."""
    import pymupdf

    settings = get_settings()
    att = pdf_attachment(entry)
    if att is None:
        raise SplitError("This entry has no PDF.")
    if pages := existing_pages(db, entry):  # split before: never make the pages twice
        if (entry.ai_metadata or {}).get("pages") != [p.id for p in pages]:
            entry.ai_metadata = {**(entry.ai_metadata or {}), "pages": [p.id for p in pages]}
            db.commit()
        return pages
    source = settings.data_dir / att.stored_path
    count = page_count(source)
    if count < 2:
        raise SplitError("This PDF has only one page.")
    if count > MAX_PAGES:
        raise SplitError(f"This PDF has {count} pages; up to {MAX_PAGES} can be split into entries.")

    settings.images_dir.mkdir(parents=True, exist_ok=True)
    stem = Path(att.original_filename).stem[:120] or "PDF"
    pages: list[CreativeEntry] = []
    written: list[Path] = []
    try:
        with pymupdf.open(source, filetype="pdf") as doc:
            for number, page in enumerate(doc, start=1):
                longest = max(page.rect.width, page.rect.height) or 1
                zoom = min(4.0, max(0.5, PREVIEW_MAX_SIDE / longest))
                pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
                data = pix.tobytes("jpeg", jpg_quality=90)  # a faithful rendering, far smaller than PNG
                dest = settings.images_dir / f"{uuid.uuid4().hex}.jpg"
                with dest.open("xb") as fh:
                    fh.write(data)
                written.append(dest)
                now = utcnow()
                page_entry = CreativeEntry(
                    title=f"{stem} — page {number}", title_source=Origin.user.value,
                    entry_type=entry.entry_type if entry.entry_type_source == Origin.user.value else EntryType.artwork.value,
                    entry_type_source=entry.entry_type_source,
                    source_type=SourceType.image.value,
                    source_file=dest.relative_to(settings.data_dir).as_posix(),
                    ai_metadata={"page_of": {"id": entry.id, "page": number, "pages": count}},
                    created_at=now, updated_at=now,
                )
                page_entry.projects.extend(p for p in entry.projects if p.deleted_at is None)
                page_entry.attachments.append(Attachment(
                    kind="image", original_filename=f"{stem} - page {number}.jpg",
                    stored_path=page_entry.source_file, mime_type="image/jpeg", size_bytes=len(data),
                    sha256=hashlib.sha256(data).hexdigest(),
                ))
                db.add(page_entry)
                db.flush()
                db.add(Relationship(source_entry_id=page_entry.id, target_entry_id=entry.id,
                                    relationship_type="part_of", origin=Origin.user.value, status="accepted",
                                    rationale=f"Page {number} of {count}"))
                record_revision(db, page_entry, f"Page {number} of the PDF “{att.original_filename}”")
                index_entry(db, page_entry)
                pages.append(page_entry)
        entry.ai_metadata = {**(entry.ai_metadata or {}), "pages": [p.id for p in pages]}
        db.commit()
    except BaseException:
        db.rollback()
        for path in written:
            path.unlink(missing_ok=True)
        raise
    for p in pages:  # small copies for grids and the phone; failures just mean no thumbnail yet
        try:
            thumbnail(p.attachments[0])
        except Exception:
            pass
    return pages
