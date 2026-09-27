"""Start NEXUS with an icon in the system tray (the icons at the bottom right on Windows).

Left-click the icon (or right-click > Open NEXUS) to open it in the browser; right-click >
Quit NEXUS to stop it. The desktop icon starts NEXUS this way.

If the tray can't be shown (for example the `pystray` package isn't installed, or there's no
desktop), NEXUS simply runs without the icon, like `python run.py`.
"""
from __future__ import annotations

import logging
import sys
import threading
import webbrowser

from app.config import BASE_DIR, get_settings

log = logging.getLogger("nexus.tray")


def _server():
    import uvicorn

    s = get_settings()
    return uvicorn.Server(uvicorn.Config("app.main:app", host=s.host, port=s.port, log_level="info"))


def _load_tray():
    """pystray + the NEXUS icon, or None when a tray isn't available here."""
    try:
        import pystray
        from PIL import Image
    except Exception:  # not installed, or no desktop to show it on
        return None
    return pystray, Image.open(BASE_DIR / "static" / "icons" / "nexus.ico")


def main() -> None:
    s = get_settings()
    s.data_dir.mkdir(parents=True, exist_ok=True)
    if sys.stdout is None or sys.stderr is None:  # started without a console (pythonw)
        out = open(s.data_dir / "nexus.log", "a", buffering=1, encoding="utf-8")  # noqa: SIM115
        sys.stdout = sys.stdout or out
        sys.stderr = sys.stderr or out
    server = _server()
    tray = _load_tray()
    if tray is None:
        server.run()
        return
    pystray, image = tray
    url = f"http://localhost:{s.port}"
    thread = threading.Thread(target=server.run, name="nexus-server", daemon=True)
    thread.start()

    def open_nexus(icon, item):
        webbrowser.open(url)

    def quit_nexus(icon, item):
        icon.title = "NEXUS is stopping…"
        server.should_exit = True  # finishes the current requests, then stops cleanly
        thread.join(timeout=20)
        icon.stop()

    icon = pystray.Icon("NEXUS", image, "NEXUS is running", menu=pystray.Menu(
        pystray.MenuItem("Open NEXUS", open_nexus, default=True),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit NEXUS", quit_nexus),
    ))

    def watch():  # if the server stops by itself (e.g. already running elsewhere), remove the icon
        thread.join()
        icon.stop()

    threading.Thread(target=watch, name="nexus-tray-watch", daemon=True).start()
    icon.run()


if __name__ == "__main__":
    main()
