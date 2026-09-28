"""Settings, tags and creative-intelligence endpoints."""
from __future__ import annotations

import re
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import __version__
from app.config import get_settings
from app.database import get_db
from app.models import (
    ACTIVE_PROCESSING,
    UNFINISHED_STATUSES,
    CreativeEntry,
    EntryStatus,
    EntryTag,
    EntryType,
    Project,
    ProjectStatus,
    ProjectType,
    RelationshipType,
    Tag,
)
from app.services import ai, embeddings, transcription
from app.services.insights import find_patterns
from app.services.search import fts_available

router = APIRouter(prefix="/api", tags=["system"])


@router.get("/ping")
def ping():
    """Tiny, instant reply so the phone app can tell quickly whether NEXUS is reachable (and which version)."""
    from app.config import FROZEN

    return {"ok": True, "version": __version__, "app": FROZEN}


@router.post("/restart", status_code=202)
def restart(request: Request):
    """Restart NEXUS (the downloadable app; on the computer NEXUS runs on only)."""
    from app.services import lifecycle

    _only_here(request, "NEXUS is restarted on the computer it runs on.")
    if not lifecycle.restart_soon():
        raise HTTPException(400, "Close NEXUS and open it again.")
    return {"restarting": True}


@router.get("/settings")
def read_settings(db: Session = Depends(get_db)):
    """Read-only view of the configuration. Secrets are never returned."""
    s = get_settings()
    try:
        provider = ai.get_ai_provider()
        provider_info = {"name": provider.name, "external": provider.external, "error": None,
                         "health": provider.health()}
    except ai.AIProviderError as exc:
        provider_info = {"name": s.ai_provider, "external": None, "error": str(exc),
                         "health": {"ok": False, "detail": str(exc)}}
    t = transcription.get_transcription_provider()
    active = CreativeEntry.deleted_at.is_(None)
    return {
        "ai_provider": {**provider_info, "configured": s.ai_provider, "available": sorted(ai.PROVIDERS)},
        "transcription_provider": {"name": t.name, "external": t.external, "health": t.health(),
                                   "available": sorted(transcription.PROVIDERS)},
        "processing": {"mode": s.processing_mode,
                       "active": db.scalar(select(func.count()).where(
                           active, CreativeEntry.processing_status.in_(ACTIVE_PROCESSING))) or 0},
        "search": _search_info(db),
        "storage": {"data_dir": str(s.data_dir), "database": s.database_url.split("://")[0],
                    "max_upload_mb": s.max_upload_mb},
        "server": {"host": s.host, "port": s.port},
        "owner_name": s.owner_name,
        "counts": {
            "entries": db.scalar(select(func.count()).where(active)) or 0,
            "projects": db.scalar(select(func.count(Project.id)).where(Project.deleted_at.is_(None))) or 0,
            "tags": db.scalar(select(func.count(Tag.id))) or 0,
            "trash": db.scalar(select(func.count()).where(CreativeEntry.deleted_at.is_not(None))) or 0,
        },
        "vocab": {
            "entry_types": [e.value for e in EntryType],
            "statuses": [e.value for e in EntryStatus],
            "project_types": [e.value for e in ProjectType],
            "project_statuses": [e.value for e in ProjectStatus],
            "relationship_types": [e.value for e in RelationshipType],
        },
    }


def _search_info(db: Session) -> dict:
    provider = embeddings.get_embedding_provider()
    health = provider.health()
    info = {"mode": "fts5" if fts_available() else "like", "semantic_enabled": bool(health.get("ok")),
            "embedding_provider": provider.name, "embedding_model": provider.model, "health": health}
    if provider.name != "none":
        info["index"] = embeddings.index_stats(db, provider.model)
    return info


@router.get("/tags")
def list_tags(db: Session = Depends(get_db)):
    rows = db.execute(
        select(Tag.name, func.count(EntryTag.entry_id))
        .join(EntryTag, EntryTag.tag_id == Tag.id)
        .join(CreativeEntry, CreativeEntry.id == EntryTag.entry_id)
        .where(CreativeEntry.deleted_at.is_(None))
        .group_by(Tag.name)
        .order_by(func.count(EntryTag.entry_id).desc(), Tag.name)
    ).all()
    return [{"name": n, "count": c} for n, c in rows]


@router.get("/insights/patterns")
def patterns(min_count: int = Query(3, ge=2, le=100), db: Session = Depends(get_db)):
    return {"patterns": find_patterns(db, min_count=min_count),
            "note": "Patterns are AI inferences computed from your entries; each lists its evidence."}


@router.get("/preferences")
def get_preferences():
    """Everyday settings that can be changed in the app (Settings > Preferences)."""
    from app.services import preferences

    return preferences.describe()


@router.put("/preferences")
def put_preferences(changes: dict):
    """Save preferences (null resets one to .env / the default). They apply straight away."""
    from app.services import preferences

    try:
        return preferences.update(changes)
    except preferences.PreferenceError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/export/markdown")
def export_markdown(db: Session = Depends(get_db)):
    """Everything as readable files: a Markdown file per entry and project, plus every original file."""
    from fastapi.responses import FileResponse
    from starlette.background import BackgroundTask

    from app.services.markdown_export import export_markdown as build

    path, name = build(db)
    return FileResponse(path, media_type="application/zip", filename=name,
                        background=BackgroundTask(lambda: path.unlink(missing_ok=True)))


@router.get("/phone-link")
def phone_link():
    """The private address a phone opens (set up by the phone setup script), with a QR code to scan."""
    import sys

    s = get_settings()
    names = [h for h in s.allowed_hosts if h.endswith(".ts.net")]
    script = ("./scripts/phone_setup_mac.sh" if sys.platform == "darwin"
              else "powershell -ExecutionPolicy Bypass -File scripts\\phone_setup_windows.ps1")
    if not names:
        return {"url": None, "setup_command": script}
    url = f"https://{names[-1]}"
    try:
        import io

        import qrcode
        import qrcode.image.svg

        buf = io.BytesIO()
        qrcode.make(url, image_factory=qrcode.image.svg.SvgPathImage, box_size=10, border=2).save(buf)
        svg = buf.getvalue().decode()
        svg = svg[svg.index("<svg"):]  # drop the XML declaration so it can sit inside the page
    except Exception:  # the QR code is a convenience; the address still works
        svg = None
    return {"url": url, "qr_svg": svg, "setup_command": script}


@router.post("/models/pull", status_code=202)
def pull_model(payload: dict):
    """Download an Ollama model in the background (progress: GET /api/models/pulls)."""
    from app.services import preferences

    try:
        return preferences.start_pull(payload.get("name", ""))
    except preferences.PreferenceError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/models/pulls")
def model_pulls():
    from app.services import preferences

    return preferences.pulls()


LOG_LINES = 80


def _log_tail(path, lines: int = LOG_LINES) -> list[str]:
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 64_000))
            return f.read().decode("utf-8", "replace").splitlines()[-lines:]
    except OSError:
        return []


@router.get("/problem-report")
def problem_report(db: Session = Depends(get_db)):
    """A plain-text report for getting help when something goes wrong. Nothing is sent anywhere:
    the app shows it, and you decide whether to copy it. It holds versions, settings health,
    counts and recent log lines, never your entries' text."""
    import platform
    import sys

    s = get_settings()
    info = read_settings(db)
    active = CreativeEntry.deleted_at.is_(None)
    by_status = dict(db.execute(select(CreativeEntry.processing_status, func.count())
                                .where(active).group_by(CreativeEntry.processing_status)).all())
    search = info["search"]
    lines = [
        f"NEXUS {__version__}",
        f"System: {platform.system()} {platform.release()} ({platform.machine()}), Python {sys.version.split()[0]}"
        f"{', packaged app' if getattr(sys, 'frozen', False) else ''}",
        f"AI: {info['ai_provider']['name']} - {info['ai_provider']['health'].get('detail') or info['ai_provider']['health']}",
        f"Picture model: {s.ollama_vision_model or '(same as AI)'} | AI model: {s.ollama_model}",
        f"Speech to text: {info['transcription_provider']['name']} - {info['transcription_provider']['health'].get('detail', '')}",
        f"Search: {search['mode']}, by meaning: {'on' if search['semantic_enabled'] else 'off'} "
        f"({search['embedding_provider']} {search['embedding_model'] or ''}".rstrip() + ")",
        f"Entries: {info['counts']['entries']}, projects: {info['counts']['projects']}, trash: {info['counts']['trash']}",
        "Processing: " + (", ".join(f"{k or 'not needed'}={v}" for k, v in sorted(by_status.items(), key=lambda x: str(x[0])))
                          or "nothing"),
        f"Port: {s.port}, uploads up to {s.max_upload_mb} MB",
    ]
    tail = _log_tail(s.data_dir / "nexus.log")
    lines += ["", f"Last {len(tail)} lines of nexus.log:" if tail else "No nexus.log (NEXUS was started in a window).", *tail]
    return {"text": "\n".join(lines)}


@router.get("/insights/rediscover")
def rediscover(today: date | None = None, tz_offset: int = Query(0, ge=-900, le=900), db: Session = Depends(get_db)):
    """Old ideas worth another look: ones captured on this day of an earlier month or year, and a
    few unfinished ideas nobody has touched for a month (a different few each day)."""
    import random
    from datetime import timedelta

    from app.routes.entries import _LOAD
    from app.schemas import entry_summary

    shift = timedelta(minutes=-tz_offset)  # the browser's getTimezoneOffset(): local = UTC - offset
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    today = today or (now + shift).date()
    rows = db.execute(select(CreativeEntry.id, CreativeEntry.created_at, CreativeEntry.updated_at, CreativeEntry.status)
                      .where(CreativeEntry.deleted_at.is_(None)).order_by(CreativeEntry.created_at.desc())).all()

    def local(dt):
        return (dt.replace(tzinfo=None) + shift).date()

    on_this_day = [r.id for r in rows if (d := local(r.created_at)).day == today.day and d < today.replace(day=1)][:12]
    seen = set(on_this_day)
    stale = sorted(r.id for r in rows if r.id not in seen and r.status in UNFINISHED_STATUSES
                   and r.updated_at.replace(tzinfo=None) < now - timedelta(days=30))
    resurfaced = random.Random(today.isoformat()).sample(stale, min(3, len(stale)))
    full = {e.id: e for e in db.scalars(select(CreativeEntry).where(CreativeEntry.id.in_(on_this_day + resurfaced))
                                        .options(*_LOAD[:3]))}
    on_this_day = [full[i] for i in on_this_day]
    resurfaced = [full[i] for i in resurfaced]
    return {"today": today.isoformat(),
            "on_this_day": [entry_summary(e).model_dump(mode="json") for e in on_this_day],
            "resurfaced": [entry_summary(e).model_dump(mode="json") for e in resurfaced]}


def _only_here(request: Request, why: str = "Updates are checked and started on the computer NEXUS runs on.") -> None:
    from app.services import lock

    if not lock.is_local(request.client.host if request.client else None, request.headers):
        raise HTTPException(403, why)


@router.get("/update")
def update_check(request: Request):
    """Is a newer NEXUS available? (Asks git; on the computer NEXUS runs on only.)"""
    from app.services import updates

    _only_here(request)
    try:
        return updates.check()
    except updates.UpdateError as exc:
        return {"can_update": False, "current_version": __version__, "reason": str(exc)}


@router.post("/update", status_code=202)
def update_start(request: Request):
    """Stop, update, set up and restart NEXUS in the background (the update script)."""
    from app.services import updates

    _only_here(request)
    try:
        updates.start()
    except updates.UpdateError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"started": True, "from_version": __version__}


@router.get("/whats-new")
def whats_new(since: str | None = None):
    """Changelog notes for this version and every one after `since` (the version this device last
    saw), shown once on Home after an update."""
    from app.config import BASE_DIR

    try:
        text_ = (BASE_DIR / "CHANGELOG.md").read_text(encoding="utf-8")
    except OSError:
        return {"version": __version__, "items": []}
    items: list[str] = []
    started = False
    for block in re.split(r"^## ", text_, flags=re.M)[1:]:
        version = block.split()[0]
        if version == __version__:
            started = True
        if not started:
            continue
        if since and version == since:
            break
        current = None
        for line in block.splitlines()[1:]:
            if line.startswith("- "):
                current = line[2:].strip()
                items.append(current)
            elif line.startswith("  ") and current is not None and line.strip():
                items[-1] = f"{items[-1]} {line.strip()}"
        if not since:
            break
    return {"version": __version__, "items": items}


@router.get("/phone-setup")
def phone_setup_status(request: Request):
    """Is Tailscale ready for phone access? (On the computer NEXUS runs on only.)"""
    from app.services import phone

    _only_here(request, "Phone access is set up on the computer NEXUS runs on.")
    try:
        return phone.status()
    except phone.PhoneSetupError as exc:
        return {"installed": True, "running": False, "name": None, "next": "error", "error": str(exc)}


@router.post("/phone-setup")
def phone_setup_turn_on(request: Request):
    """Turn on the private phone link (the same steps as the phone setup script)."""
    from app.services import phone

    _only_here(request, "Phone access is set up on the computer NEXUS runs on.")
    try:
        return phone.turn_on()
    except phone.PhoneSetupError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/autostart")
def autostart_get(request: Request):
    """Does NEXUS start when you sign in to this computer?"""
    from app.services import autostart

    _only_here(request, "This is set on the computer NEXUS runs on.")
    return {"supported": autostart.supported(), "enabled": autostart.enabled()}


@router.put("/autostart")
def autostart_set(payload: dict, request: Request):
    from app.services import autostart

    _only_here(request, "This is set on the computer NEXUS runs on.")
    if not autostart.supported():
        raise HTTPException(400, "Starting with the computer is available on Windows and Mac.")
    autostart.set_enabled(bool(payload.get("enabled")))
    return {"supported": True, "enabled": autostart.enabled()}
