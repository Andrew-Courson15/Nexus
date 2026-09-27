"""Search endpoint."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas import AskRequest, AskResponse, AskSource, SearchRequest, SearchResponse, SearchResult, entry_summary
from app.services.ask import ask
from app.services.processing import queue_embedding_backfill
from app.services.search import SemanticSearchService

router = APIRouter(prefix="/api", tags=["search"])


@router.post("/search/reindex")
def reindex(db: Session = Depends(get_db)):
    """Queue every entry whose meaning-index is missing or out of date."""
    return {"queued": queue_embedding_backfill(db)}


@router.post("/search", response_model=SearchResponse)
def search(payload: SearchRequest, db: Session = Depends(get_db)):
    service = SemanticSearchService(db)
    outcome = service.search(
        payload.query,
        limit=payload.limit,
        entry_types=[t.value for t in payload.entry_types],
        statuses=[s.value for s in payload.statuses],
        date_from=payload.date_from,
        date_to=payload.date_to,
    )
    pq = outcome.parsed
    return SearchResponse(
        query=payload.query,
        mode=outcome.mode,
        semantic_enabled=service.semantic_enabled,
        interpreted={
            "terms": pq.terms,
            "entry_types": pq.entry_types,
            "statuses": pq.statuses,
            "source_types": pq.source_types,
        },
        relaxed=outcome.relaxed,
        note=outcome.note,
        results=[SearchResult(entry=entry_summary(h.entry), score=round(h.score, 4), snippet=h.snippet,
                              matched_by=h.matched_by, similarity=h.similarity)
                 for h in outcome.hits],
    )


@router.post("/ask", response_model=AskResponse)
def ask_nexus(payload: AskRequest, db: Session = Depends(get_db)):
    """Answer a question from the archive, citing the entries used. Read-only."""
    a = ask(db, payload.question)
    cited = set(a.cited_ids)
    return AskResponse(question=a.question, answer=a.answer, provider=a.provider, note=a.note,
                       sources=[AskSource(entry=entry_summary(e), cited=e.id in cited) for e in a.sources])
