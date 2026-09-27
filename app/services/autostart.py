"""Start NEXUS when you sign in (so your phone can reach it without you opening anything).

Windows: a shortcut in the Startup folder that starts NEXUS quietly, with its tray icon.
Mac: a login item (a LaunchAgent) that starts NEXUS in the background.
The same switch is in the tray menu (Windows) and in Settings → This computer.
"""
from __future__ import annotations

import os
import plistlib
import subprocess
import sys
from pathlib import Path

from app.config import BASE_DIR, FROZEN, get_settings

MAC_LABEL = "local.nexus"


def supported() -> bool:
    return sys.platform in ("win32", "darwin")


def windows_shortcut() -> Path:
    startup = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    return startup / "NEXUS.lnk"


def mac_plist() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{MAC_LABEL}.plist"


def enabled() -> bool:
    if sys.platform == "win32":
        return windows_shortcut().is_file()
    if sys.platform == "darwin":
        return mac_plist().is_file()
    return False


def set_enabled(on: bool) -> None:
    if sys.platform == "win32":
        _windows(on)
    elif sys.platform == "darwin":
        _mac(on)


def _windows(on: bool) -> None:
    link = windows_shortcut()
    if not on:
        link.unlink(missing_ok=True)
        return
    ps = str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe")
    target, workdir = ps, BASE_DIR
    args = f'-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "{BASE_DIR / "scripts" / "nexus_launcher.ps1"}" -NoBrowser'
    if FROZEN:  # the packaged app starts itself directly
        target, args, workdir = sys.executable, "--no-browser", Path(sys.executable).parent
    script = ("$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:NEXUS_LNK); "
              "$s.TargetPath = $env:NEXUS_PS; $s.Arguments = $env:NEXUS_ARGS; $s.WorkingDirectory = $env:NEXUS_DIR; "
              "$s.IconLocation = $env:NEXUS_ICON; $s.WindowStyle = 7; $s.Save()")
    env = {**os.environ, "NEXUS_LNK": str(link), "NEXUS_PS": target, "NEXUS_ARGS": args, "NEXUS_DIR": str(workdir),
           "NEXUS_ICON": f"{BASE_DIR / 'static' / 'icons' / 'nexus.ico'},0"}
    subprocess.run([ps, "-NoProfile", "-Command", script], env=env, check=False, timeout=30,
                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def _mac(on: bool) -> None:
    plist = mac_plist()
    if not on:
        plist.unlink(missing_ok=True)
        return
    log = str(get_settings().data_dir / "nexus.log")
    if FROZEN:
        program, workdir = [sys.executable, "--no-browser"], str(Path(sys.executable).parent)
    else:
        python = BASE_DIR / ".venv" / "bin" / "python"
        program, workdir = [str(python if python.exists() else Path(sys.executable)), "run.py"], str(BASE_DIR)
    plist.parent.mkdir(parents=True, exist_ok=True)
    with plist.open("wb") as fh:
        plistlib.dump({"Label": MAC_LABEL, "ProgramArguments": program, "WorkingDirectory": workdir,
                       "RunAtLoad": True, "StandardOutPath": log, "StandardErrorPath": log}, fh)
