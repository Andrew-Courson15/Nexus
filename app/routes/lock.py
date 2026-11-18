"""Passcode lock for other devices. See app/services/lock.py."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from app.services import lock

router = APIRouter(prefix="/api/lock", tags=["lock"])


class Passcode(BaseModel):
    passcode: str = Field(max_length=128)


def _local(request: Request) -> bool:
    return lock.is_local(request.client.host if request.client else None, request.headers)


def _only_here(request: Request) -> None:
    if not _local(request):
        raise HTTPException(403, "For safety, the passcode can only be changed on the computer NEXUS runs on.")


@router.get("")
def status(request: Request):
    here = _local(request)
    return {"enabled": lock.enabled(), "this_computer": here,
            "unlocked": here or not lock.enabled() or lock.valid_token(request.cookies.get(lock.COOKIE))}


@router.post("/unlock")
def unlock(payload: Passcode, request: Request, response: Response):
    try:
        token = lock.check_passcode(payload.passcode)
    except lock.LockError as exc:
        raise HTTPException(401, str(exc)) from exc
    https = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
    response.set_cookie(lock.COOKIE, token, max_age=lock.SESSION_DAYS * 86400, httponly=True,
                        samesite="strict", secure=https, path="/")
    return {"unlocked": True}


@router.post("/sign-out")
def sign_out(response: Response):
    """Lock this device again."""
    response.delete_cookie(lock.COOKIE, path="/")
    return {"unlocked": False}


@router.put("/passcode")
def set_passcode(payload: Passcode, request: Request):
    _only_here(request)
    try:
        lock.set_passcode(payload.passcode)
    except lock.LockError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"enabled": True}


@router.delete("/passcode")
def remove_passcode(request: Request):
    _only_here(request)
    lock.remove_passcode()
    return {"enabled": False}


@router.post("/sign-out-all")
def sign_out_all(request: Request):
    """Every other device has to enter the passcode again."""
    _only_here(request)
    lock.sign_out_everyone()
    return {"ok": True}
