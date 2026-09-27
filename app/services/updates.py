"""Updating NEXUS from inside the app (Settings → Updates), on the computer it runs on.

Checking asks git whether the branch this copy follows has new commits. Updating starts the same
one-step update script as the terminal (scripts/update_windows.ps1 or scripts/update_mac.sh) in
the background: it stops NEXUS, downloads the update, sets it up and starts it again. Your data
folder and .env are never touched by an update.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

from app.config import BASE_DIR, get_settings

GIT_TIMEOUT = 40


class UpdateError(RuntimeError):
    pass


def _git(*args: str, timeout: float = GIT_TIMEOUT) -> str:
    try:
        done = subprocess.run(["git", *args], cwd=BASE_DIR, capture_output=True, text=True, timeout=timeout,
                              env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except FileNotFoundError as exc:
        raise UpdateError("Git isn't installed on this computer.") from exc
    except subprocess.TimeoutExpired as exc:
        raise UpdateError("Checking took too long. Is the internet connection working?") from exc
    if done.returncode != 0:
        raise UpdateError((done.stderr or done.stdout).strip().splitlines()[-1] if (done.stderr or done.stdout).strip()
                          else f"git {args[0]} failed")
    return done.stdout.strip()


def _version_in(source: str) -> str | None:
    m = re.search(r'__version__ = "([^"]+)"', source)
    return m.group(1) if m else None


def check() -> dict:
    """Is a newer version available on the branch this copy follows?"""
    from app import __version__

    if not (BASE_DIR / ".git").exists():
        return {"can_update": False, "current_version": __version__,
                "reason": "This copy wasn't installed with git, so it can't update itself."}
    branch = _git("rev-parse", "--abbrev-ref", "HEAD")
    if branch == "HEAD":
        return {"can_update": False, "current_version": __version__, "reason": "This copy isn't on a branch."}
    dirty = [line for line in _git("status", "--porcelain", "--untracked-files=no").splitlines() if line.strip()]
    _git("fetch", "--quiet", "origin", branch, timeout=90)
    behind = int(_git("rev-list", "--count", f"HEAD..origin/{branch}") or 0)
    changes = _git("log", "--format=%s", "-n", "12", f"HEAD..origin/{branch}").splitlines() if behind else []
    latest = _version_in(_git("show", f"origin/{branch}:app/__init__.py")) if behind else __version__
    reason = None
    if dirty:
        reason = ("Files in the NEXUS folder were changed on this computer, so updating could overwrite them. "
                  "Update from a terminal instead (scripts/update_windows.ps1 or scripts/update_mac.sh).")
    return {"can_update": behind > 0 and not dirty, "current_version": __version__, "latest_version": latest,
            "behind": behind, "changes": [c.split("\n")[0][:200] for c in changes], "branch": branch,
            "reason": reason}


def log_path() -> Path:
    return get_settings().data_dir / "update.log"


def start() -> None:
    """Run the update script in the background. It stops this NEXUS, so the reply may be the last one."""
    info = check()
    if not info["can_update"]:
        raise UpdateError(info.get("reason") or "NEXUS is already up to date.")
    log = open(log_path(), "a", encoding="utf-8")  # noqa: SIM115 - handed to the child process
    if sys.platform == "win32":
        ps = str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe")
        cmd = [ps, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(BASE_DIR / "scripts" / "update_windows.ps1"),
               "-NoBrowser"]
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW | 0x01000000  # break away from job
        try:
            subprocess.Popen(cmd, cwd=BASE_DIR, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                             creationflags=flags)
        except OSError:  # not allowed to break away: start without that flag
            subprocess.Popen(cmd, cwd=BASE_DIR, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                             creationflags=flags & ~0x01000000)
    else:
        subprocess.Popen(["bash", str(BASE_DIR / "scripts" / "update_mac.sh"), "--no-browser"], cwd=BASE_DIR,
                         stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
    log.close()
