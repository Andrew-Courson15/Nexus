"""The phone's offline copy: everything needed to *read* the archive while NEXUS is unreachable.

The phone downloads this snapshot while the PC is on and keeps it on the device, so
when the PC is off it can still show entries, projects, relationships and art
analyses (read-only). Captures made meanwhile go to the phone's outbox as before.
Pictures come separately as small thumbnails (/api/uploads/{id}/thumb).
"""
from __future__ import annotations

import gzip
import json
import logging
from collections import defaultdict

from fastapi import APIRouter, Depends, Request, Response
from fastapi.encoders import jsonable_encoder
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import ArtworkAnalysis, CreativeEntry, ProjectEntry, Relationship, RelationshipStatus, utcnow
from app.routes import art as art_routes
from app.routes.entries import _LOAD
from app.routes.projects import list_projects
from app.schemas import entry_detail

router = APIRouter(prefix="/api/offline", tags=["offline"])
log = logging.getLogger("nexus.offline")


def _each(items, convert, what: str) -> list:
    """Convert every item; one that can't be converted is skipped (and logged), never the whole copy."""
    out = []
    for item in items:
        try:
            out.append(convert(item))
        except Exception:
            log.exception("Left %s %s out of the phone's offline copy", what, getattr(item, "id", "?"))
    return out


def build_snapshot(db: Session) -> dict:
    entries = db.scalars(select(CreativeEntry).where(CreativeEntry.deleted_at.is_(None)).options(*_LOAD)
                         .order_by(CreativeEntry.created_at.desc(), CreativeEntry.id.desc())).all()
    alive = {e.id for e in entries}

    # Each link once, compactly: [id, source, target, type, origin, status, confidence, rationale].
    # The phone rebuilds what the entry page shows (direction, the other entry, the label) from these.
    edges = [[r.id, r.source_entry_id, r.target_entry_id, r.relationship_type, r.origin, r.status,
              r.confidence, r.rationale]
             for r in db.scalars(select(Relationship).where(Relationship.status != RelationshipStatus.rejected.value)
                                 .order_by(Relationship.confidence.desc().nullslast(), Relationship.id))
             if r.source_entry_id in alive and r.target_entry_id in alive]

    art: dict[int, list] = defaultdict(list)
    for a in db.scalars(select(ArtworkAnalysis).where(ArtworkAnalysis.entry_id.in_(alive))
                        .order_by(ArtworkAnalysis.analysis_version.desc())):
        art[a.entry_id].extend(_each([a], lambda x: art_routes._out(db, x), "art analysis"))

    def lean(e):  # the provenance legend is the same for every entry; the app has its own copy
        return entry_detail(e).model_dump(mode="json", exclude={"provenance"})

    orders: dict[int, list[int]] = defaultdict(list)  # projects you've arranged: entry ids in your order
    for pe in db.scalars(select(ProjectEntry).where(ProjectEntry.position.is_not(None))
                         .order_by(ProjectEntry.project_id, ProjectEntry.position)):
        if pe.entry_id in alive:
            orders[pe.project_id].append(pe.entry_id)

    return {
        "format": 2,
        "orders": orders,
        "generated_at": utcnow(),
        "entries": _each(entries, lean, "entry"),
        "edges": edges,
        "art": art,
        "projects": list_projects(False, db),
    }


@router.get("/snapshot")
def snapshot(request: Request, db: Session = Depends(get_db)):
    body = json.dumps(jsonable_encoder(build_snapshot(db)), separators=(",", ":")).encode()
    headers = {"Cache-Control": "no-store"}
    if "gzip" in request.headers.get("accept-encoding", ""):
        body = gzip.compress(body, compresslevel=6)
        headers["Content-Encoding"] = "gzip"
    return Response(body, media_type="application/json", headers=headers)
