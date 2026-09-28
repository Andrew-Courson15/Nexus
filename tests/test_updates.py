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
    (other / "app" / "__init__.py").write_text('__version__ = "0.15.0"\n')
    _git(other, "commit", "-am", "Timeline for comics")
    _git(other, "push", "origin", "HEAD:main")
    monkeypatch.setattr(updates, "BASE_DIR", work)
    monkeypatch.setattr(lock, "LOOPBACK", lock.LOOPBACK | {"testclient"})
    monkeypatch.setattr(lock, "_LOCAL_NAMES", lock._LOCAL_NAMES | {"testserver"})
    return work


def test_check_finds_the_newer_version(client, repos):
    info = client.get("/api/update").json()
    assert info["can_update"] and info["behind"] == 1 and info["latest_version"] == "0.15.0"
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

    monkeypatch.setattr(httpx, "get", lambda *a, **k: Reply(200, {"tag_name": "v0.15.0", "html_url": "https://example/rel",
                                                                   "body": "- Timeline\n- Characters"}))
    info = updates.check_release("0.14.1")
    assert info["behind"] == 1 and info["latest_version"] == "0.15.0" and info["download_url"] == "https://example/rel"
    assert info["changes"] == ["Timeline", "Characters"] and info["can_update"] is False
    monkeypatch.setattr(httpx, "get", lambda *a, **k: Reply(200, {"tag_name": "v0.14.1"}))
    assert updates.check_release("0.14.1")["behind"] == 0
    monkeypatch.setattr(httpx, "get", lambda *a, **k: Reply(404))
    assert updates.check_release("0.14.1")["behind"] == 0
    assert updates._newer("0.14.10", "0.14.9") and not updates._newer("0.14.1", "0.14.1")
