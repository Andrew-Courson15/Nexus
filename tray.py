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

from app.config import BASE_DIR, FROZEN, get_settings

log = logging.getLogger("nexus.tray")


def _server():
    import uvicorn

    from app.main import app

    s = get_settings()
    return uvicorn.Server(uvicorn.Config(app, host=s.host, port=s.port, log_level="info"))


def _running(url: str) -> dict | None:
    """What's answering here, if anything: {"version", "app"} (older NEXUS versions don't say "app")."""
    import json
    import urllib.request

    try:
        with urllib.request.urlopen(url + "/api/ping", timeout=1.5) as r:  # noqa: S310 - our own localhost
            return json.loads(r.read()) if r.status == 200 else None
    except Exception:
        return None


def _ask(message: str) -> bool:
    """A Try again / Cancel question in a system dialog. True for Try again."""
    try:
        if sys.platform == "win32":
            import ctypes

            return ctypes.windll.user32.MessageBoxW(None, message, "NEXUS", 0x5 | 0x30 | 0x10000) == 4  # RETRYCANCEL
        if sys.platform == "darwin":
            script = ('display dialog (system attribute "NEXUS_MSG") with title "NEXUS" '
                      'buttons {"Cancel", "Try again"} default button "Try again" with icon caution')
            r = subprocess.run(["osascript", "-e", script], env={**os.environ, "NEXUS_MSG": message},
                               capture_output=True, timeout=600)
            return r.returncode == 0
    except Exception:
        log.exception("Couldn't show a dialog")
    print(message)
    return False


def _wait_for_other_nexus(url: str) -> bool:
    """The NEXUS that runs from a folder (like C:\\Users\\you\\nexus) is using the same address. Ask to
    close it; True once it's gone."""
    msg = ("Another NEXUS is already running on this computer: the one from your NEXUS folder.\n\n"
           "Close it first (its window, or its icon by the clock → Quit NEXUS), then click Try again.\n\n"
           "Your ideas from it can be brought into this app: it offers when it opens.")
    while _running(url):
        if not _ask(msg):
            return False
    return True


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


# -- "Start with Windows": see app/services/autostart.py (also in Settings → This computer) ------
def starts_with_windows() -> bool:
    from app.services import autostart

    return sys.platform == "win32" and autostart.enabled()


def set_start_with_windows(on: bool) -> None:
    from app.services import autostart

    if sys.platform == "win32":
        autostart.set_enabled(on)


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


_restart = threading.Event()  # set by "Restart NEXUS now" on the page


def _start_again() -> None:
    """Start a new NEXUS once this one has stopped (so the new one can take the address)."""
    args = [sys.executable, "--no-browser"] if FROZEN else [sys.executable, str(BASE_DIR / "tray.py"), "--no-browser"]
    subprocess.Popen(args, cwd=os.path.dirname(sys.executable) if FROZEN else str(BASE_DIR), close_fds=True,
                     creationflags=getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))


def main() -> None:
    from app.services import lifecycle

    _run()
    if _restart.is_set():
        if lifecycle.run_after_exit:  # an update: the installer replaces this copy, then opens NEXUS again
            subprocess.Popen(lifecycle.run_after_exit, close_fds=True, start_new_session=sys.platform != "win32",
                             creationflags=getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
        else:
            _start_again()


def _run() -> None:
    s = get_settings()
    s.data_dir.mkdir(parents=True, exist_ok=True)
    if sys.stdout is None or sys.stderr is None:  # started without a console (pythonw)
        out = open(s.data_dir / "nexus.log", "a", buffering=1, encoding="utf-8")  # noqa: SIM115
        sys.stdout = sys.stdout or out
        sys.stderr = sys.stderr or out
    url = f"http://localhost:{s.port}"
    if FROZEN:
        running = _running(url)
        if running and running.get("app"):  # double-clicked again: just show the running NEXUS
            webbrowser.open(url)
            return
        if running and not _wait_for_other_nexus(url):  # the NEXUS from a folder is still open
            return
    server = _server()
    if FROZEN and "--no-browser" not in sys.argv:  # the packaged app opens itself once it's ready
        threading.Thread(target=_open_when_ready, args=(url,), name="nexus-open", daemon=True).start()
    from app.services import lifecycle

    def restart_without_tray():
        _restart.set()
        server.should_exit = True

    tray = _load_tray()
    if tray is None:
        lifecycle.restart_hook = restart_without_tray
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
        try:
            icon.stop()
        except Exception:  # the icon never showed (see the end of this function)
            pass

    def restart():  # from the page (e.g. after choosing a backup to restore); main() starts the new NEXUS
        _restart.set()
        quit_nexus(icon, None)

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
    lifecycle.restart_hook = restart
    intro = s.data_dir / ".tray-intro-shown"
    if not intro.exists():  # the first time: say where NEXUS lives now

        def hello():
            import time

            time.sleep(3)
            _notify(icon, "NEXUS is running. Its icon is here by the clock: click to open, right-click to quit.")
            try:
                intro.touch()
            except OSError:
                pass

        threading.Thread(target=hello, name="nexus-tray-hello", daemon=True).start()
    try:
        icon.run()
    except Exception:  # the icon couldn't be shown after all: keep NEXUS running without it
        log.exception("The tray icon couldn't be shown; NEXUS keeps running without it")
        lifecycle.restart_hook = restart_without_tray
        thread.join()
    thread.join(timeout=30)  # stopped (Quit or Restart): let the server finish before a new one starts


if __name__ == "__main__":
    main()
