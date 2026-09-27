"""Visual Art Intelligence endpoints: analyses, gallery, evolution, creative memory, art library."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.database import get_db
from app.models import (
    ArtKnowledge,
    ArtPreference,
    ArtworkAnalysis,
    Attachment,
    CreativeEntry,
    EntryTag,
    utcnow,
)
from app.routes.entries import load_entry
from app.schemas import (
    ArtAnalysisCreate,
    ArtAnalysisOut,
    ArtImage,
    ArtKnowledgeCreate,
    ArtKnowledgeOut,
    ArtPreferenceCreate,
    ArtPreferenceOut,
    ArtPreferenceReview,
    EntrySummary,
    GalleryItem,
    entry_summary,
)
from app.services import art

router = APIRouter(prefix="/api", tags=["art"])


def _images(db: Session, ids: list[int]) -> list[ArtImage]:
    rows = db.scalars(select(CreativeEntry).where(CreativeEntry.id.in_(ids))
                      .options(selectinload(CreativeEntry.attachments))).all()
    by_id = {e.id: e for e in rows}
    out = []
    for i in ids:
        e = by_id.get(i)
        if e is None:
            continue
        img = art.entry_image(e)
        out.append(ArtImage(entry_id=e.id, title=e.title or "Untitled",
                            url=f"/api/uploads/{img.id}/preview" if img else None))
    return out


def _out(db: Session, a: ArtworkAnalysis) -> ArtAnalysisOut:
    data = ArtAnalysisOut.model_validate(a, from_attributes=True)
    data.images = _images(db, [a.entry_id] + list(a.related_entry_ids or []))
    return data


# -- analyses -----------------------------------------------------------------
@router.post("/entries/{entry_id}/art", response_model=ArtAnalysisOut, status_code=202)
def analyze_art(entry_id: int, payload: ArtAnalysisCreate, response: Response, db: Session = Depends(get_db)):
    """Start a Visual Art Intelligence analysis (a new version; earlier ones are kept)."""
    entry = load_entry(db, entry_id)
    try:
        analysis = art.start_analysis(db, entry, payload.mode.value, payload.related_entry_ids, payload.question)
    except art.ArtError as exc:
        raise HTTPException(400, str(exc)) from exc
    analysis = db.get(ArtworkAnalysis, analysis.id)
    if analysis.status in ("done", "failed"):
        response.status_code = 200
    return _out(db, analysis)


@router.get("/entries/{entry_id}/art", response_model=list[ArtAnalysisOut])
def list_art(entry_id: int, db: Session = Depends(get_db)):
    load_entry(db, entry_id)
    rows = db.scalars(select(ArtworkAnalysis).where(ArtworkAnalysis.entry_id == entry_id)
                      .order_by(ArtworkAnalysis.analysis_version.desc())).all()
    return [_out(db, a) for a in rows]


@router.get("/art/analyses/{analysis_id}", response_model=ArtAnalysisOut)
def get_art(analysis_id: int, db: Session = Depends(get_db)):
    a = db.get(ArtworkAnalysis, analysis_id)
    if a is None:
        raise HTTPException(404, "Analysis not found")
    return _out(db, a)


@router.get("/art/gallery", response_model=list[GalleryItem])
def gallery(db: Session = Depends(get_db)):
    """Every artwork (entries with an image), newest first."""
    entries = db.scalars(
        select(CreativeEntry)
        .where(CreativeEntry.deleted_at.is_(None),
               CreativeEntry.id.in_(select(Attachment.entry_id).where(Attachment.kind == "image")))
        .options(selectinload(CreativeEntry.entry_tags).selectinload(EntryTag.tag),
                 selectinload(CreativeEntry.projects), selectinload(CreativeEntry.attachments))
        .order_by(CreativeEntry.created_at.desc())
    ).all()
    latest = {}
    counts = dict(db.execute(select(ArtworkAnalysis.entry_id, func.count()).group_by(ArtworkAnalysis.entry_id)).all())
    for a in db.scalars(select(ArtworkAnalysis).order_by(ArtworkAnalysis.analysis_version)):
        latest[a.entry_id] = a
    return [GalleryItem(entry=entry_summary(e), analyses=counts.get(e.id, 0),
                        latest_status=latest[e.id].status if e.id in latest else None,
                        latest_analysis_id=latest[e.id].id if e.id in latest else None) for e in entries]


@router.get("/entries/{entry_id}/evolution", response_model=list[EntrySummary])
def evolution(entry_id: int, db: Session = Depends(get_db)):
    """How a piece developed: every version linked by 'evolved from', oldest first."""
    load_entry(db, entry_id)
    chain = art.evolution_chain(db, entry_id)
    full = {e.id: e for e in db.scalars(select(CreativeEntry).where(CreativeEntry.id.in_([c.id for c in chain]))
                                        .options(selectinload(CreativeEntry.entry_tags).selectinload(EntryTag.tag),
                                                 selectinload(CreativeEntry.projects),
                                                 selectinload(CreativeEntry.attachments)))}
    return [entry_summary(full[c.id]) for c in chain]


# -- creative memory ----------------------------------------------------------
def _pref_out(db: Session, p: ArtPreference) -> ArtPreferenceOut:
    return ArtPreferenceOut(id=p.id, statement=p.statement, origin=p.origin, status=p.status,
                            evidence=_images(db, list(p.evidence or [])), created_at=p.created_at)


@router.get("/art/preferences", response_model=list[ArtPreferenceOut])
def list_preferences(db: Session = Depends(get_db)):
    rows = db.scalars(select(ArtPreference).where(ArtPreference.status != "rejected")
                      .order_by(ArtPreference.status, ArtPreference.id)).all()
    return [_pref_out(db, p) for p in rows]


@router.post("/art/preferences", response_model=ArtPreferenceOut, status_code=201)
def add_preference(payload: ArtPreferenceCreate, db: Session = Depends(get_db)):
    """A preference the artist states directly (confirmed by definition)."""
    text = " ".join(payload.statement.split())
    existing = db.scalar(select(ArtPreference).where(func.lower(ArtPreference.statement) == text.lower()))
    if existing:
        existing.status, existing.origin = "confirmed", existing.origin
        db.commit()
        return _pref_out(db, existing)
    p = ArtPreference(statement=text, origin="user", status="confirmed")
    db.add(p)
    db.commit()
    return _pref_out(db, p)


@router.patch("/art/preferences/{pref_id}", response_model=ArtPreferenceOut)
def review_preference(pref_id: int, payload: ArtPreferenceReview, db: Session = Depends(get_db)):
    """Confirm an observed pattern, or reject it (rejected patterns are never suggested again)."""
    p = db.get(ArtPreference, pref_id)
    if p is None:
        raise HTTPException(404, "Preference not found")
    p.status = payload.status
    db.commit()
    return _pref_out(db, p)


# -- art library --------------------------------------------------------------
def _k_out(k: ArtKnowledge) -> ArtKnowledgeOut:
    return ArtKnowledgeOut(id=k.id, title=k.title, topic=k.topic, source=k.source, content=k.content,
                           passages=len(k.chunks), created_at=k.created_at, updated_at=k.updated_at)


def _topic(topic: str) -> str:
    t = (topic or "other").strip().lower()
    if t not in art.ART_TOPICS:
        raise HTTPException(422, f"Topic must be one of: {', '.join(art.ART_TOPICS)}")
    return t


@router.get("/art/knowledge", response_model=list[ArtKnowledgeOut])
def list_knowledge(db: Session = Depends(get_db)):
    rows = db.scalars(select(ArtKnowledge).where(ArtKnowledge.deleted_at.is_(None))
                      .options(selectinload(ArtKnowledge.chunks)).order_by(ArtKnowledge.topic, ArtKnowledge.title))
    return [_k_out(k) for k in rows]


@router.get("/art/topics")
def topics():
    return {"topics": art.ART_TOPICS, "modes": list(art.MODE_INSTRUCTIONS), "style_signals": art.STYLE_SIGNALS}


@router.post("/art/knowledge", response_model=ArtKnowledgeOut, status_code=201)
def add_knowledge(payload: ArtKnowledgeCreate, db: Session = Depends(get_db)):
    k = ArtKnowledge(title=payload.title.strip(), topic=_topic(payload.topic),
                     source=(payload.source or "").strip() or None, content=payload.content)
    db.add(k)
    db.flush()
    art.index_knowledge(db, k)
    db.commit()
    return _k_out(k)


@router.put("/art/knowledge/{kid}", response_model=ArtKnowledgeOut)
def update_knowledge(kid: int, payload: ArtKnowledgeCreate, db: Session = Depends(get_db)):
    k = db.get(ArtKnowledge, kid)
    if k is None or k.deleted_at is not None:
        raise HTTPException(404, "Not found")
    k.title, k.topic = payload.title.strip(), _topic(payload.topic)
    k.source, k.content = (payload.source or "").strip() or None, payload.content
    art.index_knowledge(db, k)
    db.commit()
    return _k_out(k)


@router.delete("/art/knowledge/{kid}", status_code=204)
def delete_knowledge(kid: int, db: Session = Depends(get_db)):
    """Hidden, not destroyed (like entries)."""
    k = db.get(ArtKnowledge, kid)
    if k is None or k.deleted_at is not None:
        raise HTTPException(404, "Not found")
    k.deleted_at = utcnow()
    db.commit()
    return Response(status_code=204)
