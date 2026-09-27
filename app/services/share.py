"""Sharing between separate NEXUS installs: export entries to a .nexus file, import them elsewhere.

Two people each keep their own private NEXUS. To give someone a sketch, a voice note or a
whole project, NEXUS writes a `.nexus` file (a zip) you can send any way you like (AirDrop,
email, USB). Nothing goes through a server.

What travels is the *user's material*: their words, notes, transcript, user tags, title/type/
status choices and every original file, byte for byte. AI-derived data (summaries, AI tags,
art analyses) stays behind: the receiving NEXUS organizes the entries again with its own
local model. Each imported entry records whose it is (`shared_from`), and a share key
(`<their install id>:<their entry id>`) makes importing the same file twice harmless.
"""
from __future__ import annotations

import hashlib
import json
import mimetypes
import re
import shutil
import tempfile
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path, PurePosixPath

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.config import get_settings
from app.models import (
    Attachment,
    CreativeEntry,
    EntryStatus,
    EntryTag,
    EntryType,
    Origin,
    Project,
    ProjectType,
    Relationship,
    RelationshipStatus,
    RelationshipType,
    SourceType,
    utcnow,
)
from app.services.history import add_tag, record_revision
from app.services.images import classify_image, ensure_preview
from app.services.search import index_entry

FORMAT = "nexus-share"
VERSION = 1
MAX_ENTRIES = 5000
MAX_TOTAL_BYTES = 4 * 1024**3  # refuse absurd packages (zip bombs)
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._ -]+")


class ShareError(ValueError):
    pass


def install_id() -> str:
    """A random id for this NEXUS install, made once (so shares can be told apart)."""
    path = get_settings().data_dir / "install_id"
    try:
        value = path.read_text().strip()
        if re.fullmatch(r"[0-9a-f]{32}", value):
            return value
    except OSError:
        pass
    value = uuid.uuid4().hex
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value)
    return value


# -- export --------------------------------------------------------------------
def _load(db: Session, ids: list[int]) -> list[CreativeEntry]:
    rows = db.scalars(select(CreativeEntry).where(CreativeEntry.id.in_(ids), CreativeEntry.deleted_at.is_(None))
                      .options(selectinload(CreativeEntry.entry_tags).selectinload(EntryTag.tag),
                               selectinload(CreativeEntry.attachments), selectinload(CreativeEntry.projects))
                      .order_by(CreativeEntry.created_at, CreativeEntry.id)).all()
    return list(rows)


def export_package(db: Session, entry_ids: list[int] | None = None, project_id: int | None = None,
                   by: str = "") -> tuple[Path, str]:
    """Write a .nexus file to a temporary path. Returns (path, suggested file name)."""
    project = None
    if project_id is not None:
        project = db.get(Project, project_id)
        if project is None or project.deleted_at is not None:
            raise ShareError("That project doesn't exist.")
        entry_ids = [e.id for e in project.entries]
    entries = _load(db, list(dict.fromkeys(entry_ids or [])))
    if not entries:
        raise ShareError("Nothing to share: choose at least one entry.")
    data_dir = get_settings().data_dir
    ids = {e.id for e in entries}
    items = []
    tmp = Path(tempfile.mkstemp(suffix=".nexus")[1])
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for e in entries:
            files = []
            for n, a in enumerate(e.attachments):
                src = data_dir / a.stored_path
                if not src.is_file():
                    continue
                arc = f"files/{e.id}/{n}-{_SAFE_NAME.sub('_', a.original_filename)[:120] or 'file'}"
                zf.write(src, arc, compress_type=zipfile.ZIP_STORED)
                files.append({"file": arc, "kind": a.kind, "original_filename": a.original_filename,
                              "mime_type": a.mime_type, "sha256": a.sha256, "size_bytes": a.size_bytes})
            items.append({
                "entry_id": e.id,
                "title": e.title if e.title_source == Origin.user.value else None,
                "entry_type": e.entry_type if e.entry_type_source == Origin.user.value else None,
                "status": e.status,
                "source_type": e.source_type,
                "original_text": e.original_text,
                "user_notes": e.user_notes,  # the exporter's own notes
                # Re-sharing something that came from someone else: they stay its author.
                "author": (e.shared_from or {}).get("by"),
                "author_notes": (e.shared_from or {}).get("notes"),
                "transcript": e.transcript,  # machine-made from their recording: saves re-transcribing
                "user_tags": sorted(et.tag.name for et in e.entry_tags if et.source == Origin.user.value),
                "created_at": e.created_at.isoformat(),
                "attachments": files,
            })
        rels = db.scalars(select(Relationship).where(
            Relationship.source_entry_id.in_(ids), Relationship.target_entry_id.in_(ids),
            (Relationship.origin == Origin.user.value) | (Relationship.status == RelationshipStatus.accepted.value)))
        manifest = {
            "format": FORMAT, "version": VERSION, "exported_at": utcnow().isoformat(),
            "by": by.strip()[:80], "install_id": install_id(),
            "project": {"name": project.name, "description": project.description,
                        "project_type": project.project_type} if project else None,
            "entries": items,
            "relationships": [{"source": r.source_entry_id, "target": r.target_entry_id,
                               "relationship_type": r.relationship_type} for r in rels],
        }
        zf.writestr("manifest.json", json.dumps(manifest, indent=1))
    stem = project.name if project else (entries[0].title if len(entries) == 1 else f"{len(entries)} ideas")
    name = f"{_SAFE_NAME.sub('', stem).strip()[:60] or 'NEXUS share'} ({datetime.now():%Y-%m-%d}).nexus"
    return tmp, name


# -- import --------------------------------------------------------------------
@dataclass
class ImportResult:
    by: str
    imported: list[int] = field(default_factory=list)
    already_had: list[int] = field(default_factory=list)
    project_id: int | None = None
    project_name: str | None = None


def _read_manifest(zf: zipfile.ZipFile) -> dict:
    try:
        manifest = json.loads(zf.read("manifest.json"))
    except (KeyError, json.JSONDecodeError) as exc:
        raise ShareError("This isn't a NEXUS share file.") from exc
    if manifest.get("format") != FORMAT:
        raise ShareError("This isn't a NEXUS share file.")
    if manifest.get("version", 0) > VERSION:
        raise ShareError("This file was made by a newer NEXUS. Update NEXUS, then import it again.")
    if not re.fullmatch(r"[0-9a-f]{32}", str(manifest.get("install_id", ""))):
        raise ShareError("The share file is damaged (no install id).")
    entries = manifest.get("entries")
    if not isinstance(entries, list) or not entries or len(entries) > MAX_ENTRIES:
        raise ShareError("The share file has no entries.")
    if sum(i.file_size for i in zf.infolist()) > MAX_TOTAL_BYTES:
        raise ShareError("The share file is too large to import.")
    return manifest


def _choice(value, allowed: set[str]) -> str | None:
    return value if isinstance(value, str) and value in allowed else None


def import_package(db: Session, path: Path) -> ImportResult:
    """Import a .nexus file. Entries already imported before are skipped, never duplicated."""
    from app.services.processing import enqueue

    settings = get_settings()
    try:
        zf = zipfile.ZipFile(path)
    except zipfile.BadZipFile as exc:
        raise ShareError("This isn't a NEXUS share file.") from exc
    created: list[CreativeEntry] = []
    with zf:
        manifest = _read_manifest(zf)
        names = set(zf.namelist())
        theirs = manifest["install_id"]
        if theirs == install_id():
            raise ShareError("This file was exported from this same NEXUS; everything in it is already here.")
        by = str(manifest.get("by") or "").strip()[:80] or "someone"
        result = ImportResult(by=by)
        stamp = {"by": by, "install_id": theirs, "exported_at": manifest.get("exported_at"),
                 "imported_at": utcnow().isoformat()}

        project = None
        if isinstance(manifest.get("project"), dict) and str(manifest["project"].get("name", "")).strip():
            p = manifest["project"]
            pname = str(p["name"]).strip()[:300]
            project = db.scalar(select(Project).where(Project.name == pname, Project.deleted_at.is_(None)))
            if project is None:
                project = Project(name=pname, description=(p.get("description") or None),
                                  project_type=_choice(p.get("project_type"), {t.value for t in ProjectType})
                                  or ProjectType.other.value)
                db.add(project)
                db.flush()
            result.project_id, result.project_name = project.id, project.name

        id_map: dict[int, int] = {}
        written: list[Path] = []
        try:
            for item in manifest["entries"]:
                if not isinstance(item, dict) or not isinstance(item.get("entry_id"), int):
                    raise ShareError("The share file is damaged (an entry has no id).")
                key = f"{theirs}:{item['entry_id']}"
                existing = db.scalar(select(CreativeEntry).where(CreativeEntry.share_key == key))
                if existing is not None:
                    id_map[item["entry_id"]] = existing.id
                    result.already_had.append(existing.id)
                    if project is not None and existing.deleted_at is None and project not in existing.projects:
                        existing.projects.append(project)
                    continue
                entry = _import_entry(db, zf, names, item, key, stamp, settings, written)
                if project is not None:
                    entry.projects.append(project)
                id_map[item["entry_id"]] = entry.id
                created.append(entry)
                result.imported.append(entry.id)

            for r in manifest.get("relationships") or []:
                src, dst = id_map.get(r.get("source")), id_map.get(r.get("target"))
                rtype = _choice(r.get("relationship_type"), {t.value for t in RelationshipType})
                if not src or not dst or not rtype or src == dst:
                    continue
                if db.scalar(select(Relationship.id).where(Relationship.source_entry_id == src,
                                                           Relationship.target_entry_id == dst,
                                                           Relationship.relationship_type == rtype)):
                    continue
                db.add(Relationship(source_entry_id=src, target_entry_id=dst, relationship_type=rtype,
                                    origin=Origin.user.value, status=RelationshipStatus.accepted.value,
                                    rationale=f"Linked by {by} in their NEXUS."))
            for entry in created:
                record_revision(db, entry, f"Imported from {by}'s NEXUS")
                index_entry(db, entry)
            db.commit()
        except BaseException:
            db.rollback()
            for f in written:
                f.unlink(missing_ok=True)
            raise
    for entry in created:  # our own AI organizes their material (their words stay untouched)
        enqueue(db, entry, transcribe=True, analyze=True)
    return result


def _import_entry(db: Session, zf: zipfile.ZipFile, names: set[str], item: dict, key: str, stamp: dict,
                  settings, written: list[Path]) -> CreativeEntry:
    def text(k: str) -> str | None:
        v = item.get(k)
        return v if isinstance(v, str) and v.strip() else None

    author = text("author") or stamp["by"]
    notes = text("author_notes") if text("author") else text("user_notes")
    if text("author") and text("user_notes"):  # a re-share: the sender's own notes, attributed to them
        notes = "\n\n".join(filter(None, [notes, f"Notes from {stamp['by']}:\n{text('user_notes')}"]))
    try:
        created = datetime.fromisoformat(item.get("created_at", ""))
    except (TypeError, ValueError):
        created = utcnow()
    entry = CreativeEntry(
        original_text=text("original_text"), transcript=text("transcript"),
        status=_choice(item.get("status"), {s.value for s in EntryStatus}) or EntryStatus.raw.value,
        source_type=_choice(item.get("source_type"), {s.value for s in SourceType}) or SourceType.text.value,
        ai_metadata={"transcript_source": "shared"} if text("transcript") else {},
        # Their notes are kept with the share, apart from the notes *you* add later.
        shared_from={**stamp, "by": author, "entry_id": item["entry_id"], **({"notes": notes} if notes else {})},
        share_key=key,
        created_at=created, updated_at=utcnow(),
    )
    if t := text("title"):
        entry.title, entry.title_source = t[:300], Origin.user.value
    if et := _choice(item.get("entry_type"), {t.value for t in EntryType}):
        entry.entry_type, entry.entry_type_source = et, Origin.user.value
    db.add(entry)
    db.flush()
    for tag in item.get("user_tags") or []:
        if isinstance(tag, str):
            add_tag(db, entry, tag, Origin.user.value)

    dirs = {"image": settings.images_dir, "audio": settings.audio_dir, "file": settings.uploads_dir}
    for f in item.get("attachments") or []:
        arc = str(f.get("file", ""))
        p = PurePosixPath(arc)
        if arc not in names or p.is_absolute() or ".." in p.parts or not arc.startswith("files/"):
            raise ShareError("The share file is damaged (a file is missing or has an unsafe name).")
        # Don't trust the file's stated type: work it out again, as for a normal upload.
        original_name = Path(str(f.get("original_filename") or "file")).name[:500] or "file"
        stated = str(f.get("mime_type") or "").split(";")[0].strip().lower()
        guessed = mimetypes.guess_type(original_name)[0] or "application/octet-stream"
        if f.get("kind") == "audio" and (stated.startswith("audio/") or guessed.startswith("audio/")
                                         or stated == "video/webm"):
            kind, mime = "audio", stated if stated.startswith("audio/") or stated == "video/webm" else guessed
        elif image_mime := classify_image(guessed if guessed != "application/octet-stream" else stated, original_name):
            kind, mime = "image", image_mime
        else:
            kind, mime = "file", guessed
        ext = Path(original_name).suffix.lower()
        ext = ext if re.fullmatch(r"\.[a-z0-9]{1,10}", ext) else ".bin"
        dest_dir = dirs[kind]
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"{uuid.uuid4().hex}{ext}"
        digest, size = hashlib.sha256(), 0
        with zf.open(arc) as src, dest.open("xb") as out:
            written.append(dest)
            while chunk := src.read(1024 * 1024):
                digest.update(chunk)
                size += len(chunk)
                out.write(chunk)
        if f.get("sha256") and digest.hexdigest() != f["sha256"]:
            raise ShareError(f"{f.get('original_filename')} was damaged on the way (its fingerprint doesn't match).")
        rel = dest.relative_to(settings.data_dir).as_posix()
        att = Attachment(kind=kind, original_filename=original_name, stored_path=rel, mime_type=mime[:200],
                         size_bytes=size, sha256=digest.hexdigest())
        entry.attachments.append(att)
        if entry.source_file is None:
            entry.source_file = rel
        ensure_preview(att)
    return entry


def import_package_from_upload(db: Session, fileobj, limit_bytes: int = MAX_TOTAL_BYTES) -> ImportResult:
    """Save an uploaded .nexus file to a temporary place, then import it."""
    with tempfile.NamedTemporaryFile(suffix=".nexus", delete=False) as tmp:
        size = 0
        while chunk := fileobj.read(1024 * 1024):
            size += len(chunk)
            if size > limit_bytes:
                tmp.close()
                Path(tmp.name).unlink(missing_ok=True)
                raise ShareError("The share file is too large to import.")
            tmp.write(chunk)
    try:
        return import_package(db, Path(tmp.name))
    finally:
        Path(tmp.name).unlink(missing_ok=True)


def cleanup(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True) if path.is_dir() else path.unlink(missing_ok=True)
