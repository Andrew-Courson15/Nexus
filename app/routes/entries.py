"""Creative entry endpoints."""
from __future__ import annotations

from datetime import date, datetime, time, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.database import get_db
from app.models import (
    CreativeEntry,
    CreativeRevision,
    EntryTag,
    Origin,
    Project,
    ProjectEntry,
    Relationship,
    RelationshipStatus,
    Tag,
    utcnow,
)
from app.schemas import (
    EntryCreate,
    EntryDetail,
    EntryList,
    EntryUpdate,
    RelationshipCreate,
    RelationshipOut,
    RelationshipReview,
    RevisionOut,
    entry_detail,
    entry_summary,
    relationship_out,
)
from app.services.history import add_tag, record_revision, restore_snapshot, set_user_tags
from app.services.processing import enqueue, reindex_meaning
from app.services.relationships import relationships_for
from app.services.search import index_entry, remove_from_index

router = APIRouter(prefix="/api", tags=["entries"])

_LOAD = (
    selectinload(CreativeEntry.entry_tags).selectinload(EntryTag.tag),
    selectinload(CreativeEntry.projects),
    selectinload(CreativeEntry.attachments),
    selectinload(CreativeEntry.revisions),
)


def load_entry(db: Session, entry_id: int, include_deleted: bool = False) -> CreativeEntry:
    entry = db.scalar(select(CreativeEntry).where(CreativeEntry.id == entry_id).options(*_LOAD))
    if entry is None or (entry.deleted_at is not None and not include_deleted):
        raise HTTPException(404, "Entry not found")
    return entry


def existing_capture(db: Session, client_id: str | None) -> CreativeEntry | None:
    """A capture already received with this device-made id (a re-send after a dropped connection)."""
    if not client_id:
        return None
    entry_id = db.scalar(select(CreativeEntry.id).where(CreativeEntry.client_id == client_id))
    return load_entry(db, entry_id, include_deleted=True) if entry_id else None


def capture_time(captured_at: datetime | None) -> datetime:
    """When the idea was captured: the device's time if given (never in the future), else now."""
    now = utcnow()
    if captured_at is None:
        return now
    if captured_at.tzinfo is None:
        captured_at = captured_at.replace(tzinfo=timezone.utc)
    return min(captured_at, now)


def _split(values: list[str] | None) -> list[str]:
    out: list[str] = []
    for v in values or []:
        out += [p.strip() for p in v.split(",") if p.strip()]
    return out


@router.post("/entries", response_model=EntryDetail, status_code=201)
def create_entry(payload: EntryCreate, response: Response, db: Session = Depends(get_db)):
    if (dup := existing_capture(db, payload.client_id)) is not None:
        response.status_code = 200  # already have it: sending twice never creates a duplicate
        return entry_detail(dup)
    created = capture_time(payload.captured_at)
    entry = CreativeEntry(
        original_text=payload.original_text,  # RAW — stored exactly as submitted
        status=payload.status.value,
        source_type=payload.source_type,
        ai_metadata={},
        client_id=payload.client_id,
        created_at=created,
        updated_at=created,
    )
    if payload.title and payload.title.strip():
        entry.title, entry.title_source = payload.title.strip(), Origin.user.value
    if payload.entry_type:
        entry.entry_type, entry.entry_type_source = payload.entry_type.value, Origin.user.value
    for pid in dict.fromkeys(payload.project_ids):
        project = db.get(Project, pid)
        if project is None:
            raise HTTPException(400, f"Project {pid} does not exist")
        entry.projects.append(project)
    db.add(entry)
    try:
        db.flush()
    except IntegrityError:  # the same capture arrived twice at the same moment
        db.rollback()
        response.status_code = 200
        return entry_detail(existing_capture(db, payload.client_id))
    for t in payload.tags:
        add_tag(db, entry, t, Origin.user.value)
    record_revision(db, entry, "Captured original material")
    index_entry(db, entry)
    db.commit()  # the original is safe before any AI work happens

    if payload.analyze:
        enqueue(db, entry, transcribe=False)
    return entry_detail(load_entry(db, entry.id))


@router.get("/entries", response_model=EntryList)
def list_entries(
    entry_type: list[str] | None = Query(None, description="Comma-separated or repeated"),
    status: list[str] | None = Query(None),
    source_type: list[str] | None = Query(None),
    project_id: int | None = None,
    tag: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    sort: Literal["newest", "oldest", "modified", "alpha"] = "newest",
    limit: int = Query(60, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    stmt = select(CreativeEntry).where(CreativeEntry.deleted_at.is_(None))
    if types := _split(entry_type):
        stmt = stmt.where(CreativeEntry.entry_type.in_(types))
    if statuses := _split(status):
        stmt = stmt.where(CreativeEntry.status.in_(statuses))
    if sources := _split(source_type):
        stmt = stmt.where(CreativeEntry.source_type.in_(sources))
    if project_id is not None:
        stmt = stmt.where(CreativeEntry.id.in_(
            select(ProjectEntry.entry_id).where(ProjectEntry.project_id == project_id)))
    if tag:
        stmt = stmt.where(CreativeEntry.id.in_(
            select(EntryTag.entry_id).join(Tag).where(Tag.name == tag.strip().lower())))
    if date_from:
        stmt = stmt.where(CreativeEntry.created_at >= datetime.combine(date_from, time.min, timezone.utc))
    if date_to:
        stmt = stmt.where(CreativeEntry.created_at <= datetime.combine(date_to, time.max, timezone.utc))

    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    order = {
        "newest": (CreativeEntry.created_at.desc(), CreativeEntry.id.desc()),
        "oldest": (CreativeEntry.created_at.asc(), CreativeEntry.id.asc()),
        "modified": (CreativeEntry.updated_at.desc(), CreativeEntry.id.desc()),
        "alpha": (func.lower(CreativeEntry.title).asc(), CreativeEntry.id.asc()),
    }[sort]
    rows = db.scalars(stmt.options(*_LOAD[:3]).order_by(*order).limit(limit).offset(offset)).all()
    return EntryList(items=[entry_summary(e) for e in rows], total=total or 0)


@router.get("/entries/{entry_id}", response_model=EntryDetail)
def get_entry(entry_id: int, db: Session = Depends(get_db)):
    return entry_detail(load_entry(db, entry_id))


@router.put("/entries/{entry_id}", response_model=EntryDetail)
def update_entry(entry_id: int, payload: EntryUpdate, db: Session = Depends(get_db)):
    entry = load_entry(db, entry_id)
    data = payload.model_dump(exclude_unset=True)
    changes: list[str] = []

    if "title" in data:
        new = (data["title"] or "").strip()
        if new:
            if new != entry.title or entry.title_source != Origin.user.value:
                entry.title, entry.title_source = new, Origin.user.value
                changes.append("title")
        elif entry.title_source == Origin.user.value:
            # Clearing a user title hands the title back to the AI.
            entry.title = (entry.ai_metadata or {}).get("suggested_title", "")
            entry.title_source = Origin.ai.value
            changes.append("title reset to AI suggestion")
    if data.get("entry_type") is not None:
        entry.entry_type, entry.entry_type_source = payload.entry_type.value, Origin.user.value
        changes.append("type")
    if data.get("status") is not None and payload.status.value != entry.status:
        entry.status = payload.status.value
        changes.append("status")
    if "user_notes" in data and data["user_notes"] != entry.user_notes:
        entry.user_notes = data["user_notes"]
        changes.append("notes")
    if "transcript" in data and data["transcript"] != entry.transcript:
        entry.transcript = data["transcript"]
        meta = dict(entry.ai_metadata or {})
        meta["transcript_source"] = "user"
        entry.ai_metadata = meta
        changes.append("transcript corrected")
    if data.get("tags") is not None:
        set_user_tags(db, entry, data["tags"])
        changes.append("tags")
    if data.get("user_confirmed") is not None and data["user_confirmed"] != entry.user_confirmed:
        entry.user_confirmed = data["user_confirmed"]
        if entry.user_confirmed:
            # Confirming locks the current title/type as user-approved.
            entry.title_source = entry.entry_type_source = Origin.user.value
        changes.append("confirmed by user" if entry.user_confirmed else "confirmation removed")

    if changes:
        entry.updated_at = utcnow()
        record_revision(db, entry, data.get("change_description") or ("Edited: " + ", ".join(changes)))
        index_entry(db, entry)
        db.commit()
        reindex_meaning(entry.id)
    return entry_detail(load_entry(db, entry_id))


@router.delete("/entries/{entry_id}", status_code=204)
def delete_entry(entry_id: int, db: Session = Depends(get_db)):
    """Soft delete: hides the entry. Original material and files are kept."""
    entry = load_entry(db, entry_id)
    entry.deleted_at = utcnow()
    record_revision(db, entry, "Moved to trash (original material preserved)")
    remove_from_index(db, entry.id)
    db.commit()
    return Response(status_code=204)


@router.post("/entries/{entry_id}/restore", response_model=EntryDetail)
def restore_entry(entry_id: int, db: Session = Depends(get_db)):
    entry = load_entry(db, entry_id, include_deleted=True)
    if entry.deleted_at is not None:
        entry.deleted_at = None
        record_revision(db, entry, "Restored from trash")
        index_entry(db, entry)
        db.commit()
        reindex_meaning(entry.id)
    return entry_detail(load_entry(db, entry_id))


@router.post("/entries/{entry_id}/analyze", response_model=EntryDetail)
def analyze_entry(entry_id: int, response: Response, db: Session = Depends(get_db)):
    """Re-run the AI organizer. Queued in the background unless processing mode is sync."""
    entry = load_entry(db, entry_id)
    enqueue(db, entry, transcribe=False)
    entry = load_entry(db, entry_id)
    if entry.processing_status == "failed":
        raise HTTPException(502, (entry.ai_metadata or {}).get("analysis_error") or "AI analysis failed")
    if entry.processing_status in ("queued", "analyzing"):
        response.status_code = 202
    return entry_detail(entry)


@router.post("/entries/{entry_id}/transcribe", response_model=EntryDetail)
def transcribe_entry(entry_id: int, response: Response, force: bool = False, db: Session = Depends(get_db)):
    """(Re)transcribe a voice entry, then re-run the organizer on the new transcript.

    A transcript you corrected by hand is only replaced with `force=true`
    (the corrected version stays in the revision history either way).
    """
    entry = load_entry(db, entry_id)
    if entry.source_type != "voice" or not any(a.kind == "audio" for a in entry.attachments):
        raise HTTPException(400, "This entry has no recording to transcribe")
    if entry.transcript and (entry.ai_metadata or {}).get("transcript_source") == "user" and not force:
        raise HTTPException(409, "You corrected this transcript by hand. Use force=true to replace it.")
    enqueue(db, entry, force_transcribe=True)
    entry = load_entry(db, entry_id)
    if entry.processing_status in ("queued", "transcribing", "analyzing"):
        response.status_code = 202
    return entry_detail(entry)


@router.get("/entries/{entry_id}/revisions", response_model=list[RevisionOut])
def list_revisions(entry_id: int, db: Session = Depends(get_db)):
    load_entry(db, entry_id, include_deleted=True)
    revs = db.scalars(
        select(CreativeRevision).where(CreativeRevision.entry_id == entry_id)
        .order_by(CreativeRevision.version_number.desc())
    ).all()
    return [RevisionOut.model_validate(r, from_attributes=True) for r in revs]


@router.post("/entries/{entry_id}/revisions/{version}/restore", response_model=EntryDetail)
def restore_revision(entry_id: int, version: int, db: Session = Depends(get_db)):
    """Bring the entry back to an earlier version. This adds a new revision; nothing is erased."""
    entry = load_entry(db, entry_id)
    rev = db.scalar(select(CreativeRevision).where(
        CreativeRevision.entry_id == entry_id, CreativeRevision.version_number == version))
    if rev is None:
        raise HTTPException(404, "Version not found")
    changed = restore_snapshot(db, entry, rev.content_snapshot or {})
    if changed:
        entry.updated_at = utcnow()
        record_revision(db, entry, f"Restored version {version}")
        index_entry(db, entry)
        db.commit()
        reindex_meaning(entry.id)
    return entry_detail(load_entry(db, entry_id))


@router.get("/trash", response_model=EntryList)
def list_trash(db: Session = Depends(get_db)):
    """Entries moved to the trash. Nothing here is destroyed; restore brings it back."""
    rows = db.scalars(
        select(CreativeEntry).where(CreativeEntry.deleted_at.is_not(None)).options(*_LOAD[:3])
        .order_by(CreativeEntry.deleted_at.desc())
    ).all()
    return EntryList(items=[entry_summary(e) for e in rows], total=len(rows))


@router.get("/entries/{entry_id}/relationships", response_model=list[RelationshipOut])
def list_relationships(entry_id: int, include_rejected: bool = False, db: Session = Depends(get_db)):
    load_entry(db, entry_id)
    return [relationship_out(r, entry_id) for r in relationships_for(db, entry_id, include_rejected)]


@router.post("/entries/{entry_id}/relationships", response_model=RelationshipOut, status_code=201)
def create_relationship(entry_id: int, payload: RelationshipCreate, db: Session = Depends(get_db)):
    """A relationship the user states directly (a fact, not a suggestion)."""
    load_entry(db, entry_id)
    if payload.target_entry_id == entry_id:
        raise HTTPException(400, "An entry cannot relate to itself")
    load_entry(db, payload.target_entry_id)
    rel = db.scalar(select(Relationship).where(
        Relationship.source_entry_id == entry_id,
        Relationship.target_entry_id == payload.target_entry_id,
        Relationship.relationship_type == payload.relationship_type.value,
    ))
    if rel is None:
        rel = Relationship(source_entry_id=entry_id, target_entry_id=payload.target_entry_id,
                           relationship_type=payload.relationship_type.value)
        db.add(rel)
    rel.origin, rel.status, rel.confidence = Origin.user.value, RelationshipStatus.accepted.value, 1.0
    db.commit()
    db.refresh(rel)
    return relationship_out(rel, entry_id)


@router.patch("/relationships/{relationship_id}", response_model=RelationshipOut)
def review_relationship(relationship_id: int, payload: RelationshipReview, db: Session = Depends(get_db)):
    """Accept, reject or ignore an AI-suggested relationship."""
    rel = db.get(Relationship, relationship_id)
    if rel is None:
        raise HTTPException(404, "Relationship not found")
    rel.status = payload.status.value
    db.commit()
    db.refresh(rel)
    return relationship_out(rel, rel.source_entry_id)


@router.get("/entries/{entry_id}/similar")
def similar(entry_id: int, db: Session = Depends(get_db)):
    """More like this: other entries closest in meaning, from the meaning index NEXUS already keeps
    (no AI call needed, so it also works while the AI is busy or off)."""
    from app.models import EntryEmbedding
    from app.services import embeddings

    load_entry(db, entry_id)
    provider = embeddings.get_embedding_provider()
    row = db.get(EntryEmbedding, entry_id)
    if provider.name == "none" or row is None or row.model != provider.model:
        return {"available": False, "items": [],
                "reason": "Search by meaning isn't set up, or this entry isn't in the meaning index yet."}
    live = set(db.scalars(select(CreativeEntry.id).where(CreativeEntry.deleted_at.is_(None), CreativeEntry.id != entry_id)))
    scored = embeddings.nearest(db, list(embeddings.unpack(row.vector)), row.model, live, limit=None)
    best = embeddings.confident_matches(scored)[:8]
    found = {e.id: e for e in db.scalars(select(CreativeEntry).where(CreativeEntry.id.in_([i for i, _ in best])).options(*_LOAD))}
    return {"available": True, "items": [{"entry": entry_summary(found[i]).model_dump(mode="json"), "similarity": round(sim, 3)}
                                         for i, sim in best if i in found]}


@router.post("/entries/{entry_id}/develop")
async def develop_idea(entry_id: int, db: Session = Depends(get_db)):
    """Directions, questions and a next step for this idea, from the local AI (kept with the entry)."""
    from fastapi.concurrency import run_in_threadpool

    from app.services import develop

    entry = load_entry(db, entry_id)
    try:
        return await run_in_threadpool(develop.develop, db, entry)
    except develop.DevelopError as exc:
        raise HTTPException(400, str(exc)) from exc
