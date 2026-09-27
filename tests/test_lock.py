"""Optional passcode for devices other than the computer NEXUS runs on."""
import pytest

from app.services import lock

HERE = {"host": "localhost"}                                          # the PC itself
PHONE = {"host": "localhost", "x-forwarded-for": "100.64.0.7"}        # via Tailscale serve


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.setattr(lock, "LOOPBACK", lock.LOOPBACK | {"testclient"})  # the test client counts as this computer
    lock.remove_passcode()
    lock.reset_failures()
    yield
    lock.remove_passcode()
    lock.reset_failures()


def test_without_a_passcode_everything_is_open(client):
    assert client.get("/api/entries", headers=PHONE).status_code == 200
    assert client.get("/api/lock", headers=PHONE).json() == {"enabled": False, "this_computer": False, "unlocked": True}


def test_other_devices_must_unlock_once(client):
    assert client.put("/api/lock/passcode", json={"passcode": "moon42"}, headers=HERE).status_code == 200
    # the PC itself never needs it
    assert client.get("/api/entries", headers=HERE).status_code == 200
    # a phone is locked out of everything but ping / unlock
    r = client.get("/api/entries", headers=PHONE)
    assert r.status_code == 401 and r.json()["locked"] is True
    assert client.get("/api/uploads/1/thumb", headers=PHONE).status_code == 401
    assert client.get("/api/ping", headers=PHONE).status_code == 200
    assert client.get("/", headers=PHONE).status_code == 200  # the app shell loads, to show the unlock screen
    assert client.post("/api/lock/unlock", json={"passcode": "nope"}, headers=PHONE).status_code == 401
    r = client.post("/api/lock/unlock", json={"passcode": "moon42"}, headers=PHONE)
    assert r.status_code == 200 and "httponly" in r.headers["set-cookie"].lower()
    assert client.get("/api/entries", headers=PHONE).status_code == 200  # cookie kept by the client
    assert client.get("/api/lock", headers=PHONE).json()["unlocked"] is True


def test_signing_everyone_out_and_removing_the_passcode(client):
    client.put("/api/lock/passcode", json={"passcode": "moon42"}, headers=HERE)
    client.post("/api/lock/unlock", json={"passcode": "moon42"}, headers=PHONE)
    assert client.get("/api/entries", headers=PHONE).status_code == 200
    client.post("/api/lock/sign-out-all", headers=HERE)
    assert client.get("/api/entries", headers=PHONE).status_code == 401  # old cookie no longer works
    client.delete("/api/lock/passcode", headers=HERE)
    assert client.get("/api/entries", headers=PHONE).status_code == 200


def test_only_this_computer_can_change_the_passcode(client):
    assert client.put("/api/lock/passcode", json={"passcode": "evil1"}, headers=PHONE).status_code == 403
    client.put("/api/lock/passcode", json={"passcode": "moon42"}, headers=HERE)
    client.post("/api/lock/unlock", json={"passcode": "moon42"}, headers=PHONE)
    assert client.delete("/api/lock/passcode", headers=PHONE).status_code == 403
    assert client.post("/api/lock/sign-out-all", headers=PHONE).status_code == 403
    assert client.put("/api/lock/passcode", json={"passcode": "abc"}, headers=HERE).status_code == 400  # too short


def test_guessing_is_slowed_down(client):
    client.put("/api/lock/passcode", json={"passcode": "moon42"}, headers=HERE)
    for _ in range(5):
        client.post("/api/lock/unlock", json={"passcode": "wrong"}, headers=PHONE)
    r = client.post("/api/lock/unlock", json={"passcode": "moon42"}, headers=PHONE)  # even the right one waits
    assert r.status_code == 401 and "Try again in" in r.json()["detail"]


def test_forged_or_expired_cookies_do_not_unlock(client, monkeypatch):
    client.put("/api/lock/passcode", json={"passcode": "moon42"}, headers=HERE)
    client.cookies.set(lock.COOKIE, "abc.9999999999.deadbeef")
    assert client.get("/api/entries", headers=PHONE).status_code == 401
    monkeypatch.setattr(lock, "SESSION_DAYS", -1)
    client.cookies.clear()
    client.post("/api/lock/unlock", json={"passcode": "moon42"}, headers=PHONE)
    assert client.get("/api/entries", headers=PHONE).status_code == 401
    client.cookies.clear()
