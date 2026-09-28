"""Backups: list, start, download, and restore from a file. See app/services/backup.py."""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models import CreativeEntry
from app.services import backup, lifecycle

router = APIRouter(prefix="/api/backups", tags=["backups"])


@router.get("")
def list_backups():
    s = get_settings()
    return {
        "backups": backup.list_backups(),
        "running": backup.runner.running,
        "last_error": backup.runner.last_error,
        "folder": str(s.backups_dir),
        "every_days": s.backup_every_days,
        "keep": s.backup_keep,
        "folder_is_inside_data": s.backups_dir.resolve().is_relative_to(s.data_dir.resolve()),
        "restore_pending": backup.pending_restore(),
        "last_restore": backup.last_restore(),
        "can_restart": lifecycle.can_restart(),
    }


def _only_here(request: Request) -> None:
    from app.services import lock

    if not lock.is_local(request.client.host if request.client else None, request.headers):
        raise HTTPException(403, "Restoring a backup is done on the computer NEXUS runs on.")


@router.post("/restore")
def stage_restore(request: Request, file: UploadFile = File(...)):
    """Check a backup zip and restore it the next time NEXUS starts (the current data is moved
    aside, never deleted). On the computer NEXUS runs on only."""
    _only_here(request)
    try:
        info = backup.stage_restore(file.file, file.filename or "")
    except backup.BackupError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"restore_pending": info}


@router.get("/found")
def found_archives(request: Request, db: Session = Depends(get_db)):
    """NEXUS folders on this computer with an archive (the downloadable app offers to bring one over).
    Only while this archive is still empty: afterwards the old folder is still there, and bringing it
    again would replace everything captured here since (use a backup file for that instead)."""
    _only_here(request)
    if db.scalar(select(func.count()).select_from(CreativeEntry)):
        return {"archives": []}
    return {"archives": backup.found_archives()}


class _Bring(BaseModel):
    folder: str


@router.post("/found")
def bring_archive(body: _Bring, request: Request, db: Session = Depends(get_db)):
    """Copy the archive from a NEXUS folder, to be restored the next time NEXUS starts."""
    _only_here(request)
    if db.scalar(select(func.count()).select_from(CreativeEntry)):
        raise HTTPException(400, "This NEXUS already has ideas in it. To replace them, use Restore from a backup file.")
    try:
        info = backup.bring_archive(body.folder)
    except backup.BackupError as exc:
        raise HTTPException(400, str(exc)) from exc
    _take_over_autostart()
    return {"restore_pending": info}


def _take_over_autostart() -> None:
    """If the folder's NEXUS started at sign-in, this app does from now on (same shortcut / login item)."""
    from app.config import FROZEN
    from app.services import autostart

    try:
        if FROZEN and autostart.supported() and autostart.enabled():
            autostart.set_enabled(True)
    except Exception:  # a nicety; the setting is also in Settings → This computer
        pass


@router.delete("/restore")
def cancel_restore(request: Request):
    _only_here(request)
    backup.cancel_restore()
    return {"restore_pending": None}


@router.delete("/last-restore")
def forget_last_restore(request: Request):
    _only_here(request)
    backup.forget_last_restore()
    return {"last_restore": None}


@router.post("", status_code=202)
def start_backup(response: Response):
    """Start a backup in the background (the list shows it when it's done)."""
    wait = get_settings().processing_mode == "sync"  # tests: finish before answering
    started = backup.runner.start_now(wait=wait)
    if wait:
        response.status_code = 200
    return {"started": started, "running": backup.runner.running, "last_error": backup.runner.last_error}


@router.get("/{name}")
def download_backup(name: str):
    try:
        path = backup.backup_path(name)
    except backup.BackupError as exc:
        raise HTTPException(404, str(exc)) from exc
    return FileResponse(path, media_type="application/zip", filename=name)
