"""Settings, tags and creative-intelligence endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import __version__
from app.config import get_settings
from app.database import get_db
from app.models import (
    ACTIVE_PROCESSING,
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
    return {"ok": True, "version": __version__}


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
        f"System: {platform.system()} {platform.release()} ({platform.machine()}), Python {sys.version.split()[0]}",
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
