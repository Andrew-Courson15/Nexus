"""Backups: one zip with the database and every original file.

A backup holds everything that can't be rebuilt:
    nexus.db        a consistent copy of the database (SQLite's online backup, safe while NEXUS runs)
    uploads/ audio/ images/   every original file exactly as it was uploaded
    backup.json     what's inside, and when it was made
Previews, thumbnails and the search index are left out: NEXUS rebuilds them.

Backups are written to NEXUS_BACKUP_DIR (default data/backups). Put that on another drive or
in a synced cloud folder so a failing disk can't take the backups with it. An automatic backup
runs every NEXUS_BACKUP_EVERY_DAYS days, and only the newest NEXUS_BACKUP_KEEP are kept.

Restore from Settings → Backups → "Restore from a backup file" (it happens when NEXUS next starts), or
with:  python -m app.services.backup restore <file.zip>   (while NEXUS is closed)
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
import time
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.config import FROZEN, get_settings

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
    _write_zip(dest, _db_path(), s.data_dir, automatic)
    _prune(folder, s.backup_keep)
    log.info("Backup written: %s", dest)
    return dest


def _write_zip(dest: Path, db_path: Path, data_dir: Path, automatic: bool = False) -> None:
    """A backup of the archive whose database is db_path and whose files are in data_dir."""
    partial = dest.with_suffix(".zip.partial")
    counts = {"files": 0, "bytes": 0}
    with tempfile.TemporaryDirectory() as tmp:
        db_copy = Path(tmp) / "nexus.db"
        src = sqlite3.connect(db_path)
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
                    base = data_dir / sub
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
                    "restore": "Settings → Backups → Restore from a backup file, or: python -m app.services.backup restore <this file>",
                }, indent=2))
            partial.replace(dest)  # a half-written backup never looks like a real one
        except BaseException:
            partial.unlink(missing_ok=True)
            raise


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


# -- restore ---------------------------------------------------------------
# From the command line with NEXUS closed, or from Settings: the file is checked and put aside
# ("staged"), and restored the next time NEXUS starts, before the database is opened.
SQLITE_MAGIC = b"SQLite format 3\x00"


def check_backup(zip_path: Path) -> dict:
    """Is this a NEXUS backup that can be restored? Returns what backup.json says about it."""
    try:
        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()
            if "nexus.db" not in names or "backup.json" not in names:
                raise BackupError("That isn't a NEXUS backup (it needs nexus.db and backup.json inside).")
            for n in names:
                if n.startswith(("/", "\\")) or ".." in Path(n).parts or re.match(r"^[A-Za-z]:", n):
                    raise BackupError("The backup contains unsafe paths; refusing to restore it.")
                top = n.split("/", 1)[0]
                if n not in ("nexus.db", "backup.json") and top not in ORIGINAL_DIRS:
                    raise BackupError(f"The backup contains something unexpected ({n}); refusing to restore it.")
            with zf.open("nexus.db") as f:
                if f.read(len(SQLITE_MAGIC)) != SQLITE_MAGIC:
                    raise BackupError("The database in that backup is damaged.")
            try:
                info = json.loads(zf.read("backup.json"))
            except ValueError as exc:
                raise BackupError("That backup's description (backup.json) is damaged.") from exc
    except zipfile.BadZipFile as exc:
        raise BackupError("That file isn't a zip, or it's incomplete.") from exc
    if not isinstance(info, dict) or info.get("app") != "NEXUS":
        raise BackupError("That isn't a NEXUS backup.")
    return {"created_at": info.get("created_at"), "original_files": info.get("original_files"),
            "original_bytes": info.get("original_bytes"), "size_bytes": zip_path.stat().st_size}


def restore(zip_path: Path) -> Path:
    """Replace the current data with a backup. The current data is moved aside, never deleted.
    If anything goes wrong, the current data is put back as it was.

    Returns the folder the previous data was moved to.
    """
    s = get_settings()
    check_backup(zip_path)
    db = _db_path()
    aside = s.data_dir.parent / f"{s.data_dir.name}-before-restore-{datetime.now():%Y%m%d-%H%M%S}"
    aside.mkdir(parents=True)
    moved: list[tuple[Path, Path]] = []
    try:
        for item in [db, db.with_name(db.name + "-wal"), db.with_name(db.name + "-shm"),
                     *(s.data_dir / d for d in (*ORIGINAL_DIRS, "previews"))]:
            if item.exists():
                shutil.move(str(item), str(aside / item.name))
                moved.append((item, aside / item.name))
        with zipfile.ZipFile(zip_path) as zf:
            for n in zf.namelist():
                if n == "backup.json" or n.endswith("/"):
                    continue
                target = db if n == "nexus.db" else s.data_dir / n
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(n) as src, open(target, "wb") as out:
                    shutil.copyfileobj(src, out)
    except BaseException:
        log.exception("Restore failed; putting the previous data back")
        for extracted in [db, *(s.data_dir / d for d in ORIGINAL_DIRS)]:
            if extracted.is_dir():
                shutil.rmtree(extracted, ignore_errors=True)
            elif extracted.exists():
                extracted.unlink()
        for original, kept in reversed(moved):
            shutil.move(str(kept), str(original))
        shutil.rmtree(aside, ignore_errors=True)
        raise
    return aside


def _pending_path() -> Path:
    return get_settings().data_dir / "restore-pending.zip"


def _result_path() -> Path:
    return get_settings().data_dir / "restore-result.json"


def stage_restore(upload, filename: str = "") -> dict:
    """Keep an uploaded backup to restore the next time NEXUS starts. `upload` is a readable file."""
    pending = _pending_path()
    pending.parent.mkdir(parents=True, exist_ok=True)
    partial = pending.with_suffix(".zip.partial")
    try:
        with open(partial, "wb") as out:
            shutil.copyfileobj(upload, out, 1024 * 1024)
        info = check_backup(partial)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    partial.replace(pending)
    info["filename"] = filename
    pending.with_suffix(".json").write_text(json.dumps(info))
    return info


def pending_restore() -> dict | None:
    pending = _pending_path()
    if not pending.is_file():
        return None
    try:
        return json.loads(pending.with_suffix(".json").read_text())
    except (OSError, ValueError):
        return {"size_bytes": pending.stat().st_size}


def cancel_restore() -> None:
    _pending_path().unlink(missing_ok=True)
    _pending_path().with_suffix(".json").unlink(missing_ok=True)


def last_restore() -> dict | None:
    """What happened the last time a staged backup was restored (shown once in Settings)."""
    try:
        return json.loads(_result_path().read_text())
    except (OSError, ValueError):
        return None


def forget_last_restore() -> None:
    _result_path().unlink(missing_ok=True)


def apply_pending_restore() -> dict | None:
    """At startup, before the database is opened: restore a staged backup, if there is one."""
    pending = _pending_path()
    if not pending.is_file():
        return None
    info = pending_restore() or {}
    result = {"at": datetime.now(timezone.utc).isoformat(), "backup_created_at": info.get("created_at"),
              "filename": info.get("filename")}
    for attempt in range(10):
        try:
            aside = restore(pending)
            result.update(ok=True, previous_data=str(aside))
            log.info("Restored a backup; the previous data is in %s", aside)
            break
        except PermissionError as exc:  # Windows: the NEXUS that just stopped may still be letting go of the files
            result.update(ok=False, error=str(exc))
            time.sleep(1 + attempt)
        except Exception as exc:
            result.update(ok=False, error=str(exc))
            break
    cancel_restore()
    _result_path().write_text(json.dumps(result))
    return result


# -- archives from a NEXUS folder, for the downloadable app ------------------
# Someone who used NEXUS from its folder (like C:\\Users\\you\\nexus) and then installs the app
# starts with an empty archive. The app looks in the usual places and offers to bring it over.
LOOK_FOR_OTHER_ARCHIVES = FROZEN  # only the downloadable app looks; a NEXUS folder already has its archive
FOLDER_NAMES = ("nexus", "Nexus", "NEXUS", "nexus-main", "Nexus-main", "Nexus-claude-nexus-ai-os-rhh5si")
PLACES = ("", "Desktop", "Documents", "Downloads", "OneDrive/Desktop", "OneDrive/Documents")


def _candidate_dirs() -> list[Path]:
    home = Path.home()
    return [home / place / name / "data" for place in PLACES for name in FOLDER_NAMES]


def found_archives() -> list[dict]:
    """Other NEXUS archives on this computer (a NEXUS folder's data), with how many ideas each has."""
    if not LOOK_FOR_OTHER_ARCHIVES:
        return []
    own = get_settings().data_dir.resolve()
    out, seen = [], set()
    for data in _candidate_dirs():
        db = data / "nexus.db"
        try:
            if not db.is_file():
                continue
            real = data.resolve()
            if real == own or real in seen:
                continue
            seen.add(real)
            con = sqlite3.connect(f"{db.as_uri()}?mode=ro", uri=True)
            try:
                count = con.execute("SELECT count(*) FROM creative_entries").fetchone()[0]
            finally:
                con.close()
        except (OSError, sqlite3.Error):
            continue
        if count:
            out.append({"folder": str(real.parent), "entries": count,
                        "modified": datetime.fromtimestamp(db.stat().st_mtime, timezone.utc).isoformat()})
    return out


def bring_archive(folder: str) -> dict:
    """Stage one of found_archives() to be restored at the next start. The other copy isn't touched."""
    match = next((a for a in found_archives() if a["folder"] == folder), None)
    if not match:
        raise BackupError("That NEXUS folder wasn't found.")
    data = Path(folder) / "data"
    pending = _pending_path()
    pending.parent.mkdir(parents=True, exist_ok=True)
    _write_zip(pending, data / "nexus.db", data)
    info = check_backup(pending)
    info.update(filename=f"your archive from {folder}", entries=match["entries"])
    pending.with_suffix(".json").write_text(json.dumps(info))
    return info


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
        try:
            aside = restore(path)
        except BackupError as exc:
            print(exc)
            return 1
        print(f"Restored {path.name}.\nYour previous data was moved to: {aside}\nStart NEXUS as usual.")
        return 0
    print("Usage:\n  python -m app.services.backup create\n  python -m app.services.backup restore <backup.zip>")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
