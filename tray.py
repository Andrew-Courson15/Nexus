"""Start NEXUS with an icon in the system tray (the icons at the bottom right on Windows).

Left-click the icon (or right-click > Open NEXUS) to open it in the browser. Right-click for:
Back up now, Check for updates, Start with Windows (on/off), Open data folder, and Quit NEXUS.
The desktop icon starts NEXUS this way.

If the tray can't be shown (for example the `pystray` package isn't installed, or there's no
desktop), NEXUS simply runs without the icon, like `python run.py`.
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path

from app.config import BASE_DIR, FROZEN, get_settings

log = logging.getLogger("nexus.tray")


def _server():
    import uvicorn

    from app.main import app

    s = get_settings()
    return uvicorn.Server(uvicorn.Config(app, host=s.host, port=s.port, log_level="info"))


def _already_running(url: str) -> bool:
    """Is a NEXUS already answering here? (Starting the app twice just opens the running one.)"""
    import urllib.request

    try:
        with urllib.request.urlopen(url + "/api/ping", timeout=1.5) as r:  # noqa: S310 - our own localhost
            return r.status == 200
    except Exception:
        return False


def _open_when_ready(url: str) -> None:
    import time
    import urllib.request

    for _ in range(120):
        try:
            with urllib.request.urlopen(url + "/api/ping", timeout=1):  # noqa: S310 - our own localhost
                webbrowser.open(url)
                return
        except Exception:
            time.sleep(0.5)


# -- "Start with Windows": a shortcut in the Startup folder that starts NEXUS quietly -------------
def startup_shortcut() -> Path:
    startup = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    return startup / "NEXUS.lnk"


def starts_with_windows() -> bool:
    return sys.platform == "win32" and startup_shortcut().is_file()


def set_start_with_windows(on: bool) -> None:
    if sys.platform != "win32":
        return
    link = startup_shortcut()
    if not on:
        link.unlink(missing_ok=True)
        return
    ps = str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe")
    args = f'-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "{BASE_DIR / "scripts" / "nexus_launcher.ps1"}" -NoBrowser'
    target, workdir = ps, BASE_DIR
    if FROZEN:  # the packaged app starts itself directly
        target, args, workdir = sys.executable, "--no-browser", Path(sys.executable).parent
    script = ("$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:NEXUS_LNK); "
              "$s.TargetPath = $env:NEXUS_PS; $s.Arguments = $env:NEXUS_ARGS; $s.WorkingDirectory = $env:NEXUS_DIR; "
              "$s.IconLocation = $env:NEXUS_ICON; $s.WindowStyle = 7; $s.Save()")
    env = {**os.environ, "NEXUS_LNK": str(link), "NEXUS_PS": target, "NEXUS_ARGS": args, "NEXUS_DIR": str(workdir),
           "NEXUS_ICON": f"{BASE_DIR / 'static' / 'icons' / 'nexus.ico'},0"}
    subprocess.run([ps, "-NoProfile", "-Command", script], env=env, check=False, timeout=30,
                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def _load_tray():
    """pystray + the NEXUS icon, or None when a tray isn't available here."""
    try:
        import pystray
        from PIL import Image
    except Exception:  # not installed, or no desktop to show it on
        return None
    return pystray, Image.open(BASE_DIR / "static" / "icons" / "nexus.ico")


def _notify(icon, message: str) -> None:
    try:
        icon.notify(message, "NEXUS")
    except Exception:  # notifications are a nicety; some systems don't support them
        pass


def main() -> None:
    s = get_settings()
    s.data_dir.mkdir(parents=True, exist_ok=True)
    if sys.stdout is None or sys.stderr is None:  # started without a console (pythonw)
        out = open(s.data_dir / "nexus.log", "a", buffering=1, encoding="utf-8")  # noqa: SIM115
        sys.stdout = sys.stdout or out
        sys.stderr = sys.stderr or out
    url = f"http://localhost:{s.port}"
    if FROZEN and _already_running(url):  # double-clicked again: just show the running NEXUS
        webbrowser.open(url)
        return
    server = _server()
    if FROZEN and "--no-browser" not in sys.argv:  # the packaged app opens itself once it's ready
        threading.Thread(target=_open_when_ready, args=(url,), name="nexus-open", daemon=True).start()
    tray = _load_tray()
    if tray is None:
        server.run()
        return
    pystray, image = tray
    thread = threading.Thread(target=server.run, name="nexus-server", daemon=True)
    thread.start()

    def open_nexus(icon, item):
        webbrowser.open(url)

    def back_up(icon, item):
        from app.services.backup import runner

        started = runner.start_now()
        _notify(icon, "Backing up NEXUS in the background…" if started else "A backup is already running.")

    def check_updates(icon, item):
        webbrowser.open(url + "/#/settings?check-update=1")

    def toggle_startup(icon, item):
        set_start_with_windows(not starts_with_windows())

    def open_folder(icon, item):
        folder = str(s.data_dir)
        if sys.platform == "win32":
            os.startfile(folder)  # noqa: S606 - opens Explorer on the user's own data folder
        else:
            subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", folder])

    def quit_nexus(icon, item):
        icon.title = "NEXUS is stopping…"
        server.should_exit = True  # finishes the current requests, then stops cleanly
        thread.join(timeout=20)
        icon.stop()

    icon = pystray.Icon("NEXUS", image, "NEXUS is running", menu=pystray.Menu(
        pystray.MenuItem("Open NEXUS", open_nexus, default=True),
        pystray.MenuItem("Back up now", back_up),
        pystray.MenuItem("Check for updates", check_updates),
        pystray.MenuItem("Start with Windows", toggle_startup, checked=lambda item: starts_with_windows(),
                         visible=sys.platform == "win32"),
        pystray.MenuItem("Open data folder", open_folder),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit NEXUS", quit_nexus),
    ))

    def watch():  # if the server stops by itself (e.g. already running elsewhere), remove the icon
        thread.join()
        icon.stop()

    threading.Thread(target=watch, name="nexus-tray-watch", daemon=True).start()
    try:
        icon.run()
    except Exception:  # the icon couldn't be shown after all: keep NEXUS running without it
        log.exception("The tray icon couldn't be shown; NEXUS keeps running without it")
        thread.join()


if __name__ == "__main__":
    main()
