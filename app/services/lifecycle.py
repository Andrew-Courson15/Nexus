"""Restarting NEXUS from the page (the downloadable app only: its tray knows how to start itself again).

The tray sets `restart_hook`; when it's None (running from a NEXUS folder, or in tests), a restart
has to be done by hand: close NEXUS and open it again.
"""
from __future__ import annotations

import threading
import time
from collections.abc import Callable

restart_hook: Callable[[], None] | None = None


def can_restart() -> bool:
    return restart_hook is not None


def restart_soon(delay: float = 0.5) -> bool:
    """Restart after the current reply has been sent. False if this NEXUS can't restart itself."""
    hook = restart_hook
    if hook is None:
        return False

    def later():
        time.sleep(delay)
        hook()

    threading.Thread(target=later, name="nexus-restart", daemon=True).start()
    return True
