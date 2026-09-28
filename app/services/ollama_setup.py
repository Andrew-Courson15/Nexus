"""Install Ollama (the free program that runs NEXUS's AI) from inside NEXUS, on Windows and Mac.

Settings' setup check offers "Install Ollama for me": the official download from ollama.com is
fetched and installed in the background (Windows: its installer, quietly; Mac: Ollama.app into
Applications), then Ollama is started. Progress: status().
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path

from app.config import get_settings

DOWNLOADS = {
    "win32": "https://ollama.com/download/OllamaSetup.exe",
    "darwin": "https://ollama.com/download/Ollama-darwin.zip",
}
# ollama.com hands the download over to GitHub's release storage.
HOSTS = ("ollama.com", "github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com")


class OllamaSetupError(RuntimeError):
    pass


_lock = threading.Lock()
_state: dict = {"state": "idle", "done_bytes": 0, "total_bytes": None, "error": None}


def supported() -> bool:
    return sys.platform in DOWNLOADS


def installed() -> bool:
    if shutil.which("ollama"):
        return True
    if sys.platform == "win32":
        return any(p.is_file() for p in (Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe",
                                          Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Ollama" / "ollama.exe"))
    if sys.platform == "darwin":
        return any(p.is_dir() for p in (Path("/Applications/Ollama.app"), Path.home() / "Applications" / "Ollama.app"))
    return False


def status() -> dict:
    with _lock:
        return {**_state, "supported": supported(), "installed": installed()}


def start() -> dict:
    """Start downloading and installing Ollama in the background."""
    if not supported():
        raise OllamaSetupError("Install Ollama from ollama.com on this computer.")
    with _lock:
        if _state["state"] in ("downloading", "installing"):
            return {**_state}
        _state.update(state="downloading", done_bytes=0, total_bytes=None, error=None)
    threading.Thread(target=_run, name="nexus-ollama-setup", daemon=True).start()
    return status()


def _set(**kw) -> None:
    with _lock:
        _state.update(**kw)


def _run() -> None:
    try:
        file = _download(DOWNLOADS[sys.platform])
        _set(state="installing")
        _install(file)
        file.unlink(missing_ok=True)
        _set(state="done")
    except Exception as exc:  # shown in the setup check
        _set(state="error", error=str(exc) or exc.__class__.__name__)


def _download(url: str) -> Path:
    import httpx

    folder = get_settings().data_dir / "updates"
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / url.rsplit("/", 1)[1]
    partial = dest.with_name(dest.name + ".part")
    try:
        with httpx.stream("GET", url, follow_redirects=True, timeout=httpx.Timeout(30, read=120)) as r:
            r.raise_for_status()
            if r.url.host not in HOSTS:
                raise OllamaSetupError("The download went somewhere unexpected; stopped.")
            total = int(r.headers.get("content-length") or 0) or None
            _set(total_bytes=total)
            done = 0
            with open(partial, "wb") as out:
                for chunk in r.iter_bytes(1024 * 1024):
                    out.write(chunk)
                    done += len(chunk)
                    _set(done_bytes=done)
        if total and done != total:
            raise OllamaSetupError("The download was incomplete. Try again.")
    except httpx.HTTPError as exc:
        partial.unlink(missing_ok=True)
        raise OllamaSetupError(f"Couldn't download Ollama ({exc}). Is the internet connection working?") from exc
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    partial.replace(dest)
    return dest


def _install(file: Path) -> None:
    if sys.platform == "win32":
        with open(file, "rb") as f:
            if f.read(2) != b"MZ":
                raise OllamaSetupError("The download isn't a Windows installer.")
        proc = subprocess.Popen([str(file), "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART"],
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            code = proc.wait(timeout=1800)  # only the installer: the Ollama app it starts keeps running
        except subprocess.TimeoutExpired as exc:
            raise OllamaSetupError("Installing Ollama took too long.") from exc
        if code != 0:
            raise OllamaSetupError(f"Ollama's installer stopped (code {code}).")
        return
    # Mac: unzip Ollama.app into Applications (or ~/Applications if that isn't writable), then open it.
    target = Path("/Applications") if os.access("/Applications", os.W_OK) else Path.home() / "Applications"
    target.mkdir(parents=True, exist_ok=True)
    done = subprocess.run(["/usr/bin/ditto", "-x", "-k", str(file), str(target)], capture_output=True, text=True, timeout=600)
    if done.returncode != 0 or not (target / "Ollama.app").is_dir():
        raise OllamaSetupError(f"Couldn't unpack Ollama ({(done.stderr or '').strip()[:200]}).")
    subprocess.run(["/usr/bin/open", str(target / "Ollama.app")], timeout=60)


def reset() -> None:  # tests
    with _lock:
        _state.update(state="idle", done_bytes=0, total_bytes=None, error=None)
