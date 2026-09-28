"""Restarting NEXUS from the page (the downloadable app only: its tray knows how to start itself again).

The tray sets `restart_hook`; when it's None (running from a NEXUS folder, or in tests), a restart
has to be done by hand: close NEXUS and open it again.
"""
from __future__ import annotations

import threading
import time
from collections.abc import Callable

restart_hook: Callable[[], None] | None = None
# A command to run once NEXUS has stopped (the Windows installer, for an update). Read by the tray.
run_after_exit: list[str] | None = None


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


def exit_and_run(command: list[str]) -> bool:
    """Stop NEXUS, then run `command` (which starts NEXUS again when it's done). False if unsupported."""
    global run_after_exit
    if restart_hook is None:
        return False
    run_after_exit = command
    return restart_soon()
