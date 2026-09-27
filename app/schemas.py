"""Pydantic schemas (request validation and response shapes)."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models import (
    ArtMode,
    CreativeEntry,
    EntryStatus,
    EntryType,
    Project,
    ProjectStatus,
    ProjectType,
    Relationship,
    RelationshipStatus,
    RelationshipType,
)

# Explains the provenance labels to API consumers and the UI.
PROVENANCE = {
    "user_said": ["original_text", "user_notes", "attachments", "tags[source=user]", "*_source=user"],
    "ai_interpretation": ["ai_summary", "ai_interpretation", "ai_metadata.concepts", "tags[source=ai]",
                          "*_source=ai"],
    "ai_inference": ["ai_inference", "ai_metadata.themes", "ai_metadata.potential_projects",
                     "relationships[origin=ai,status=suggested]"],
    "unknown": "Any field that is null/empty has not been established.",
}


# --------------------------------------------------------------------------- #
# Requests
# --------------------------------------------------------------------------- #
class EntryCreate(BaseModel):
    original_text: str = Field(..., max_length=200_000, description="Stored exactly as submitted.")
    title: str | None = Field(None, max_length=300)
    entry_type: EntryType | None = None
    status: EntryStatus = EntryStatus.raw
    source_type: Literal["text", "conversation", "import"] = "text"
    tags: list[str] = Field(default_factory=list)
    project_ids: list[int] = Field(default_factory=list)
    analyze: bool = True
    # Set by the phone's offline queue: a re-sent capture is recognised, and keeps the time it was made.
    client_id: str | None = Field(None, min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    captured_at: datetime | None = None

    @field_validator("original_text")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("original_text cannot be empty")
        return v  # intentionally NOT stripped: stored exactly as submitted


class EntryUpdate(BaseModel):
    """Editable fields. `original_text` is deliberately absent: raw material is immutable."""

    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(None, max_length=300)
    entry_type: EntryType | None = None
    status: EntryStatus | None = None
    user_notes: str | None = Field(None, max_length=200_000)
    transcript: str | None = Field(None, max_length=200_000)
    tags: list[str] | None = None
    user_confirmed: bool | None = None
    change_description: str | None = Field(None, max_length=300)


class RelationshipCreate(BaseModel):
    target_entry_id: int
    relationship_type: RelationshipType = RelationshipType.related_to


class RelationshipReview(BaseModel):
    status: RelationshipStatus


def _project_name(v: str | None) -> str | None:
    if v is None:
        return v
    v = " ".join(v.split())
    if not v:
        raise ValueError("Project name cannot be empty")
    return v


class ProjectCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=300)
    description: str | None = Field(None, max_length=20_000)
    project_type: ProjectType = ProjectType.other
    status: ProjectStatus = ProjectStatus.active

    @field_validator("name")
    @classmethod
    def _clean_name(cls, v: str | None) -> str | None:
        return _project_name(v)


class ProjectUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(None, min_length=1, max_length=300)
    description: str | None = Field(None, max_length=20_000)
    project_type: ProjectType | None = None
    status: ProjectStatus | None = None

    @field_validator("name")
    @classmethod
    def _clean_name(cls, v: str | None) -> str | None:
        return _project_name(v)


class ProjectEntryAdd(BaseModel):
    entry_id: int


class SearchRequest(BaseModel):
    query: str = Field("", max_length=500)
    limit: int = Field(50, ge=1, le=200)
    entry_types: list[EntryType] = Field(default_factory=list)
    statuses: list[EntryStatus] = Field(default_factory=list)
    date_from: datetime | None = None
    date_to: datetime | None = None


# --------------------------------------------------------------------------- #
# Responses
# --------------------------------------------------------------------------- #
class TagOut(BaseModel):
    name: str
    source: str


class ProjectRef(BaseModel):
    id: int
    name: str


class AttachmentOut(BaseModel):
    id: int
    kind: str
    original_filename: str
    mime_type: str
    size_bytes: int
    sha256: str
    created_at: datetime
    url: str
    preview_url: str | None = None  # JPEG/PNG to display (the original, or a rendering of it)
    thumb_url: str | None = None    # small version for cards and grids


class EntrySummary(BaseModel):
    id: int
    title: str
    title_source: str
    entry_type: str
    entry_type_source: str
    status: str
    source_type: str
    created_at: datetime
    updated_at: datetime
    ai_summary: str | None
    tags: list[TagOut]
    projects: list[ProjectRef]
    attachments: list[AttachmentOut]
    excerpt: str | None
    processing_status: str | None
    deleted_at: datetime | None = None


class EntryDetail(EntrySummary):
    original_text: str | None
    user_notes: str | None
    transcript: str | None
    ai_interpretation: str | None
    ai_inference: str | None
    ai_metadata: dict[str, Any]
    ai_provider: str | None
    analyzed_at: datetime | None
    confidence: float | None
    user_confirmed: bool
    source_file: str | None
    embedding_id: str | None
    embedding_model: str | None
    embedding_created_at: datetime | None
    revision_count: int
    provenance: dict[str, Any] = PROVENANCE


class EntryList(BaseModel):
    items: list[EntrySummary]
    total: int


class RevisionOut(BaseModel):
    id: int
    version_number: int
    change_description: str
    created_at: datetime
    content_snapshot: dict[str, Any]


class EntryRef(BaseModel):
    id: int
    title: str
    entry_type: str
    status: str


class RelationshipOut(BaseModel):
    id: int
    relationship_type: str
    confidence: float | None
    origin: str
    status: str
    rationale: str | None
    created_at: datetime
    direction: Literal["outgoing", "incoming"]
    other: EntryRef
    label: str


class ProjectOut(BaseModel):
    id: int
    name: str
    description: str | None
    project_type: str
    status: str
    created_at: datetime
    updated_at: datetime
    entry_count: int
    deleted_at: datetime | None = None


class ProjectDetail(ProjectOut):
    entries: list[EntrySummary]


class SearchResult(BaseModel):
    entry: EntrySummary
    score: float
    snippet: str | None
    matched_by: list[str] = []
    similarity: float | None = None


class AskRequest(BaseModel):
    question: str = Field(min_length=2, max_length=1000)

    @field_validator("question")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        v = v.strip()
        if len(v) < 2:
            raise ValueError("Type a question.")
        return v


class AskSource(BaseModel):
    entry: EntrySummary
    cited: bool


class AskResponse(BaseModel):
    question: str
    answer: str
    provider: str
    sources: list[AskSource]
    note: str | None = None


class SearchResponse(BaseModel):
    query: str
    mode: str
    semantic_enabled: bool
    interpreted: dict[str, Any]
    relaxed: bool
    note: str | None = None
    results: list[SearchResult]


# --------------------------------------------------------------------------- #
# ORM -> schema helpers
# --------------------------------------------------------------------------- #
def preview_url(a: Any) -> str | None:
    from app.services.images import needs_preview

    if a.kind == "image" or a.preview_path or needs_preview(a.mime_type, a.size_bytes):
        return f"/api/uploads/{a.id}/preview"
    return None


def _attachments(e: CreativeEntry) -> list[AttachmentOut]:
    return [
        AttachmentOut(
            id=a.id, kind=a.kind, original_filename=a.original_filename, mime_type=a.mime_type,
            size_bytes=a.size_bytes, sha256=a.sha256, created_at=a.created_at, url=f"/api/uploads/{a.id}",
            preview_url=preview_url(a),
            thumb_url=f"/api/uploads/{a.id}/thumb?v={a.sha256[:10]}" if preview_url(a) else None,
        )
        for a in e.attachments
    ]


def _excerpt(e: CreativeEntry) -> str | None:
    src = e.original_text or e.user_notes
    if not src:
        return None
    src = " ".join(src.split())
    return src if len(src) <= 220 else src[:217].rsplit(" ", 1)[0] + "…"


def _summary_fields(e: CreativeEntry) -> dict[str, Any]:
    return dict(
        id=e.id, title=e.title or "Untitled", title_source=e.title_source, entry_type=e.entry_type,
        entry_type_source=e.entry_type_source, status=e.status, source_type=e.source_type,
        created_at=e.created_at, updated_at=e.updated_at, ai_summary=e.ai_summary,
        tags=sorted((TagOut(name=et.tag.name, source=et.source) for et in e.entry_tags),
                    key=lambda t: (t.source != "user", t.name)),
        projects=[ProjectRef(id=p.id, name=p.name) for p in e.projects if p.deleted_at is None],
        attachments=_attachments(e),
        excerpt=_excerpt(e),
        processing_status=e.processing_status,
        deleted_at=e.deleted_at,
    )


def entry_summary(e: CreativeEntry) -> EntrySummary:
    return EntrySummary(**_summary_fields(e))


def entry_detail(e: CreativeEntry) -> EntryDetail:
    return EntryDetail(
        **_summary_fields(e),
        original_text=e.original_text, user_notes=e.user_notes, transcript=e.transcript,
        ai_interpretation=e.ai_interpretation, ai_inference=e.ai_inference,
        ai_metadata=e.ai_metadata or {}, ai_provider=e.ai_provider, analyzed_at=e.analyzed_at,
        confidence=e.confidence, user_confirmed=e.user_confirmed, source_file=e.source_file,
        embedding_id=e.embedding_id, embedding_model=e.embedding_model,
        embedding_created_at=e.embedding_created_at, revision_count=len(e.revisions),
    )


def relationship_out(r: Relationship, entry_id: int) -> RelationshipOut:
    outgoing = r.source_entry_id == entry_id
    other = r.target_entry if outgoing else r.source_entry
    if r.origin == "ai":
        label = {"suggested": "AI suggested relationship", "accepted": "Confirmed by you (AI suggested)",
                 "ignored": "AI suggestion — ignored", "rejected": "AI suggestion — rejected"}[r.status]
    else:
        label = "Created by you"
    return RelationshipOut(
        id=r.id, relationship_type=r.relationship_type, confidence=r.confidence, origin=r.origin,
        status=r.status, rationale=r.rationale, created_at=r.created_at,
        direction="outgoing" if outgoing else "incoming",
        other=EntryRef(id=other.id, title=other.title or "Untitled", entry_type=other.entry_type,
                       status=other.status),
        label=label,
    )


def project_out(p: Project, entry_count: int) -> ProjectOut:
    return ProjectOut(
        id=p.id, name=p.name, description=p.description, project_type=p.project_type, status=p.status,
        created_at=p.created_at, updated_at=p.updated_at, entry_count=entry_count, deleted_at=p.deleted_at,
    )


# --------------------------------------------------------------------------- #
# Visual Art Intelligence
# --------------------------------------------------------------------------- #
class ArtAnalysisCreate(BaseModel):
    mode: ArtMode = ArtMode.analysis
    related_entry_ids: list[int] = Field(default_factory=list, max_length=5)
    question: str | None = Field(None, max_length=2000)


class ArtImage(BaseModel):
    entry_id: int
    title: str
    url: str | None


class ArtAnalysisOut(BaseModel):
    id: int
    entry_id: int
    analysis_version: int
    mode: str
    related_entry_ids: list[int]
    question: str | None
    status: str
    error: str | None
    model: str | None
    visual_description: str | None
    artistic_analysis: dict[str, Any]
    composition_analysis: dict[str, Any]
    character_analysis: dict[str, Any]
    storytelling_analysis: dict[str, Any]
    sequential_art_analysis: dict[str, Any]
    strengths: list[Any]
    development_opportunities: list[Any]
    experiments: list[Any]
    uncertainties: list[Any]
    details: dict[str, Any]
    created_at: datetime
    completed_at: datetime | None
    images: list[ArtImage] = []


class ArtPreferenceCreate(BaseModel):
    statement: str = Field(..., min_length=2, max_length=300)


class ArtPreferenceReview(BaseModel):
    status: Literal["confirmed", "rejected", "suggested"]


class ArtPreferenceOut(BaseModel):
    id: int
    statement: str
    origin: str
    status: str
    evidence: list[ArtImage]
    created_at: datetime


class ArtKnowledgeCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=300)
    topic: str = "other"
    source: str | None = Field(None, max_length=300)
    content: str = Field(..., min_length=1, max_length=200_000)

    @field_validator("title", "content")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("cannot be empty")
        return v


class ArtKnowledgeOut(BaseModel):
    id: int
    title: str
    topic: str
    source: str | None
    content: str
    passages: int
    created_at: datetime
    updated_at: datetime


class GalleryItem(BaseModel):
    entry: EntrySummary
    analyses: int
    latest_status: str | None
    latest_analysis_id: int | None
