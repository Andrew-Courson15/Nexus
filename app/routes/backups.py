"""Backups: list, start, download. See app/services/backup.py."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import FileResponse

from app.config import get_settings
from app.services import backup

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
    }


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
