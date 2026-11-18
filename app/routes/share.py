"""Sharing with another person's NEXUS: export a .nexus file, import one. See services/share.py."""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from app.database import get_db
from app.services import share

router = APIRouter(prefix="/api/share", tags=["share"])


@router.get("/export")
def export_share(entry_ids: str | None = Query(None, description="Comma-separated entry ids"),
                 project_id: int | None = None, by: str = Query("", max_length=80),
                 db: Session = Depends(get_db)):
    """Download a .nexus file with these entries (or a whole project) for someone else's NEXUS."""
    try:
        ids = [int(x) for x in (entry_ids or "").split(",") if x.strip()]
    except ValueError as exc:
        raise HTTPException(422, "entry_ids must be numbers") from exc
    try:
        path, name = share.export_package(db, ids, project_id, by)
    except share.ShareError as exc:
        raise HTTPException(400, str(exc)) from exc
    return FileResponse(path, media_type="application/octet-stream", filename=name,
                        background=BackgroundTask(share.cleanup, path))


@router.post("/import")
def import_share(file: UploadFile = File(...), db: Session = Depends(get_db)):
    """Import a .nexus file from someone else's NEXUS. Importing the same file again adds nothing."""
    try:
        r = share.import_package_from_upload(db, file.file)
    except share.ShareError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"by": r.by, "imported": r.imported, "already_had": r.already_had,
            "project_id": r.project_id, "project_name": r.project_name}
