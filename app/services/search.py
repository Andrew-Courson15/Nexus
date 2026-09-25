"""Search.

Version 1 uses SQLite FTS5 (porter stemming, BM25 ranking) over both user
material and AI-derived text, with a light natural-language layer that turns
words like "unfinished", "comic" or "sketches" into filters.

`SemanticSearchService` is the seam for future embedding/vector search. Today
it falls back to the full-text search.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import or_, select, text
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session, selectinload

from app.models import UNFINISHED_STATUSES, CreativeEntry, EntryTag
from app.services import text as T

FTS_TABLE = "entry_search"
_FTS_COLUMNS = (
    "title", "original_text", "user_notes", "transcript",
    "ai_summary", "ai_interpretation", "tags", "concepts",
)
# BM25 weights per column (user material and titles rank highest).
_BM25_WEIGHTS = "10.0, 6.0, 5.0, 5.0, 2.0, 1.0, 3.0, 3.0"

_fts_available: bool | None = None


def ensure_search_index(conn: Connection) -> bool:
    """Create the FTS5 index if the database supports it."""
    global _fts_available
    if conn.dialect.name != "sqlite":
        _fts_available = False
        return False
    try:
        conn.execute(text(
            f"CREATE VIRTUAL TABLE IF NOT EXISTS {FTS_TABLE} USING fts5("
            + ", ".join(_FTS_COLUMNS)
            + ", tokenize='porter unicode61')"
        ))
        _fts_available = True
    except Exception:
        _fts_available = False
    return _fts_available


def fts_available() -> bool:
    return bool(_fts_available)


def index_entry(db: Session, entry: CreativeEntry) -> None:
    """(Re)index one entry. Call after any change to searchable fields."""
    if not fts_available():
        return
    db.execute(text(f"DELETE FROM {FTS_TABLE} WHERE rowid = :id"), {"id": entry.id})
    if entry.deleted_at is not None:
        return
    meta = entry.ai_metadata or {}
    values = {
        "id": entry.id,
        "title": entry.title or "",
        "original_text": entry.original_text or "",
        "user_notes": entry.user_notes or "",
        "transcript": entry.transcript or "",
        "ai_summary": entry.ai_summary or "",
        "ai_interpretation": entry.ai_interpretation or "",
        "tags": " ".join(et.tag.name for et in entry.entry_tags),
        "concepts": " ".join(
            meta.get("concepts", []) + meta.get("themes", []) + [entry.entry_type.replace("_", " ")]
            + _visual_terms(meta.get("visual"))
        ),
    }
    cols = ", ".join(_FTS_COLUMNS)
    params = ", ".join(":" + c for c in _FTS_COLUMNS)
    db.execute(text(f"INSERT INTO {FTS_TABLE} (rowid, {cols}) VALUES (:id, {params})"), values)


def _visual_terms(visual: dict | None) -> list[str]:
    """What the AI saw in an image, so 'sketches with monsters' can find pictures."""
    if not visual:
        return []
    out: list[str] = []
    for key in ("visual_subjects", "characters", "objects", "colors"):
        out += [str(v) for v in visual.get(key) or []]
    for key in ("style", "setting"):
        if visual.get(key):
            out.append(str(visual[key]))
    return out


def remove_from_index(db: Session, entry_id: int) -> None:
    if fts_available():
        db.execute(text(f"DELETE FROM {FTS_TABLE} WHERE rowid = :id"), {"id": entry_id})


# --------------------------------------------------------------------------- #
# Natural-language query parsing
# --------------------------------------------------------------------------- #
_QUERY_STOPWORDS = T.STOPWORDS | {
    "idea", "ideas", "about", "with", "show", "find", "entries", "entry", "involving", "related",
    "regarding", "containing", "contain", "featuring", "concept", "concepts", "everything", "anything",
}
_TYPE_WORDS = {
    "comic": "comic", "manga": "comic", "movie": "movie", "film": "movie", "story": "story",
    "character": "character", "scene": "scene", "dialogue": "dialogue", "sketch": "sketch",
    "drawing": "sketch", "doodle": "sketch", "artwork": "artwork", "painting": "artwork",
    "illustration": "artwork", "worldbuilding": "worldbuilding", "research": "research", "note": "note",
}
_TYPE_PHRASES = {"short film": "short_film", "short films": "short_film", "brain dump": "brain_dump",
                 "brain dumps": "brain_dump"}
_STATUS_WORDS = {
    "unfinished": list(UNFINISHED_STATUSES), "incomplete": list(UNFINISHED_STATUSES),
    "ongoing": list(UNFINISHED_STATUSES), "wip": list(UNFINISHED_STATUSES),
    "finished": ["completed"], "completed": ["completed"], "done": ["completed"],
    "abandoned": ["abandoned"], "dropped": ["abandoned"], "archived": ["archived"],
    "raw": ["raw"], "exploring": ["exploring"], "developing": ["developing"], "active": ["active"],
}
_SOURCE_WORDS = {
    "voice": "voice", "recording": "voice", "audio": "voice", "memo": "voice",
    "image": "image", "picture": "image", "photo": "image",
}


@dataclass
class ParsedQuery:
    raw: str
    terms: list[str] = field(default_factory=list)
    entry_types: list[str] = field(default_factory=list)
    statuses: list[str] = field(default_factory=list)
    source_types: list[str] = field(default_factory=list)

    @property
    def has_filters(self) -> bool:
        return bool(self.entry_types or self.statuses or self.source_types)


def parse_query(query: str) -> ParsedQuery:
    pq = ParsedQuery(raw=query)
    low = " " + re.sub(r"\s+", " ", query.lower()) + " "
    for phrase, etype in _TYPE_PHRASES.items():
        if f" {phrase} " in low:
            pq.entry_types.append(etype)
            low = low.replace(f" {phrase} ", " ")
    for w in T.words(low):
        lw, n = w.lower(), T.normalize(w)
        if n in _TYPE_WORDS or lw in _TYPE_WORDS:
            t = _TYPE_WORDS.get(n) or _TYPE_WORDS[lw]
            if t not in pq.entry_types:
                pq.entry_types.append(t)
        elif lw in _STATUS_WORDS:
            for s in _STATUS_WORDS[lw]:
                if s not in pq.statuses:
                    pq.statuses.append(s)
        elif n in _SOURCE_WORDS:
            if _SOURCE_WORDS[n] not in pq.source_types:
                pq.source_types.append(_SOURCE_WORDS[n])
        elif lw not in _QUERY_STOPWORDS and n not in _QUERY_STOPWORDS and len(lw) > 1:
            if lw not in pq.terms:
                pq.terms.append(lw)
    return pq


# --------------------------------------------------------------------------- #
# Search services
# --------------------------------------------------------------------------- #
@dataclass
class SearchHit:
    entry: CreativeEntry
    score: float
    snippet: str | None


@dataclass
class SearchOutcome:
    parsed: ParsedQuery
    hits: list[SearchHit]
    mode: str
    relaxed: bool = False


class SearchService:
    def __init__(self, db: Session):
        self.db = db

    def search(
        self,
        query: str,
        limit: int = 50,
        entry_types: list[str] | None = None,
        statuses: list[str] | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
    ) -> SearchOutcome:
        pq = parse_query(query)
        for t in entry_types or []:
            if t not in pq.entry_types:
                pq.entry_types.append(t)
        for s in statuses or []:
            if s not in pq.statuses:
                pq.statuses.append(s)
        mode = "fts5" if fts_available() else "like"

        hits = self._run(pq, pq.terms, limit, date_from, date_to, use_filters=True)
        relaxed = False
        if not hits and pq.terms and pq.has_filters:
            # e.g. "sketches with monsters" when nothing is typed sketch: match the text alone.
            hits = self._run(pq, pq.terms, limit, date_from, date_to, use_filters=False)
            relaxed = bool(hits)
        return SearchOutcome(parsed=pq, hits=hits, mode=mode, relaxed=relaxed)

    def _base_query(self, pq: ParsedQuery, use_filters: bool, date_from, date_to):
        stmt = (
            select(CreativeEntry)
            .where(CreativeEntry.deleted_at.is_(None))
            .options(selectinload(CreativeEntry.entry_tags).selectinload(EntryTag.tag),
                     selectinload(CreativeEntry.projects))
        )
        if use_filters:
            if pq.entry_types:
                stmt = stmt.where(CreativeEntry.entry_type.in_(pq.entry_types))
            if pq.statuses:
                stmt = stmt.where(CreativeEntry.status.in_(pq.statuses))
            if pq.source_types:
                stmt = stmt.where(CreativeEntry.source_type.in_(pq.source_types))
        if date_from:
            stmt = stmt.where(CreativeEntry.created_at >= date_from)
        if date_to:
            stmt = stmt.where(CreativeEntry.created_at <= date_to)
        return stmt

    def _run(self, pq, terms, limit, date_from, date_to, use_filters) -> list[SearchHit]:
        stmt = self._base_query(pq, use_filters, date_from, date_to)
        if not terms:
            rows = self.db.scalars(stmt.order_by(CreativeEntry.created_at.desc()).limit(limit)).all()
            return [SearchHit(e, 0.0, None) for e in rows]
        if fts_available():
            return self._fts(stmt, terms, limit)
        return self._like(stmt, terms, limit)

    def _fts(self, stmt, terms, limit) -> list[SearchHit]:
        match = " OR ".join('"' + t.replace('"', "") + '"' for t in terms)
        rows = self.db.execute(
            text(
                f"SELECT rowid, bm25({FTS_TABLE}, {_BM25_WEIGHTS}) AS score, "
                f"snippet({FTS_TABLE}, 1, char(1), char(2), '…', 14) AS snip_user, "
                f"snippet({FTS_TABLE}, -1, char(1), char(2), '…', 14) AS snip_any "
                f"FROM {FTS_TABLE} WHERE {FTS_TABLE} MATCH :q ORDER BY score LIMIT 500"
            ),
            {"q": match},
        ).all()
        if not rows:
            return []
        # Prefer a snippet from the user's own words when they matched.
        ranked = {r.rowid: (r.score, r.snip_user if "\x01" in (r.snip_user or "") else r.snip_any) for r in rows}
        entries = self.db.scalars(stmt.where(CreativeEntry.id.in_(ranked))).all()
        hits = [SearchHit(e, -ranked[e.id][0], ranked[e.id][1]) for e in entries]
        hits.sort(key=lambda h: -h.score)
        return hits[:limit]

    def _like(self, stmt, terms, limit) -> list[SearchHit]:
        fields = (CreativeEntry.title, CreativeEntry.original_text, CreativeEntry.user_notes,
                  CreativeEntry.transcript, CreativeEntry.ai_summary, CreativeEntry.ai_interpretation)
        conds = [f.ilike(f"%{t}%") for t in terms for f in fields]
        rows = self.db.scalars(stmt.where(or_(*conds)).order_by(CreativeEntry.created_at.desc()).limit(limit)).all()
        return [SearchHit(e, 1.0, None) for e in rows]


class SemanticSearchService:
    """Future home of embedding-based search.

    Planned: an `EmbeddingProvider` fills `CreativeEntry.embedding_*` and a
    vector store answers similarity queries. Until then this delegates to
    full-text search so callers can already depend on this interface.
    """

    def __init__(self, db: Session, text_search: SearchService | None = None):
        self.db = db
        self.text_search = text_search or SearchService(db)

    @property
    def semantic_enabled(self) -> bool:
        return False

    def search(self, query: str, **kwargs) -> SearchOutcome:
        return self.text_search.search(query, **kwargs)
