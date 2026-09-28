"""Settings → Updates: checking git for a newer NEXUS and starting the update script."""
import subprocess

import pytest

from app.services import lock, updates


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                   env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                        "GIT_COMMITTER_EMAIL": "t@t", "HOME": str(cwd), "PATH": "/usr/bin:/bin:/usr/local/bin"})


@pytest.fixture
def repos(tmp_path, monkeypatch):
    origin, work, other = tmp_path / "origin.git", tmp_path / "work", tmp_path / "other"
    _git(tmp_path, "init", "--bare", "-b", "main", str(origin))
    _git(tmp_path, "clone", str(origin), str(work))
    (work / "app").mkdir()
    (work / "app" / "__init__.py").write_text('__version__ = "0.14.0"\n')
    _git(work, "add", ".")
    _git(work, "commit", "-m", "First")
    _git(work, "push", "origin", "HEAD:main")
    _git(tmp_path, "clone", str(origin), str(other))
    (other / "app" / "__init__.py").write_text('__version__ = "9.9.0"\n')
    _git(other, "commit", "-am", "Timeline for comics")
    _git(other, "push", "origin", "HEAD:main")
    monkeypatch.setattr(updates, "BASE_DIR", work)
    monkeypatch.setattr(lock, "LOOPBACK", lock.LOOPBACK | {"testclient"})
    monkeypatch.setattr(lock, "_LOCAL_NAMES", lock._LOCAL_NAMES | {"testserver"})
    return work


def test_check_finds_the_newer_version(client, repos):
    info = client.get("/api/update").json()
    assert info["can_update"] and info["behind"] == 1 and info["latest_version"] == "9.9.0"
    assert info["changes"] == ["Timeline for comics"] and info["branch"] == "main"


def test_local_changes_block_the_in_app_update(client, repos):
    (repos / "app" / "__init__.py").write_text('__version__ = "mine"\n')
    info = client.get("/api/update").json()
    assert not info["can_update"] and "changed on this computer" in info["reason"]
    assert client.post("/api/update").status_code == 400


def test_start_runs_the_update_script_in_the_background(client, repos, monkeypatch):
    started = []
    assert updates.check()["can_update"]
    monkeypatch.setattr(updates, "check", lambda: {"can_update": True})
    monkeypatch.setattr(updates.subprocess, "Popen", lambda cmd, **kw: started.append((cmd, kw)))
    r = client.post("/api/update")
    assert r.status_code == 202, r.text
    (cmd, kw), = started
    assert cmd[-2].endswith("update_mac.sh") and cmd[-1] == "--no-browser" and kw["start_new_session"]


def test_other_devices_cannot_update(client, repos, monkeypatch):
    monkeypatch.setattr(lock, "LOOPBACK", {"127.0.0.1"})
    assert client.get("/api/update").status_code == 403
    assert client.post("/api/update").status_code == 403


def test_not_a_git_copy(client, tmp_path, monkeypatch):
    monkeypatch.setattr(updates, "BASE_DIR", tmp_path)
    monkeypatch.setattr(lock, "LOOPBACK", lock.LOOPBACK | {"testclient"})
    monkeypatch.setattr(lock, "_LOCAL_NAMES", lock._LOCAL_NAMES | {"testserver"})
    info = client.get("/api/update").json()
    assert not info["can_update"] and "wasn't installed with git" in info["reason"]


def test_packaged_app_checks_the_releases_page(monkeypatch):
    import httpx

    class Reply:
        def __init__(self, status, data=None):
            self.status_code, self._data = status, data or {}

        def raise_for_status(self):
            if self.status_code >= 400:
                raise httpx.HTTPStatusError("x", request=None, response=None)

        def json(self):
            return self._data

    monkeypatch.setattr(httpx, "get", lambda *a, **k: Reply(200, {"tag_name": "v9.9.0", "html_url": "https://example/rel",
                                                                   "body": "- Timeline\n- Characters"}))
    info = updates.check_release("0.14.1")
    assert info["behind"] == 1 and info["latest_version"] == "9.9.0" and info["download_url"] == "https://example/rel"
    assert info["changes"] == ["Timeline", "Characters"] and info["can_update"] is False
    monkeypatch.setattr(httpx, "get", lambda *a, **k: Reply(200, {"tag_name": "v0.14.1"}))
    assert updates.check_release("0.14.1")["behind"] == 0
    monkeypatch.setattr(httpx, "get", lambda *a, **k: Reply(404))
    assert updates.check_release("0.14.1")["behind"] == 0
    assert updates._newer("0.14.10", "0.14.9") and not updates._newer("0.14.1", "0.14.1")


def test_the_windows_app_updates_itself_from_the_release(monkeypatch, tmp_path):
    import httpx

    from app.services import lifecycle

    release = {"tag_name": "v9.9.0", "html_url": "https://example/rel", "body": "- Faster", "assets": [
        {"name": "NEXUS-9.9.0.dmg", "browser_download_url": "https://github.com/x/NEXUS-9.9.0.dmg"},
        {"name": "NEXUS-Setup-9.9.0.exe", "size": 6,
         "browser_download_url": "https://github.com/Andrew-Courson15/Nexus/releases/download/v9.9.0/NEXUS-Setup-9.9.0.exe"}]}

    class Reply:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return release

    monkeypatch.setattr(httpx, "get", lambda *a, **k: Reply())
    monkeypatch.setattr(updates.sys, "platform", "darwin")
    assert not updates.check_release("0.15.1")["can_update"]  # a Mac copy it can't replace: the download page
    monkeypatch.setattr(updates, "mac_bundle", lambda: tmp_path / "Applications" / "NEXUS.app")
    mac = updates.check_release("0.15.1")
    assert mac["can_update"] and mac["installer_url"].endswith("NEXUS-9.9.0.dmg")
    cmd = updates.mac_install_command(tmp_path / "NEXUS-9.9.0.dmg", tmp_path / "Applications" / "NEXUS.app")
    assert cmd[0] == "/bin/bash" and cmd[-3:-1] == [str(tmp_path / "NEXUS-9.9.0.dmg"), str(tmp_path / "Applications" / "NEXUS.app")]
    monkeypatch.setattr(updates.sys, "platform", "win32")
    info = updates.check_release("0.15.1")
    assert info["can_update"] and info["installer_url"].endswith("NEXUS-Setup-9.9.0.exe")

    class Stream:
        def __init__(self, body, host="release-assets.githubusercontent.com"):
            self.body, self.url = body, httpx.URL(f"https://{host}/file")

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def raise_for_status(self):
            pass

        def iter_bytes(self, _n):
            yield self.body

    monkeypatch.setattr(httpx, "stream", lambda *a, **k: Stream(b"MZ1234"))
    setup = updates.download_installer(info)
    assert setup.read_bytes() == b"MZ1234" and setup.name == "NEXUS-Setup-9.9.0.exe"
    cmd = updates.installer_command(setup)
    assert cmd[0] == str(setup) and "/VERYSILENT" in cmd and "/RELAUNCH=1" in cmd

    dmg = {**mac, "installer_size": None}
    monkeypatch.setattr(httpx, "stream", lambda *a, **k: Stream(b"x" * 1000 + b"koly" + b"\0" * 508))
    assert updates.download_installer(dmg).name == "NEXUS-9.9.0.dmg"
    monkeypatch.setattr(httpx, "stream", lambda *a, **k: Stream(b"MZ" + b"\0" * 1000))
    with pytest.raises(updates.UpdateError):
        updates.download_installer(dmg)
    monkeypatch.setattr(httpx, "stream", lambda *a, **k: Stream(b"<html>"))  # not an installer (and wrong size)
    with pytest.raises(updates.UpdateError):
        updates.download_installer(info)
    monkeypatch.setattr(httpx, "stream", lambda *a, **k: Stream(b"MZ1234", host="evil.example"))
    with pytest.raises(updates.UpdateError):
        updates.download_installer(info)
    with pytest.raises(updates.UpdateError):
        updates.download_installer({**info, "installer_url": "https://evil.example/NEXUS-Setup.exe"})

    # Starting: download, then hand over to the installer once NEXUS has stopped.
    monkeypatch.setattr(updates, "FROZEN", True)
    monkeypatch.setattr(updates, "check", lambda: info)
    monkeypatch.setattr(httpx, "stream", lambda *a, **k: Stream(b"MZ1234"))
    monkeypatch.setattr(lifecycle, "restart_hook", None)
    with pytest.raises(updates.UpdateError):
        updates.start()
    restarted = []
    monkeypatch.setattr(lifecycle, "restart_hook", lambda: restarted.append(1))
    monkeypatch.setattr(lifecycle, "restart_soon", lambda: restarted.append(1) or True)
    updates.start()
    assert restarted and lifecycle.run_after_exit[0].endswith("NEXUS-Setup-9.9.0.exe")
    lifecycle.run_after_exit = None
