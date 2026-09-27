"""Backups: one zip with the database and every original file.

A backup holds everything that can't be rebuilt:
    nexus.db        a consistent copy of the database (SQLite's online backup, safe while NEXUS runs)
    uploads/ audio/ images/   every original file exactly as it was uploaded
    backup.json     what's inside, and when it was made
Previews, thumbnails and the search index are left out: NEXUS rebuilds them.

Backups are written to NEXUS_BACKUP_DIR (default data/backups). Put that on another drive or
in a synced cloud folder so a failing disk can't take the backups with it. An automatic backup
runs every NEXUS_BACKUP_EVERY_DAYS days, and only the newest NEXUS_BACKUP_KEEP are kept.

Restore with:  python -m app.services.backup restore <file.zip>   (while NEXUS is closed)
"""
from __future__ import annotations

import json
import logging
import re
import shutil
import sqlite3
import sys
import tempfile
import threading
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.config import get_settings

log = logging.getLogger("nexus.backup")

NAME_RE = re.compile(r"^nexus-backup-\d{8}-\d{6}(-[a-z]+)?\.zip$")
ORIGINAL_DIRS = ("uploads", "audio", "images")
CHECK_EVERY_S = 6 * 3600


class BackupError(RuntimeError):
    pass


def _db_path() -> Path:
    url = get_settings().database_url
    if not url.startswith("sqlite:///"):
        raise BackupError("Backups currently support the built-in SQLite database only.")
    return Path(url.removeprefix("sqlite:///"))


def list_backups() -> list[dict]:
    folder = get_settings().backups_dir
    if not folder.is_dir():
        return []
    out = []
    for f in folder.iterdir():
        if f.is_file() and NAME_RE.match(f.name):
            st = f.stat()
            out.append({"name": f.name, "size_bytes": st.st_size,
                        "created_at": datetime.fromtimestamp(st.st_mtime, timezone.utc),
                        "automatic": f.stem.endswith("-auto")})
    return sorted(out, key=lambda b: b["name"], reverse=True)


def backup_path(name: str) -> Path:
    """The file for a backup name from list_backups(); refuses anything else."""
    if not NAME_RE.match(name):
        raise BackupError("Not a NEXUS backup name.")
    path = get_settings().backups_dir / name
    if not path.is_file():
        raise BackupError("That backup doesn't exist.")
    return path


def create_backup(automatic: bool = False) -> Path:
    """Write a new backup zip and prune old ones. Returns the new file."""
    s = get_settings()
    folder = s.backups_dir
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = folder / f"nexus-backup-{stamp}{'-auto' if automatic else ''}.zip"
    partial = dest.with_suffix(".zip.partial")
    counts = {"files": 0, "bytes": 0}
    with tempfile.TemporaryDirectory() as tmp:
        db_copy = Path(tmp) / "nexus.db"
        src = sqlite3.connect(_db_path())
        try:
            dst = sqlite3.connect(db_copy)
            with dst:
                src.backup(dst)  # consistent even while NEXUS is writing
            dst.close()
        finally:
            src.close()
        try:
            with zipfile.ZipFile(partial, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
                zf.write(db_copy, "nexus.db")
                for sub in ORIGINAL_DIRS:
                    base = s.data_dir / sub
                    if not base.is_dir():
                        continue
                    for f in sorted(base.rglob("*")):
                        if f.is_file() and f.name != ".gitkeep":
                            # pictures and audio are already compressed: store them as they are
                            zf.write(f, f"{sub}/{f.relative_to(base).as_posix()}", compress_type=zipfile.ZIP_STORED)
                            counts["files"] += 1
                            counts["bytes"] += f.stat().st_size
                zf.writestr("backup.json", json.dumps({
                    "app": "NEXUS", "format": 1, "created_at": datetime.now(timezone.utc).isoformat(),
                    "automatic": automatic, "original_files": counts["files"], "original_bytes": counts["bytes"],
                    "restore": "python -m app.services.backup restore <this file>  (with NEXUS closed)",
                }, indent=2))
            partial.replace(dest)  # a half-written backup never looks like a real one
        except BaseException:
            partial.unlink(missing_ok=True)
            raise
    _prune(folder, s.backup_keep)
    log.info("Backup written: %s", dest)
    return dest


def _prune(folder: Path, keep: int) -> None:
    for old in list_backups()[keep:]:
        (folder / old["name"]).unlink(missing_ok=True)


# -- running backups without blocking requests ---------------------------------
class BackupRunner:
    """One backup at a time, in a background thread; plus the automatic schedule."""

    def __init__(self):
        self._lock = threading.Lock()
        self.running = False
        self.last_error: str | None = None
        self._stop = threading.Event()
        self._timer: threading.Thread | None = None

    def start_now(self, automatic: bool = False, wait: bool = False) -> bool:
        """Start a backup. False if one is already running."""
        with self._lock:
            if self.running:
                return False
            self.running = True
        t = threading.Thread(target=self._run, args=(automatic,), name="nexus-backup", daemon=True)
        t.start()
        if wait:
            t.join()
        return True

    def _run(self, automatic: bool) -> None:
        try:
            create_backup(automatic)
            self.last_error = None
        except Exception as exc:
            log.exception("Backup failed")
            self.last_error = str(exc)
        finally:
            with self._lock:
                self.running = False

    def due(self) -> bool:
        days = get_settings().backup_every_days
        if days <= 0:
            return False
        latest = list_backups()[:1]
        return not latest or datetime.now(timezone.utc) - latest[0]["created_at"] > timedelta(days=days)

    def start_schedule(self) -> None:
        if self._timer and self._timer.is_alive():
            return
        self._stop.clear()

        def loop():
            if self._stop.wait(120):  # let NEXUS finish starting up first
                return
            while True:
                try:
                    if self.due():
                        self.start_now(automatic=True)
                except Exception:
                    log.exception("Automatic backup check failed")
                if self._stop.wait(CHECK_EVERY_S):
                    return

        self._timer = threading.Thread(target=loop, name="nexus-backup-schedule", daemon=True)
        self._timer.start()

    def stop(self) -> None:
        self._stop.set()


runner = BackupRunner()


# -- restore (command line, with NEXUS closed) ---------------------------------
def restore(zip_path: Path) -> Path:
    """Replace the current data with a backup. The current data is moved aside, never deleted.

    Returns the folder the previous data was moved to.
    """
    s = get_settings()
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        if "nexus.db" not in names or "backup.json" not in names:
            raise BackupError(f"{zip_path.name} isn't a NEXUS backup.")
        for n in names:
            if n.startswith("/") or ".." in Path(n).parts:
                raise BackupError("The backup contains unsafe paths; refusing to restore it.")
        db = _db_path()
        aside = s.data_dir.parent / f"{s.data_dir.name}-before-restore-{datetime.now():%Y%m%d-%H%M%S}"
        aside.mkdir(parents=True)
        for item in [db, db.with_name(db.name + "-wal"), db.with_name(db.name + "-shm"),
                     *(s.data_dir / d for d in (*ORIGINAL_DIRS, "previews"))]:
            if item.exists():
                shutil.move(str(item), str(aside / item.name))
        for n in names:
            if n == "backup.json" or n.endswith("/"):
                continue
            target = db if n == "nexus.db" else s.data_dir / n
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(n) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out)
    return aside


def _nexus_running() -> bool:
    import socket

    s = get_settings()
    try:
        with socket.create_connection(("127.0.0.1", s.port), timeout=1):
            return True
    except OSError:
        return False


def main(argv: list[str]) -> int:
    if len(argv) >= 1 and argv[0] == "create":
        print(f"Backup written: {create_backup()}")
        return 0
    if len(argv) == 2 and argv[0] == "restore":
        if _nexus_running():
            print("Close NEXUS first (it's still running), then run this again.")
            return 1
        path = Path(argv[1]).expanduser()
        if not path.is_file():
            print(f"No such file: {path}")
            return 1
        aside = restore(path)
        print(f"Restored {path.name}.\nYour previous data was moved to: {aside}\nStart NEXUS as usual.")
        return 0
    print("Usage:\n  python -m app.services.backup create\n  python -m app.services.backup restore <backup.zip>")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
