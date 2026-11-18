"""Start NEXUS when you sign in: the Mac login item (Windows uses a shortcut made by PowerShell)."""
import plistlib

import pytest

from app.services import autostart, lock


@pytest.fixture
def here(monkeypatch):
    monkeypatch.setattr(lock, "LOOPBACK", lock.LOOPBACK | {"testclient"})
    monkeypatch.setattr(lock, "_LOCAL_NAMES", lock._LOCAL_NAMES | {"testserver"})


def test_mac_login_item(client, here, tmp_path, monkeypatch):
    monkeypatch.setattr(autostart.sys, "platform", "darwin")
    monkeypatch.setattr(autostart.Path, "home", lambda: tmp_path)
    assert client.get("/api/autostart").json() == {"supported": True, "enabled": False}
    assert client.put("/api/autostart", json={"enabled": True}).json()["enabled"] is True
    item = plistlib.loads((tmp_path / "Library" / "LaunchAgents" / "local.nexus.plist").read_bytes())
    assert item["Label"] == "local.nexus" and item["RunAtLoad"] and item["ProgramArguments"][-1] == "run.py"
    assert client.put("/api/autostart", json={"enabled": False}).json()["enabled"] is False
    assert not (tmp_path / "Library" / "LaunchAgents" / "local.nexus.plist").exists()


def test_elsewhere_and_other_devices(client, here, monkeypatch):
    monkeypatch.setattr(autostart.sys, "platform", "linux")
    assert client.get("/api/autostart").json() == {"supported": False, "enabled": False}
    assert client.put("/api/autostart", json={"enabled": True}).status_code == 400
    monkeypatch.setattr(lock, "LOOPBACK", {"127.0.0.1"})
    assert client.get("/api/autostart").status_code == 403
