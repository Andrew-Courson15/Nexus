"""Search by meaning: embeddings for creative entries.

An embedding is a list of numbers describing what a piece of text is *about*.
Entries with similar meaning get similar vectors, so "forgetting who you are"
can find an idea about memory loss even when no words match.

Vectors are DERIVED data: computed locally by an Ollama embedding model
(default `nomic-embed-text`), stored in the `entry_embeddings` table, and
regenerated whenever the entry's text or the model changes. No vector
database is needed at personal-archive scale: similarity is computed directly.
"""
from __future__ import annotations

import hashlib
import math
from abc import ABC, abstractmethod
from array import array
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import CreativeEntry, EntryEmbedding, utcnow

MAX_TEXT_CHARS = 8000

# Some embedding models are trained with task prefixes; using them improves results.
_PREFIXES: dict[str, tuple[str, str]] = {  # model family -> (query prefix, document prefix)
    "nomic-embed-text": ("search_query: ", "search_document: "),
    "embeddinggemma": ("task: search result | query: ", "title: none | text: "),
    "mxbai-embed-large": ("Represent this sentence for searching relevant passages: ", ""),
}


class EmbeddingError(RuntimeError):
    pass


class EmbeddingProvider(ABC):
    name = "base"
    model = ""

    @abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]: ...

    def _prefixes(self) -> tuple[str, str]:
        return _PREFIXES.get(self.model.split(":")[0], ("", ""))

    def embed_query(self, text: str) -> list[float]:
        return self.embed([self._prefixes()[0] + text])[0]

    def embed_document(self, text: str) -> list[float]:
        return self.embed([self._prefixes()[1] + text])[0]

    def health(self) -> dict[str, Any]:
        return {"ok": True, "detail": "Ready."}


class DisabledEmbeddingProvider(EmbeddingProvider):
    name = "none"

    def embed(self, texts):
        raise EmbeddingError("Search by meaning is turned off (NEXUS_EMBEDDING_PROVIDER=none).")

    def health(self):
        return {"ok": False, "detail": "Turned off. Search uses words only."}


class OllamaEmbeddingProvider(EmbeddingProvider):
    name = "ollama"

    def __init__(self, model: str | None = None, base_url: str | None = None,
                 transport: httpx.BaseTransport | None = None):
        s = get_settings()
        self.model = model or s.embedding_model
        self.base_url = (base_url or s.ollama_url).rstrip("/")
        self._http = httpx.Client(base_url=self.base_url, timeout=httpx.Timeout(120.0, connect=5.0),
                                  trust_env=False, transport=transport)

    def embed(self, texts: list[str]) -> list[list[float]]:
        try:
            r = self._http.post("/api/embed", json={"model": self.model, "input": texts})
            if r.status_code == 404 and "not found" not in r.text.lower():
                # Older Ollama without /api/embed: one text at a time.
                return [self._legacy(t) for t in texts]
        except httpx.ConnectError as exc:
            raise EmbeddingError(f"Ollama is not reachable at {self.base_url}.") from exc
        except httpx.HTTPError as exc:
            raise EmbeddingError(f"Could not talk to Ollama: {exc}") from exc
        if r.status_code == 404:
            raise EmbeddingError(f"The embedding model '{self.model}' isn't installed. Run: ollama pull {self.model}")
        if r.status_code >= 400:
            raise EmbeddingError(f"Ollama error ({r.status_code}): {r.text[:200]}")
        try:
            vectors = r.json().get("embeddings")
        except (ValueError, AttributeError) as exc:
            raise EmbeddingError("Ollama returned an unreadable reply.") from exc
        if (not isinstance(vectors, list) or len(vectors) != len(texts)
                or not all(isinstance(v, list) and v for v in vectors)):
            raise EmbeddingError("Ollama returned no embeddings.")
        return vectors

    def _legacy(self, text: str) -> list[float]:
        try:
            r = self._http.post("/api/embeddings", json={"model": self.model, "prompt": text})
            if r.status_code >= 400:
                raise EmbeddingError(f"Ollama error ({r.status_code}): {r.text[:200]}")
            vec = r.json()["embedding"]
        except httpx.HTTPError as exc:
            raise EmbeddingError(f"Could not talk to Ollama: {exc}") from exc
        except (ValueError, KeyError, TypeError) as exc:
            raise EmbeddingError("Ollama returned an unreadable reply.") from exc
        if not isinstance(vec, list) or not vec:
            raise EmbeddingError("Ollama returned no embeddings.")
        return vec

    def health(self) -> dict[str, Any]:
        try:
            r = self._http.get("/api/tags", timeout=2.0)
            r.raise_for_status()
            installed = [m.get("name", "") for m in r.json().get("models", [])]
        except httpx.HTTPError:
            return {"ok": False, "detail": f"Ollama is not reachable at {self.base_url}. Search uses words only."}
        base = self.model.split(":")[0]
        if not any(m == self.model or m.split(":")[0] == base for m in installed):
            return {"ok": False, "detail": f"Embedding model not installed yet. Run: ollama pull {self.model} "
                                           "(about 0.3 GB). Until then search uses words only."}
        return {"ok": True, "detail": f"Search by meaning is on ({self.model})."}


_instances: dict[tuple, EmbeddingProvider] = {}


def get_embedding_provider() -> EmbeddingProvider:
    """The configured provider. One shared instance per process (reuses its connection pool)."""
    s = get_settings()
    if s.embedding_provider in ("none", "off", "disabled", ""):
        return DisabledEmbeddingProvider()
    key = (s.embedding_provider, s.embedding_model, s.ollama_url)
    if key not in _instances:
        _instances[key] = OllamaEmbeddingProvider()
    return _instances[key]


# --------------------------------------------------------------------------- #
# Vectors
# --------------------------------------------------------------------------- #
def normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]


def pack(vec: list[float]) -> bytes:
    return array("f", vec).tobytes()


def unpack(blob: bytes) -> array:
    a = array("f")
    a.frombytes(blob)
    return a


def dot(a, b) -> float:
    return sum(x * y for x, y in zip(a, b))


# --------------------------------------------------------------------------- #
# Entry index
# --------------------------------------------------------------------------- #
def embedding_text(entry: CreativeEntry) -> str:
    """The text that represents an entry's meaning: user material first, then AI layers."""
    from app.services.search import visual_terms

    meta = entry.ai_metadata or {}
    parts = [
        entry.title or "",
        entry.original_text or "",
        entry.user_notes or "",
        entry.transcript or "",
        entry.ai_summary or "",
        ", ".join(meta.get("concepts", []) + meta.get("themes", [])),
        ", ".join(visual_terms(meta.get("visual"))),
        ", ".join(et.tag.name for et in entry.entry_tags),
    ]
    return "\n".join(p for p in parts if p.strip())[:MAX_TEXT_CHARS]


def content_hash(text: str, model: str) -> str:
    return hashlib.sha256(f"{model}\n{text}".encode()).hexdigest()


def needs_embedding(db: Session, entry: CreativeEntry, model: str) -> bool:
    text = embedding_text(entry)
    if not text.strip():
        return False
    row = db.get(EntryEmbedding, entry.id)
    return row is None or row.content_hash != content_hash(text, model)


def embed_entry(db: Session, entry: CreativeEntry, provider: EmbeddingProvider | None = None) -> bool:
    """(Re)compute the entry's vector if its text changed. Returns True if one was stored."""
    provider = provider or get_embedding_provider()
    text = embedding_text(entry)
    if not text.strip():
        return False
    digest = content_hash(text, provider.model)
    row = db.get(EntryEmbedding, entry.id)
    if row is not None and row.content_hash == digest:
        return False
    vec = normalize(provider.embed_document(text))
    if row is None:
        row = EntryEmbedding(entry_id=entry.id)
        db.add(row)
    row.model, row.dim, row.vector, row.content_hash = provider.model, len(vec), pack(vec), digest
    row.created_at = utcnow()
    entry.embedding_id = f"entry_embeddings:{entry.id}"
    entry.embedding_model = provider.model
    entry.embedding_created_at = row.created_at
    meta = dict(entry.ai_metadata or {})
    if meta.pop("embedding_error", None) is not None:
        entry.ai_metadata = meta
    return True


def nearest(db: Session, query_vec: list[float], model: str, allowed_ids: set[int] | None = None,
            limit: int | None = 50) -> list[tuple[int, float]]:
    """Entries most similar in meaning to `query_vec`, as (entry_id, cosine similarity)."""
    q = normalize(query_vec)
    rows = db.execute(select(EntryEmbedding.entry_id, EntryEmbedding.vector)
                      .where(EntryEmbedding.model == model)).all()
    scored = []
    for entry_id, blob in rows:
        if allowed_ids is not None and entry_id not in allowed_ids:
            continue
        vec = unpack(blob)
        if len(vec) != len(q):
            continue
        scored.append((entry_id, dot(q, vec)))
    scored.sort(key=lambda t: -t[1])
    return scored if limit is None else scored[:limit]


# Below this many indexed entries the archive's typical score isn't meaningful; only the floor applies.
MIN_ENTRIES_FOR_BASELINE = 6


def confident_matches(scored: list[tuple[int, float]], baseline: list[float] | None = None
                      ) -> list[tuple[int, float]]:
    """Keep only meaning matches that are clearly related, best first.

    Embedding models give *everything* some similarity (nomic-embed-text scores unrelated
    text around 0.5), so a match must:
      - clear an absolute floor (NEXUS_SEMANTIC_MIN_SIMILARITY),
      - stand out from the archive's typical score for this query by NEXUS_SEMANTIC_MARGIN
        (when there are enough entries to know what "typical" is), and
      - be within NEXUS_SEMANTIC_WINDOW of the best match.
    `baseline` is every score for this query (defaults to `scored`).
    """
    if not scored:
        return []
    s = get_settings()
    cutoff = max(s.semantic_min_similarity, scored[0][1] - s.semantic_window)
    sims = sorted(baseline if baseline is not None else [sim for _, sim in scored])
    if len(sims) >= MIN_ENTRIES_FOR_BASELINE:
        cutoff = max(cutoff, sims[len(sims) // 2] + s.semantic_margin)
    return [(eid, sim) for eid, sim in scored if sim >= cutoff]


def similar_entries(db: Session, text: str, allowed_ids: set[int], limit: int = 5) -> list[int]:
    """Entries (among `allowed_ids`) closest in meaning to `text`, best first.

    Empty if the meaning index isn't available.
    """
    provider = get_embedding_provider()
    if provider.name == "none" or not text.strip() or not allowed_ids:
        return []
    try:
        vec = provider.embed_document(text[:MAX_TEXT_CHARS])
    except EmbeddingError:
        return []
    scored = nearest(db, vec, provider.model, allowed_ids, limit=None)
    return [eid for eid, _ in confident_matches(scored)[:limit]]


def index_stats(db: Session, model: str) -> dict[str, int]:
    from sqlalchemy.orm import selectinload

    from app.models import EntryTag

    entries = db.scalars(select(CreativeEntry).where(CreativeEntry.deleted_at.is_(None))
                         .options(selectinload(CreativeEntry.entry_tags).selectinload(EntryTag.tag))).all()
    stale = sum(1 for e in entries if needs_embedding(db, e, model))
    return {"entries": len(entries), "indexed": len(entries) - stale, "pending": stale}
