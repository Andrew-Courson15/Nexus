"""Phone access from inside the app (Settings → Your phone), the same steps as the phone setup scripts.

Tailscale gives this computer a private address that only your own devices can open. Setting up:
1. find Tailscale and check it's signed in, 2. remember this computer's Tailscale name so NEXUS
answers to it, 3. turn on the private HTTPS link (`tailscale serve`) to NEXUS's port.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from app.config import get_settings

TIMEOUT = 30


class PhoneSetupError(RuntimeError):
    pass


def find_tailscale() -> str | None:
    found = shutil.which("tailscale")
    if found:
        return found
    candidates = {
        "win32": [Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Tailscale" / "tailscale.exe"],
        "darwin": [Path("/Applications/Tailscale.app/Contents/MacOS/Tailscale"), Path("/usr/local/bin/tailscale"),
                   Path("/opt/homebrew/bin/tailscale")],
    }.get(sys.platform, [])
    return next((str(p) for p in candidates if p.exists()), None)


def _run(binary: str, *args: str) -> subprocess.CompletedProcess:
    try:
        return subprocess.run([binary, *args], capture_output=True, text=True, timeout=TIMEOUT,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PhoneSetupError(f"Tailscale didn't answer ({exc}).") from exc


def host_file() -> Path:
    return get_settings().data_dir / "phone.json"


def saved_host() -> str | None:
    try:
        host = json.loads(host_file().read_text(encoding="utf-8")).get("host")
    except (OSError, ValueError, AttributeError):
        return None
    return host if isinstance(host, str) and host.endswith(".ts.net") else None


def status() -> dict:
    """What's ready: Tailscale installed, signed in, and this computer's private name."""
    binary = find_tailscale()
    if not binary:
        return {"installed": False, "running": False, "name": None, "next": "install"}
    done = _run(binary, "status", "--json")
    try:
        data = json.loads(done.stdout or "{}")
    except ValueError:
        data = {}
    running = data.get("BackendState") == "Running"
    name = ((data.get("Self") or {}).get("DNSName") or "").rstrip(".") or None
    return {"installed": True, "running": running, "name": name if running else None,
            "next": "sign_in" if not running else "magicdns" if not name else "turn_on",
            "enabled_for": saved_host()}


def turn_on() -> dict:
    """Let NEXUS answer to this computer's Tailscale name and turn on the private HTTPS link."""
    st = status()
    if st["next"] == "install":
        raise PhoneSetupError("Tailscale isn't installed on this computer yet.")
    if st["next"] == "sign_in":
        raise PhoneSetupError("Tailscale is installed but not signed in. Open Tailscale and log in, then try again.")
    if st["next"] == "magicdns":
        raise PhoneSetupError("Couldn't find this computer's Tailscale name. Turn on MagicDNS at "
                              "https://login.tailscale.com/admin/dns, then try again.")
    name = st["name"]
    path = host_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"host": name}), encoding="utf-8")
    get_settings.cache_clear()  # NEXUS answers to the new name straight away
    done = _run(find_tailscale(), "serve", "--bg", str(get_settings().port))
    if done.returncode != 0:
        detail = (done.stderr or done.stdout or "").strip().splitlines()
        raise PhoneSetupError(
            "Tailscale couldn't turn on the private link" + (f": {detail[-1]}" if detail else ".")
            + " If it mentions HTTPS or certificates, turn on MagicDNS and HTTPS Certificates at "
              "https://login.tailscale.com/admin/dns. If it says access denied, run the phone setup script as "
              "administrator instead.")
    return {"url": f"https://{name}", "name": name}
