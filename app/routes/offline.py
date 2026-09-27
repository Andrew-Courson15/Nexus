"""The phone's offline copy: everything needed to *read* the archive while NEXUS is unreachable.

The phone downloads this snapshot while the PC is on and keeps it on the device, so
when the PC is off it can still show entries, projects, relationships and art
analyses (read-only). Captures made meanwhile go to the phone's outbox as before.
Pictures come separately as small thumbnails (/api/uploads/{id}/thumb).
"""
from __future__ import annotations

import gzip
import json
from collections import defaultdict

from fastapi import APIRouter, Depends, Request, Response
from fastapi.encoders import jsonable_encoder
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.database import get_db
from app.models import ArtworkAnalysis, CreativeEntry, Relationship, RelationshipStatus, utcnow
from app.routes import art as art_routes
from app.routes.entries import _LOAD
from app.routes.projects import list_projects
from app.schemas import entry_detail, relationship_out

router = APIRouter(prefix="/api/offline", tags=["offline"])


def build_snapshot(db: Session) -> dict:
    entries = db.scalars(select(CreativeEntry).where(CreativeEntry.deleted_at.is_(None)).options(*_LOAD)
                         .order_by(CreativeEntry.created_at.desc(), CreativeEntry.id.desc())).all()
    alive = {e.id for e in entries}

    relationships: dict[int, list] = defaultdict(list)
    rels = db.scalars(select(Relationship).where(Relationship.status != RelationshipStatus.rejected.value)
                      .options(selectinload(Relationship.source_entry), selectinload(Relationship.target_entry))
                      .order_by(Relationship.confidence.desc().nullslast(), Relationship.id)).all()
    for r in rels:
        if r.source_entry_id in alive and r.target_entry_id in alive:
            relationships[r.source_entry_id].append(relationship_out(r, r.source_entry_id))
            relationships[r.target_entry_id].append(relationship_out(r, r.target_entry_id))

    art: dict[int, list] = defaultdict(list)
    for a in db.scalars(select(ArtworkAnalysis).where(ArtworkAnalysis.entry_id.in_(alive))
                        .order_by(ArtworkAnalysis.analysis_version.desc())):
        art[a.entry_id].append(art_routes._out(db, a))

    return {
        "generated_at": utcnow(),
        "entries": [entry_detail(e) for e in entries],
        "relationships": relationships,
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
