"""Optional passcode for devices other than the computer NEXUS runs on.

With a passcode set, a phone (or any other device reaching NEXUS through Tailscale) has to
unlock NEXUS once; it then gets a signed cookie that keeps it unlocked (about half a year).
The computer NEXUS runs on (localhost) never needs the passcode, and only it can set,
change or remove the passcode, or sign every other device out.

Stored in <data>/lock.json: a PBKDF2 hash of the passcode (never the passcode itself) and
the secret that signs the cookies. Signing everyone out just replaces that secret.
Failed attempts slow down: after 5 wrong tries, each further try waits longer.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import threading
import time

from app.config import get_settings

COOKIE = "nexus_session"
SESSION_DAYS = 180
ITERATIONS = 200_000
LOOPBACK = {"127.0.0.1", "::1", "localhost"}
_LOCAL_NAMES = {"localhost", "127.0.0.1", "[::1]", "::1"}
# Endpoints a locked device may still use: is NEXUS there, and unlocking itself.
OPEN_PATHS = {"/api/ping", "/api/lock", "/api/lock/unlock"}

_lock = threading.Lock()
_failures = {"count": 0, "until": 0.0}


class LockError(ValueError):
    pass


def _file():
    return get_settings().data_dir / "lock.json"


def _read() -> dict:
    try:
        data = json.loads(_file().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) and data.get("hash") and data.get("secret") else {}
    except (OSError, ValueError):
        return {}


def _write(data: dict) -> None:
    path = _file()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    os.replace(tmp, path)


def enabled() -> bool:
    return bool(_read())


def _hash(passcode: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", passcode.encode(), bytes.fromhex(salt), ITERATIONS).hex()


def set_passcode(passcode: str) -> None:
    passcode = passcode.strip()
    if len(passcode) < 4:
        raise LockError("Use at least 4 characters.")
    if len(passcode) > 128:
        raise LockError("That's too long.")
    salt = secrets.token_hex(16)
    _write({"salt": salt, "hash": _hash(passcode, salt), "secret": secrets.token_hex(32)})


def remove_passcode() -> None:
    _file().unlink(missing_ok=True)


def sign_out_everyone() -> None:
    data = _read()
    if data:
        data["secret"] = secrets.token_hex(32)
        _write(data)


def check_passcode(passcode: str) -> str:
    """Return a session token if the passcode is right. Raises LockError (with a wait) if not."""
    data = _read()
    if not data:
        raise LockError("No passcode is set.")
    with _lock:
        wait = _failures["until"] - time.monotonic()
        if wait > 0:
            raise LockError(f"Too many wrong tries. Try again in {int(wait) + 1} seconds.")
    if not hmac.compare_digest(_hash(passcode.strip(), data["salt"]), data["hash"]):
        with _lock:
            _failures["count"] += 1
            if _failures["count"] >= 5:
                _failures["until"] = time.monotonic() + min(300, 15 * 2 ** (_failures["count"] - 5))
        raise LockError("That passcode isn't right.")
    with _lock:
        _failures.update(count=0, until=0.0)
    return make_token(data["secret"])


def make_token(secret: str) -> str:
    expires = int(time.time()) + SESSION_DAYS * 86400
    body = f"{secrets.token_hex(8)}.{expires}"
    sig = hmac.new(bytes.fromhex(secret), body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


def valid_token(token: str | None) -> bool:
    data = _read()
    if not data or not token or token.count(".") != 2:
        return False
    body, sig = token.rsplit(".", 1)
    good = hmac.new(bytes.fromhex(data["secret"]), body.encode(), hashlib.sha256).hexdigest()
    try:
        return hmac.compare_digest(sig, good) and int(body.split(".")[1]) > time.time()
    except ValueError:
        return False


def is_local(scope_client: str | None, headers) -> bool:
    """True for requests from the computer NEXUS runs on (not forwarded from another device)."""
    host = (headers.get("host") or "").rsplit(":", 1)[0] if not (headers.get("host") or "").startswith("[") \
        else (headers.get("host") or "").split("]")[0] + "]"
    forwarded = headers.get("x-forwarded-for") or headers.get("tailscale-user-login") or headers.get("forwarded")
    return host in _LOCAL_NAMES and not forwarded and (scope_client or "") in LOOPBACK


def reset_failures() -> None:  # tests
    _failures.update(count=0, until=0.0)
