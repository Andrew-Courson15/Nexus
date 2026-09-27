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
        "user_notes": entry.combined_notes,
        "transcript": entry.transcript or "",
        "ai_summary": entry.ai_summary or "",
        "ai_interpretation": entry.ai_interpretation or "",
        "tags": " ".join(et.tag.name for et in entry.entry_tags),
        "concepts": " ".join(
            meta.get("concepts", []) + meta.get("themes", []) + [entry.entry_type.replace("_", " ")]
            + visual_terms(meta.get("visual"))
        ),
    }
    cols = ", ".join(_FTS_COLUMNS)
    params = ", ".join(":" + c for c in _FTS_COLUMNS)
    db.execute(text(f"INSERT INTO {FTS_TABLE} (rowid, {cols}) VALUES (:id, {params})"), values)


def visual_terms(visual: dict | None) -> list[str]:
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
    matched_by: list[str] = field(default_factory=lambda: ["words"])  # words | meaning | filters
    similarity: float | None = None


@dataclass
class SearchOutcome:
    parsed: ParsedQuery
    hits: list[SearchHit]
    mode: str
    relaxed: bool = False
    note: str | None = None


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
            .options(selectinload(CreativeEntry.entry_tags).selectinload(EntryTag.tag),
                     selectinload(CreativeEntry.projects), selectinload(CreativeEntry.attachments))
        )
        return self.apply_filters(stmt, pq, use_filters, date_from, date_to)

    @staticmethod
    def apply_filters(stmt, pq: ParsedQuery, use_filters: bool, date_from, date_to):
        stmt = stmt.where(CreativeEntry.deleted_at.is_(None))
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
            return [SearchHit(e, 0.0, None, ["filters"]) for e in rows]
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
    """Hybrid search: word matches (FTS5) + meaning matches (embeddings).

    The two ranked lists are merged with reciprocal-rank fusion, so an entry
    found both ways rises to the top. If the embedding model isn't available,
    this quietly returns the word-only results with a note explaining why.
    """

    RRF_K = 60
    MAX_MEANING_HITS = 20

    def __init__(self, db: Session, text_search: SearchService | None = None, provider=None):
        from app.services.embeddings import get_embedding_provider

        self.db = db
        self.text_search = text_search or SearchService(db)
        self.provider = provider or get_embedding_provider()

    @property
    def semantic_enabled(self) -> bool:
        return self.provider.name != "none"

    def search(self, query: str, limit: int = 50, entry_types: list[str] | None = None,
               statuses: list[str] | None = None, date_from: datetime | None = None,
               date_to: datetime | None = None) -> SearchOutcome:
        from app.config import get_settings
        from app.services.embeddings import EmbeddingError, confident_matches, nearest

        text = self.text_search.search(query, limit=limit, entry_types=entry_types, statuses=statuses,
                                       date_from=date_from, date_to=date_to)
        pq = text.parsed
        if not pq.terms or not self.semantic_enabled:
            return text  # filter-only queries ("unfinished comics") don't need meaning
        try:
            qvec = self.provider.embed_query(query.strip())
        except EmbeddingError as exc:
            text.note = f"Search by meaning is unavailable right now ({exc}) Showing word matches."
            return text

        s = get_settings()

        def meaning_matches(use_filters: bool) -> list[tuple[int, float]]:
            ids_stmt = SearchService.apply_filters(select(CreativeEntry.id), pq, use_filters, date_from, date_to)
            near = nearest(self.db, qvec, self.provider.model, set(self.db.scalars(ids_stmt)), limit=None)
            # "Typical" is judged against the whole archive, not just the filtered slice.
            everyone = [sim for _, sim in nearest(self.db, qvec, self.provider.model, None, limit=None)]
            return confident_matches(near, baseline=everyone)[: self.MAX_MEANING_HITS]

        meaning = meaning_matches(True)
        relaxed = False
        if not meaning and not text.hits and pq.has_filters:
            # Same rule as word search: "stories about amnesia" with no entry typed
            # "story" still shows ideas about amnesia, flagged as a looser match.
            meaning = meaning_matches(False)
            relaxed = bool(meaning)
        if not meaning:
            return SearchOutcome(pq, text.hits, text.mode + "+meaning", text.relaxed, text.note)

        # Keep filters strict when meaning found strict matches; relaxed word matches would only add noise.
        word_hits = text.hits if (relaxed or not text.relaxed) else []
        scores: dict[int, float] = {}
        by_id: dict[int, SearchHit] = {}
        for rank, h in enumerate(word_hits):
            scores[h.entry.id] = scores.get(h.entry.id, 0) + 1 / (self.RRF_K + rank + 1)
            by_id[h.entry.id] = SearchHit(h.entry, 0, h.snippet, ["words"])
        missing = [eid for eid, _ in meaning if eid not in by_id]
        loaded = {}
        if missing:
            stmt = self.text_search._base_query(pq, False, None, None).where(CreativeEntry.id.in_(missing))
            loaded = {e.id: e for e in self.db.scalars(stmt)}
        for rank, (eid, sim) in enumerate(meaning):
            scores[eid] = scores.get(eid, 0) + 1 / (self.RRF_K + rank + 1)
            hit = by_id.get(eid)
            if hit is None:
                if eid not in loaded:
                    continue
                hit = by_id[eid] = SearchHit(loaded[eid], 0, None, [])
            hit.matched_by.append("meaning")
            hit.similarity = round(sim, 3)
        hits = sorted(by_id.values(), key=lambda h: -scores[h.entry.id])[:limit]
        for h in hits:
            h.score = round(scores[h.entry.id] * 1000, 3)
        return SearchOutcome(pq, hits, text.mode + "+meaning", relaxed=relaxed, note=None)
