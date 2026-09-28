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

from app.config import BASE_DIR, FROZEN, get_settings

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

    if FROZEN:
        return check_release(__version__)
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
    if dirty and behind:
        reason = ("Files in the NEXUS folder were changed on this computer, so updating could overwrite them. "
                  "Update from a terminal instead (scripts/update_windows.ps1 or scripts/update_mac.sh).")
    return {"can_update": behind > 0 and not dirty, "current_version": __version__, "latest_version": latest,
            "behind": behind, "changes": [c.split("\n")[0][:200] for c in changes], "branch": branch,
            "reason": reason}


RELEASES_API = "https://api.github.com/repos/Andrew-Courson15/Nexus/releases/latest"


def _newer(a: str, b: str) -> bool:
    """Is version a newer than b? ("0.15.0" > "0.14.1")"""
    def parts(v: str) -> tuple:
        return tuple(int(x) if x.isdigit() else 0 for x in re.split(r"[.\-+]", v.lstrip("v"))[:3])
    return parts(a) > parts(b)


def _release_notes(body: str) -> list[str]:
    """The release's bullet points (from CHANGELOG.md), one line each, without Markdown; the download
    instructions after "---" are left out."""
    items: list[str] = []
    for line in body.split("\n---", 1)[0].splitlines():
        if line.lstrip().startswith(("- ", "* ")):
            items.append(line.strip()[2:])
        elif line.strip() and items:  # a wrapped bullet continues
            items[-1] += " " + line.strip()
    return [re.sub(r"\*\*|`", "", i)[:300] for i in items][:8]


def check_release(current: str, platform: str | None = None) -> dict:
    """The downloadable app: is there a newer release to download? (Asks the NEXUS Releases page.)
    `platform` defaults to this computer's ("win32", "darwin")."""
    platform = platform or sys.platform
    import httpx

    base = {"can_update": False, "current_version": current, "packaged": True}
    try:
        headers = {"Accept": "application/vnd.github+json"}
        if os.environ.get("GITHUB_TOKEN"):  # the build's own checks (shared machines hit GitHub's anonymous limit)
            headers["Authorization"] = f"Bearer {os.environ['GITHUB_TOKEN']}"
        r = httpx.get(RELEASES_API, timeout=10, headers=headers)
        if r.status_code == 404:
            return {**base, "behind": 0, "reason": None}
        r.raise_for_status()
        data = r.json()
    except Exception:
        return {**base, "reason": "Couldn't check for a newer version. Is the internet connection working?"}
    latest = str(data.get("tag_name") or "").lstrip("v")
    if not latest or not _newer(latest, current):
        return {**base, "behind": 0, "latest_version": latest or current, "reason": None}
    notes = _release_notes(str(data.get("body") or ""))
    wanted = {"win32": f"NEXUS-Setup-{latest}.exe", "darwin": f"NEXUS-{latest}.dmg"}.get(platform)
    installer = next((a for a in data.get("assets") or [] if isinstance(a, dict)
                      and a.get("name") == wanted and a.get("browser_download_url")), None)
    if installer and (platform == "win32" or mac_bundle()):  # the app updates itself: download, install, reopen
        return {**base, "can_update": True, "behind": 1, "latest_version": latest, "changes": notes,
                "download_url": data.get("html_url"), "installer_url": installer["browser_download_url"],
                "installer_size": installer.get("size"), "reason": None}
    return {**base, "behind": 1, "latest_version": latest, "changes": notes, "download_url": data.get("html_url"),
            "reason": "Download the new version and install it over this one; your ideas and settings stay where they are."}


INSTALLER_HOSTS = ("github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com")


def mac_bundle() -> Path | None:
    """The NEXUS.app this copy runs from, if it can be replaced (not run from the .dmg itself)."""
    if not FROZEN or sys.platform != "darwin":
        return None
    bundle = Path(sys.executable).resolve().parents[2]
    if bundle.suffix != ".app" or str(bundle).startswith(("/Volumes/", "/private/var/folders/")):
        return None  # still on the disk image, or run from a quarantined copy macOS moved aside
    return bundle if os.access(bundle.parent, os.W_OK) else None


def download_installer(info: dict) -> Path:
    """Download the new installer (.exe or .dmg, from the NEXUS Releases page only) into the data folder."""
    import httpx

    url = info["installer_url"]
    if httpx.URL(url).scheme != "https" or httpx.URL(url).host not in INSTALLER_HOSTS:
        raise UpdateError("The update isn't on the NEXUS Releases page; not downloading it.")
    folder = get_settings().data_dir / "updates"
    folder.mkdir(parents=True, exist_ok=True)
    for old in [*folder.glob("NEXUS-Setup-*.exe"), *folder.glob("NEXUS-*.dmg")]:
        old.unlink(missing_ok=True)
    dmg = info["installer_url"].endswith(".dmg")
    dest = folder / (f"NEXUS-{info['latest_version']}.dmg" if dmg else f"NEXUS-Setup-{info['latest_version']}.exe")
    partial = dest.with_suffix(".part")
    try:
        with httpx.stream("GET", url, follow_redirects=True, timeout=httpx.Timeout(30, read=120)) as r:
            r.raise_for_status()
            if r.url.host not in INSTALLER_HOSTS:
                raise UpdateError("The download went somewhere unexpected; stopped.")
            with open(partial, "wb") as out:
                for chunk in r.iter_bytes(1024 * 1024):
                    out.write(chunk)
        size = partial.stat().st_size
        if info.get("installer_size") and size != info["installer_size"]:
            raise UpdateError("The download was incomplete. Try again.")
        with open(partial, "rb") as f:
            if dmg:
                f.seek(max(0, size - 512))
                if f.read(4) != b"koly":  # the trailer every .dmg ends with
                    raise UpdateError("The download isn't a Mac disk image.")
            elif f.read(2) != b"MZ":
                raise UpdateError("The download isn't a Windows installer.")
    except httpx.HTTPError as exc:
        partial.unlink(missing_ok=True)
        raise UpdateError(f"Couldn't download the update ({exc}).") from exc
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    partial.replace(dest)
    return dest


# Mac: after NEXUS has stopped, copy the new NEXUS.app out of the disk image, swap it in (the old one
# is kept until the new one is in place), and open it.
MAC_INSTALL = r"""
set -eu
dmg="$1"; app="$2"; mnt="$(mktemp -d)"
echo "Updating $app from $dmg"
hdiutil attach -nobrowse -readonly -mountpoint "$mnt" "$dmg"
rm -rf "$app.new"
ditto "$mnt/NEXUS.app" "$app.new"
hdiutil detach "$mnt" || hdiutil detach -force "$mnt" || true
rm -rf "$app.old"
mv "$app" "$app.old"
mv "$app.new" "$app"
rm -rf "$app.old"
open -n "$app" --args --no-browser
echo "Updated."
"""


def mac_install_command(dmg: Path, bundle: Path) -> list[str]:
    log = get_settings().data_dir / "update.log"
    return ["/bin/bash", "-c", 'exec >>"$3" 2>&1' + MAC_INSTALL, "nexus-update", str(dmg), str(bundle), str(log)]


def installer_command(setup: Path) -> list[str]:
    """Install over this copy without questions, then open NEXUS again (see packaging/nexus.iss)."""
    if setup.suffix == ".dmg":
        return mac_install_command(setup, mac_bundle())
    return [str(setup), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS", "/RELAUNCH=1",
            f"/LOG={get_settings().data_dir / 'update.log'}"]


def log_path() -> Path:
    return get_settings().data_dir / "update.log"


def start() -> None:
    """Run the update script in the background. It stops this NEXUS, so the reply may be the last one."""
    info = check()
    if not info["can_update"]:
        raise UpdateError(info.get("reason") or "NEXUS is already up to date.")
    if FROZEN:
        from app.services import lifecycle

        if not lifecycle.can_restart():
            raise UpdateError("This NEXUS can't update itself. Download the new version from the Releases page.")
        lifecycle.exit_and_run(installer_command(download_installer(info)))
        return
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
