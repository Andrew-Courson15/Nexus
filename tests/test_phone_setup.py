"""Settings → Your phone: turning on private phone access from inside the app (Tailscale)."""
import stat
import sys

import pytest

from app.services import lock, phone


@pytest.fixture
def here(monkeypatch):
    monkeypatch.setattr(lock, "LOOPBACK", lock.LOOPBACK | {"testclient"})
    monkeypatch.setattr(lock, "_LOCAL_NAMES", lock._LOCAL_NAMES | {"testserver"})


def fake_tailscale(tmp_path, monkeypatch, state="Running", dns="gaming-pc.tail1234.ts.net.", serve_ok=True):
    script = tmp_path / "tailscale"
    log = tmp_path / "calls.txt"
    script.write_text(f"""#!{sys.executable}
import json, sys
open({str(log)!r}, "a").write(" ".join(sys.argv[1:]) + "\\n")
if sys.argv[1] == "status":
    print(json.dumps({{"BackendState": {state!r}, "Self": {{"DNSName": {dns!r}}}}}))
elif sys.argv[1] == "serve":
    sys.exit(0 if {serve_ok!r} else 1)
""")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setattr(phone, "find_tailscale", lambda: str(script))
    return log


def test_turning_on_phone_access(client, here, tmp_path, monkeypatch):
    log = fake_tailscale(tmp_path, monkeypatch)
    st = client.get("/api/phone-setup").json()
    assert st["next"] == "turn_on" and st["name"] == "gaming-pc.tail1234.ts.net"
    # Before: NEXUS doesn't answer to that name.
    assert client.get("/api/ping", headers={"host": "gaming-pc.tail1234.ts.net"}).status_code == 400
    r = client.post("/api/phone-setup").json()
    assert r["url"] == "https://gaming-pc.tail1234.ts.net"
    assert "serve --bg" in log.read_text()
    # After, straight away: it answers to it, and Settings shows the address as a QR code.
    assert client.get("/api/ping", headers={"host": "gaming-pc.tail1234.ts.net"}).status_code == 200
    link = client.get("/api/phone-link").json()
    assert link["url"] == "https://gaming-pc.tail1234.ts.net" and link["qr_svg"].startswith("<svg")


def test_not_installed_or_not_signed_in(client, here, tmp_path, monkeypatch):
    monkeypatch.setattr(phone, "find_tailscale", lambda: None)
    assert client.get("/api/phone-setup").json()["next"] == "install"
    assert "isn't installed" in client.post("/api/phone-setup").json()["detail"]
    fake_tailscale(tmp_path, monkeypatch, state="NeedsLogin")
    assert client.get("/api/phone-setup").json()["next"] == "sign_in"
    assert client.post("/api/phone-setup").status_code == 400


def test_serve_failure_is_explained(client, here, tmp_path, monkeypatch):
    fake_tailscale(tmp_path, monkeypatch, serve_ok=False)
    r = client.post("/api/phone-setup")
    assert r.status_code == 400 and "HTTPS Certificates" in r.json()["detail"]


def test_only_on_this_computer(client, tmp_path, monkeypatch):
    fake_tailscale(tmp_path, monkeypatch)
    assert client.get("/api/phone-setup").status_code == 403
    assert client.post("/api/phone-setup").status_code == 403


def test_a_damaged_phone_file_is_ignored(client, here):
    from app.config import get_settings

    (get_settings().data_dir / "phone.json").write_text("{not json")
    get_settings.cache_clear()
    assert client.get("/api/ping").status_code == 200
