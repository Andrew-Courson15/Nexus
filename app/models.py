"""SQLAlchemy models.

Data principle
--------------
RAW data (never modified after capture):
    CreativeEntry.original_text, Attachment files on disk.
USER data (supplied or confirmed by the user):
    title/entry_type when their *_source is "user", status, user_notes,
    user tags, project membership, accepted relationships.
DERIVED data (regenerable, always labelled as AI output):
    transcript, ai_summary, ai_interpretation, ai_inference, ai_metadata,
    AI tags, AI-suggested relationships, embeddings.

Enumerations are stored as plain strings so the schema migrates cleanly to
PostgreSQL and new values can be added without a schema change.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator

from app.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class UTCDateTime(TypeDecorator):
    """Timezone-aware UTC datetimes on every backend (SQLite drops tzinfo)."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is not None and value.tzinfo is not None:
            value = value.astimezone(timezone.utc)
            if dialect.name == "sqlite":
                value = value.replace(tzinfo=None)
        return value

    def process_result_value(self, value, dialect):
        if value is not None and value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value


class EntryType(str, Enum):
    idea = "idea"
    comic = "comic"
    movie = "movie"
    short_film = "short_film"
    story = "story"
    character = "character"
    scene = "scene"
    dialogue = "dialogue"
    sketch = "sketch"
    artwork = "artwork"
    worldbuilding = "worldbuilding"
    research = "research"
    note = "note"
    brain_dump = "brain_dump"
    other = "other"


class EntryStatus(str, Enum):
    raw = "raw"
    exploring = "exploring"
    developing = "developing"
    active = "active"
    completed = "completed"
    abandoned = "abandoned"
    archived = "archived"


class SourceType(str, Enum):
    text = "text"
    voice = "voice"
    image = "image"
    file = "file"
    import_ = "import"
    conversation = "conversation"


class ProjectType(str, Enum):
    comic = "comic"
    movie = "movie"
    short_film = "short_film"
    art = "art"
    animation = "animation"
    writing = "writing"
    game = "game"
    other = "other"


class ProjectStatus(str, Enum):
    idea = "idea"
    active = "active"
    paused = "paused"
    completed = "completed"
    archived = "archived"


class RelationshipType(str, Enum):
    related_to = "related_to"
    inspired_by = "inspired_by"
    part_of = "part_of"
    contains = "contains"
    character_in = "character_in"
    scene_in = "scene_in"
    similar_to = "similar_to"
    contradicts = "contradicts"
    evolved_from = "evolved_from"
    replaces = "replaces"
    references = "references"


class RelationshipStatus(str, Enum):
    suggested = "suggested"  # AI suggested, not yet reviewed
    accepted = "accepted"    # confirmed by the user
    rejected = "rejected"    # user said no; never re-suggested
    ignored = "ignored"      # user dismissed without deciding


class Origin(str, Enum):
    user = "user"
    ai = "ai"


# Statuses shown in the Idea Incubator.
INCUBATOR_STATUSES = (EntryStatus.raw.value, EntryStatus.exploring.value)
# Processing states that mean "work still to do".
ACTIVE_PROCESSING = ("queued", "transcribing", "analyzing")
UNFINISHED_STATUSES = (
    EntryStatus.raw.value,
    EntryStatus.exploring.value,
    EntryStatus.developing.value,
    EntryStatus.active.value,
)


class ProjectEntry(Base):
    """Many-to-many link: an entry can belong to several projects."""

    __tablename__ = "project_entries"

    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True)
    entry_id: Mapped[int] = mapped_column(ForeignKey("creative_entries.id", ondelete="CASCADE"), primary_key=True)
    added_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class CreativeEntry(Base):
    __tablename__ = "creative_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # Title / type can come from the user or the AI; *_source records which.
    title: Mapped[str] = mapped_column(String(300), default="")
    title_source: Mapped[str] = mapped_column(String(10), default=Origin.ai.value)
    entry_type: Mapped[str] = mapped_column(String(30), default=EntryType.other.value, index=True)
    entry_type_source: Mapped[str] = mapped_column(String(10), default=Origin.ai.value)
    status: Mapped[str] = mapped_column(String(30), default=EntryStatus.raw.value, index=True)

    # RAW: exactly what the user submitted. Never modified after creation.
    original_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    # USER: later additions written by the user (the original stays untouched).
    user_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # DERIVED
    transcript: Mapped[str | None] = mapped_column(Text, nullable=True)
    ai_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    ai_interpretation: Mapped[str | None] = mapped_column(Text, nullable=True)
    ai_inference: Mapped[str | None] = mapped_column(Text, nullable=True)
    # concepts, themes, potential projects, uncertainties, visual metadata...
    ai_metadata: Mapped[dict] = mapped_column(JSON, default=dict)
    ai_provider: Mapped[str | None] = mapped_column(String(100), nullable=True)
    analyzed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    user_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    # Background work: None | queued | transcribing | analyzing | done | failed
    processing_status: Mapped[str | None] = mapped_column(String(20), nullable=True)

    source_type: Mapped[str] = mapped_column(String(30), default=SourceType.text.value, index=True)
    # Id the capturing device made up (phone offline queue): makes re-sends safe.
    client_id: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    # Path of the raw file relative to the data directory (if any).
    source_file: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Reserved for future semantic search (no vector DB in v1).
    embedding_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    embedding_model: Mapped[str | None] = mapped_column(String(200), nullable=True)
    embedding_created_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)

    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)
    # Soft delete: entries are hidden, never destroyed.
    deleted_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True, index=True)

    attachments: Mapped[list[Attachment]] = relationship(
        back_populates="entry", cascade="all, delete-orphan", order_by="Attachment.id"
    )
    revisions: Mapped[list[CreativeRevision]] = relationship(
        back_populates="entry", cascade="all, delete-orphan", order_by="CreativeRevision.version_number"
    )
    entry_tags: Mapped[list[EntryTag]] = relationship(back_populates="entry", cascade="all, delete-orphan")
    projects: Mapped[list[Project]] = relationship(
        secondary="project_entries", back_populates="entries", order_by="Project.name"
    )

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None


class Attachment(Base):
    """Metadata about an original uploaded file. The file itself is never modified."""

    __tablename__ = "attachments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    entry_id: Mapped[int] = mapped_column(ForeignKey("creative_entries.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(20))  # image | audio | file
    original_filename: Mapped[str] = mapped_column(String(500))
    stored_path: Mapped[str] = mapped_column(String(500))  # relative to data dir
    mime_type: Mapped[str] = mapped_column(String(200))
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)

    entry: Mapped[CreativeEntry] = relationship(back_populates="attachments")


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(300))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    project_type: Mapped[str] = mapped_column(String(30), default=ProjectType.other.value)
    status: Mapped[str] = mapped_column(String(30), default=ProjectStatus.active.value)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)
    # Soft delete: a trashed project is hidden; its entries are never touched.
    deleted_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)

    entries: Mapped[list[CreativeEntry]] = relationship(
        secondary="project_entries", back_populates="projects", order_by="CreativeEntry.created_at.desc()"
    )


class Relationship(Base):
    """Edge between two entries — the initial knowledge-graph representation."""

    __tablename__ = "relationships"
    __table_args__ = (UniqueConstraint("source_entry_id", "target_entry_id", "relationship_type"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_entry_id: Mapped[int] = mapped_column(ForeignKey("creative_entries.id", ondelete="CASCADE"), index=True)
    target_entry_id: Mapped[int] = mapped_column(ForeignKey("creative_entries.id", ondelete="CASCADE"), index=True)
    relationship_type: Mapped[str] = mapped_column(String(30), default=RelationshipType.related_to.value)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    origin: Mapped[str] = mapped_column(String(10), default=Origin.ai.value)
    status: Mapped[str] = mapped_column(String(20), default=RelationshipStatus.suggested.value)
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)  # the evidence behind a suggestion
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)

    source_entry: Mapped[CreativeEntry] = relationship(foreign_keys=[source_entry_id])
    target_entry: Mapped[CreativeEntry] = relationship(foreign_keys=[target_entry_id])


class EntryEmbedding(Base):
    """DERIVED: a vector describing an entry's meaning, for search by meaning.

    Stored as packed float32 (L2-normalised) so cosine similarity is a dot
    product. Regenerated whenever the entry's text or the model changes.
    """

    __tablename__ = "entry_embeddings"

    entry_id: Mapped[int] = mapped_column(ForeignKey("creative_entries.id", ondelete="CASCADE"), primary_key=True)
    model: Mapped[str] = mapped_column(String(200))
    dim: Mapped[int] = mapped_column(Integer)
    vector: Mapped[bytes] = mapped_column(LargeBinary)
    content_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class Tag(Base):
    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, index=True)


class EntryTag(Base):
    __tablename__ = "entry_tags"

    entry_id: Mapped[int] = mapped_column(ForeignKey("creative_entries.id", ondelete="CASCADE"), primary_key=True)
    tag_id: Mapped[int] = mapped_column(ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True)
    source: Mapped[str] = mapped_column(String(10), default=Origin.user.value)  # user | ai
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)

    entry: Mapped[CreativeEntry] = relationship(back_populates="entry_tags")
    tag: Mapped[Tag] = relationship()


class CreativeRevision(Base):
    """Snapshot of an entry after each meaningful change (append-only)."""

    __tablename__ = "creative_revisions"
    __table_args__ = (UniqueConstraint("entry_id", "version_number"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    entry_id: Mapped[int] = mapped_column(ForeignKey("creative_entries.id", ondelete="CASCADE"), index=True)
    version_number: Mapped[int] = mapped_column(Integer)
    content_snapshot: Mapped[dict] = mapped_column(JSON)
    change_description: Mapped[str] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)

    entry: Mapped[CreativeEntry] = relationship(back_populates="revisions")


# --------------------------------------------------------------------------- #
# Visual Art Intelligence
# --------------------------------------------------------------------------- #
class ArtMode(str, Enum):
    analysis = "analysis"        # layered analysis of one artwork (default, also automatic)
    critique = "critique"        # deeper structured critique
    artist = "artist"            # collaborator: questions first
    storyboard = "storyboard"    # rough sequence: staging, camera, clarity (not rendering)
    page = "page"                # a whole comic page: layout, reading order, page turn
    sequence = "sequence"        # several panels in order
    compare = "compare"          # image A vs image B: what changed
    consistency = "consistency"  # several drawings of one character
    evolution = "evolution"      # versions of one piece over time


# Modes that need more than one image.
MULTI_IMAGE_MODES = ("sequence", "compare", "consistency", "evolution")


class ArtworkAnalysis(Base):
    """DERIVED: one AI analysis of one or more artworks.

    Append-only: re-running creates a new version, so earlier analyses are
    never destroyed and the thinking about a piece can be compared over time.
    """

    __tablename__ = "artwork_analyses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    entry_id: Mapped[int] = mapped_column(ForeignKey("creative_entries.id", ondelete="CASCADE"), index=True)
    analysis_version: Mapped[int] = mapped_column(Integer, default=1)
    mode: Mapped[str] = mapped_column(String(20), default=ArtMode.analysis.value)
    # Other entries whose images were part of this analysis, in order (compare / sequence ...).
    related_entry_ids: Mapped[list] = mapped_column(JSON, default=list)
    question: Mapped[str | None] = mapped_column(Text, nullable=True)  # what the artist asked (artist mode)
    status: Mapped[str] = mapped_column(String(20), default="queued")  # queued | running | done | failed
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    model: Mapped[str | None] = mapped_column(String(200), nullable=True)

    visual_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    artistic_analysis: Mapped[dict] = mapped_column(JSON, default=dict)
    composition_analysis: Mapped[dict] = mapped_column(JSON, default=dict)
    character_analysis: Mapped[dict] = mapped_column(JSON, default=dict)
    storytelling_analysis: Mapped[dict] = mapped_column(JSON, default=dict)
    sequential_art_analysis: Mapped[dict] = mapped_column(JSON, default=dict)
    strengths: Mapped[list] = mapped_column(JSON, default=list)                  # PRESERVE
    development_opportunities: Mapped[list] = mapped_column(JSON, default=list)  # DEVELOP
    experiments: Mapped[list] = mapped_column(JSON, default=list)                # EXPERIMENT (optional)
    uncertainties: Mapped[list] = mapped_column(JSON, default=list)
    # Everything else the mode produced: critique, comparison, creative directions, questions,
    # environment, lighting, style signals, library references ...
    details: Mapped[dict] = mapped_column(JSON, default=dict)

    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class ArtPreference(Base):
    """Creative memory: an artistic preference.

    origin="user": stated by the artist (confirmed). origin="observed": a pattern
    NEXUS noticed across several analyses; it stays "suggested" (and is only
    shown as an observation, with its evidence) until the artist confirms it.
    """

    __tablename__ = "art_preferences"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    statement: Mapped[str] = mapped_column(String(300))
    origin: Mapped[str] = mapped_column(String(20), default=Origin.user.value)  # user | observed
    status: Mapped[str] = mapped_column(String(20), default="confirmed")  # suggested | confirmed | rejected
    evidence: Mapped[list] = mapped_column(JSON, default=list)  # entry ids supporting an observed pattern
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)


class ArtKnowledge(Base):
    """USER material: notes from art books, courses and references, used to ground critiques."""

    __tablename__ = "art_knowledge"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(300))
    topic: Mapped[str] = mapped_column(String(50), default="other")
    source: Mapped[str | None] = mapped_column(String(300), nullable=True)  # book / course / link
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)

    chunks: Mapped[list[ArtKnowledgeChunk]] = relationship(
        back_populates="knowledge", cascade="all, delete-orphan", order_by="ArtKnowledgeChunk.idx"
    )


class ArtKnowledgeChunk(Base):
    """DERIVED: a passage of an ArtKnowledge item, with an optional meaning vector for retrieval."""

    __tablename__ = "art_knowledge_chunks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    knowledge_id: Mapped[int] = mapped_column(ForeignKey("art_knowledge.id", ondelete="CASCADE"), index=True)
    idx: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    vector: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    model: Mapped[str | None] = mapped_column(String(200), nullable=True)

    knowledge: Mapped[ArtKnowledge] = relationship(back_populates="chunks")
