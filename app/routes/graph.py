"""The connections map: entries as nodes, relationships as edges."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.database import get_db
from app.models import CreativeEntry, Origin, ProjectEntry, Relationship, RelationshipStatus
from app.schemas import preview_url

router = APIRouter(prefix="/api", tags=["graph"])


def _thumb(e: CreativeEntry) -> str | None:
    pics = [a for a in e.attachments if a.kind == "image"] or list(e.attachments)
    for a in pics:
        if preview_url(a):
            return f"/api/uploads/{a.id}/thumb?v={a.sha256[:10]}"
    return None


@router.get("/graph")
def graph(project_id: int | None = None, include_suggested: bool = False, db: Session = Depends(get_db)):
    """Nodes and links for the connections map.

    Links are the ones you made or accepted; AI suggestions only with include_suggested=true
    (rejected and ignored ones never). With project_id, only that project's entries.
    """
    stmt = select(CreativeEntry).where(CreativeEntry.deleted_at.is_(None)).options(
        selectinload(CreativeEntry.attachments), selectinload(CreativeEntry.projects))
    if project_id is not None:
        stmt = stmt.where(CreativeEntry.id.in_(select(ProjectEntry.entry_id).where(ProjectEntry.project_id == project_id)))
    entries = db.scalars(stmt).all()
    ids = {e.id for e in entries}
    wanted = [RelationshipStatus.accepted.value] + ([RelationshipStatus.suggested.value] if include_suggested else [])
    # plain columns, filtered here: much faster than loading thousands of link objects
    rels = [r for r in db.execute(select(Relationship.id, Relationship.source_entry_id, Relationship.target_entry_id,
                                         Relationship.relationship_type, Relationship.origin, Relationship.status)
                                  .where(Relationship.status.in_(wanted)))
            if r.source_entry_id in ids and r.target_entry_id in ids]
    return {
        "nodes": [{"id": e.id, "title": e.title or "Untitled", "entry_type": e.entry_type, "status": e.status,
                   "thumb_url": _thumb(e), "project_ids": [p.id for p in e.projects if p.deleted_at is None],
                   "shared_by": (e.shared_from or {}).get("by")} for e in entries],
        "edges": [{"id": r.id, "source": r.source_entry_id, "target": r.target_entry_id, "type": r.relationship_type,
                   "origin": r.origin, "status": r.status,
                   "yours": r.origin == Origin.user.value or r.status == RelationshipStatus.accepted.value}
                  for r in rels],
    }
