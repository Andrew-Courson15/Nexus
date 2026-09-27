"""Organizing many entries at once: bulk actions, and renaming / merging / deleting tags.

Every change to an entry is recorded in its revision history (like a single edit), the
word index is updated, and nothing is ever destroyed: 'trash' is the usual soft delete.
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.config import get_settings
from app.database import get_db
from app.models import CreativeEntry, EntryStatus, EntryTag, Origin, Project, Tag, utcnow
from app.services.history import add_tag, clean_tag, get_or_create_tag, record_revision
from app.services.processing import reindex_meaning
from app.services.search import index_entry, remove_from_index

router = APIRouter(prefix="/api", tags=["organize"])


class BulkAction(BaseModel):
    entry_ids: list[int] = Field(min_length=1, max_length=1000)
    action: Literal["add_to_project", "remove_from_project", "set_status", "add_tag", "trash"]
    project_id: int | None = None
    status: EntryStatus | None = None
    tag: str | None = Field(None, max_length=100)


@router.post("/entries/bulk")
def bulk(payload: BulkAction, db: Session = Depends(get_db)):
    entries = db.scalars(select(CreativeEntry).where(CreativeEntry.id.in_(payload.entry_ids),
                                                     CreativeEntry.deleted_at.is_(None))
                         .options(selectinload(CreativeEntry.entry_tags).selectinload(EntryTag.tag),
                                  selectinload(CreativeEntry.projects))).all()
    project = None
    if payload.action in ("add_to_project", "remove_from_project"):
        project = db.get(Project, payload.project_id) if payload.project_id else None
        if project is None or project.deleted_at is not None:
            raise HTTPException(400, "Choose a project.")
    if payload.action == "set_status" and payload.status is None:
        raise HTTPException(400, "Choose a status.")
    tag = clean_tag(payload.tag or "")
    if payload.action == "add_tag" and not tag:
        raise HTTPException(400, "Type a tag.")

    changed = []
    for e in entries:
        if payload.action == "add_to_project" and project not in e.projects:
            e.projects.append(project)
            note = f"Added to project “{project.name}”"
        elif payload.action == "remove_from_project" and project in e.projects:
            e.projects.remove(project)
            note = f"Removed from project “{project.name}”"
        elif payload.action == "set_status" and e.status != payload.status.value:
            e.status = payload.status.value
            note = f"Status set to {e.status}"
        elif payload.action == "add_tag" and not any(et.tag.name == tag for et in e.entry_tags):
            add_tag(db, e, tag, Origin.user.value)
            note = f"Tagged “{tag}”"
        elif payload.action == "trash":
            e.deleted_at = utcnow()
            record_revision(db, e, "Moved to trash (original material preserved)")
            remove_from_index(db, e.id)
            changed.append(e.id)
            continue
        else:
            continue
        e.updated_at = utcnow()
        record_revision(db, e, note)
        index_entry(db, e)
        changed.append(e.id)
    if project is not None and changed:
        project.updated_at = utcnow()
    db.commit()
    if payload.action == "add_tag":
        for eid in changed:
            reindex_meaning(eid)
    return {"changed": len(changed), "entry_ids": changed}


# -- tags ---------------------------------------------------------------------
class TagRename(BaseModel):
    new_name: str = Field(min_length=1, max_length=100)


def _tagged_entries(db: Session, tag: Tag) -> list[CreativeEntry]:
    return list(db.scalars(select(CreativeEntry).join(EntryTag, EntryTag.entry_id == CreativeEntry.id)
                           .where(EntryTag.tag_id == tag.id)
                           .options(selectinload(CreativeEntry.entry_tags).selectinload(EntryTag.tag),
                                    selectinload(CreativeEntry.projects))))


def _get_tag(db: Session, name: str) -> Tag:
    tag = db.scalar(select(Tag).where(Tag.name == clean_tag(name)))
    if tag is None:
        raise HTTPException(404, "Tag not found")
    return tag


@router.patch("/tags/{name}")
def rename_tag(name: str, payload: TagRename, db: Session = Depends(get_db)):
    """Rename a tag everywhere. If the new name already exists, the two tags are merged."""
    tag = _get_tag(db, name)
    new = clean_tag(payload.new_name)
    if not new:
        raise HTTPException(400, "Type a tag name.")
    if new == tag.name:
        return {"changed": 0, "merged": False}
    existing = db.scalar(select(Tag).where(Tag.name == new))  # renaming onto it merges the two
    target = existing
    entries = _tagged_entries(db, tag)
    for e in entries:
        old = next(et for et in e.entry_tags if et.tag_id == tag.id)
        e.entry_tags.remove(old)
        if not (target is not None and any(et.tag_id == target.id for et in e.entry_tags)):
            target = target or get_or_create_tag(db, new)
            e.entry_tags.append(EntryTag(tag=target, source=old.source))
        e.updated_at = utcnow()
        record_revision(db, e, f"Tag “{tag.name}” renamed to “{new}”")
        index_entry(db, e)
    db.flush()
    db.delete(tag)
    db.commit()
    for e in entries:
        reindex_meaning(e.id)
    return {"changed": len(entries), "merged": existing is not None}


@router.delete("/tags/{name}")
def delete_tag(name: str, db: Session = Depends(get_db)):
    """Remove a tag from every entry. AI tags removed this way are remembered, so the AI won't re-add them."""
    tag = _get_tag(db, name)
    entries = _tagged_entries(db, tag)
    for e in entries:
        et = next(x for x in e.entry_tags if x.tag_id == tag.id)
        if et.source == Origin.ai.value:
            meta = dict(e.ai_metadata or {})
            meta["rejected_tags"] = sorted(set(meta.get("rejected_tags", [])) | {tag.name})
            e.ai_metadata = meta
        e.entry_tags.remove(et)
        e.updated_at = utcnow()
        record_revision(db, e, f"Tag “{tag.name}” removed")
        index_entry(db, e)
    db.flush()
    db.delete(tag)
    db.commit()
    for e in entries:
        reindex_meaning(e.id)
    return {"changed": len(entries)}


# -- deleting forever (only from the trash) ----------------------------------------
def _erase_entry(db: Session, entry: CreativeEntry) -> None:
    """Delete an entry and everything derived from it, including its files on disk."""
    data_dir = get_settings().data_dir
    files = []
    for a in entry.attachments:
        files.append(data_dir / a.stored_path)
        if a.preview_path:
            files.append(data_dir / a.preview_path)
        files.extend((data_dir / "previews" / "thumbs").glob(f"{a.id}-*"))
    remove_from_index(db, entry.id)
    db.delete(entry)  # tags, history, links, art analyses and the meaning index go with it
    db.flush()
    for f in files:
        try:
            f.unlink(missing_ok=True)
        except OSError:
            pass


@router.delete("/trash/entries/{entry_id}")
def delete_entry_forever(entry_id: int, db: Session = Depends(get_db)):
    """Permanently delete an entry that is already in the trash (older backups may still contain it)."""
    entry = db.get(CreativeEntry, entry_id)
    if entry is None:
        raise HTTPException(404, "Entry not found")
    if entry.deleted_at is None:
        raise HTTPException(400, "Move it to the trash first; only entries in the trash can be deleted forever.")
    _erase_entry(db, entry)
    db.commit()
    return {"deleted": 1}


@router.delete("/trash/projects/{project_id}")
def delete_project_forever(project_id: int, db: Session = Depends(get_db)):
    """Permanently delete a trashed project. Its entries are never touched."""
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(404, "Project not found")
    if project.deleted_at is None:
        raise HTTPException(400, "Move it to the trash first.")
    db.delete(project)
    db.commit()
    return {"deleted": 1}


@router.post("/trash/empty")
def empty_trash(db: Session = Depends(get_db)):
    """Permanently delete everything in the trash."""
    entries = db.scalars(select(CreativeEntry).where(CreativeEntry.deleted_at.is_not(None))
                         .options(selectinload(CreativeEntry.attachments))).all()
    projects = db.scalars(select(Project).where(Project.deleted_at.is_not(None))).all()
    for e in entries:
        _erase_entry(db, e)
    for p in projects:
        db.delete(p)
    db.commit()
    return {"entries": len(entries), "projects": len(projects)}
