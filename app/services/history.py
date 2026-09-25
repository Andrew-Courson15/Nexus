"""Revision history and tag helpers for creative entries."""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import CreativeEntry, CreativeRevision, EntryTag, Origin, Tag

SNAPSHOT_FIELDS = (
    "title", "title_source", "entry_type", "entry_type_source", "status", "original_text", "user_notes",
    "transcript", "ai_summary", "ai_interpretation", "ai_inference", "ai_metadata", "ai_provider",
    "confidence", "user_confirmed", "source_type", "source_file",
)


def snapshot(entry: CreativeEntry) -> dict:
    data = {f: getattr(entry, f) for f in SNAPSHOT_FIELDS}
    data["tags"] = sorted(({"name": et.tag.name, "source": et.source} for et in entry.entry_tags),
                          key=lambda t: t["name"])
    data["project_ids"] = sorted(p.id for p in entry.projects)
    return data


def record_revision(db: Session, entry: CreativeEntry, description: str) -> CreativeRevision:
    """Append an immutable snapshot of the entry's current state."""
    db.flush()
    current = db.scalar(
        select(func.max(CreativeRevision.version_number)).where(CreativeRevision.entry_id == entry.id)
    ) or 0
    rev = CreativeRevision(
        entry_id=entry.id,
        version_number=current + 1,
        content_snapshot=snapshot(entry),
        change_description=description[:500],
    )
    db.add(rev)
    return rev


def clean_tag(name: str) -> str:
    return " ".join(name.strip().lower().split())[:100]


def get_or_create_tag(db: Session, name: str) -> Tag:
    name = clean_tag(name)
    tag = db.scalar(select(Tag).where(Tag.name == name))
    if tag is None:
        tag = Tag(name=name)
        db.add(tag)
        db.flush()
    return tag


def add_tag(db: Session, entry: CreativeEntry, name: str, source: str) -> None:
    name = clean_tag(name)
    if not name or any(et.tag.name == name for et in entry.entry_tags):
        return
    entry.entry_tags.append(EntryTag(tag=get_or_create_tag(db, name), source=source))


def set_user_tags(db: Session, entry: CreativeEntry, names: list[str]) -> None:
    """Make the entry's tag set equal `names`.

    Kept tags retain their origin; new ones are user tags. AI tags the user
    removed are remembered so re-analysis does not bring them back.
    """
    wanted = {clean_tag(n) for n in names if clean_tag(n)}
    meta = dict(entry.ai_metadata or {})
    rejected = set(meta.get("rejected_tags", []))
    for et in list(entry.entry_tags):
        if et.tag.name not in wanted:
            if et.source == Origin.ai.value:
                rejected.add(et.tag.name)
            entry.entry_tags.remove(et)
    for n in sorted(wanted):
        add_tag(db, entry, n, Origin.user.value)
        rejected.discard(n)
    meta["rejected_tags"] = sorted(rejected)
    entry.ai_metadata = meta


def replace_ai_tags(db: Session, entry: CreativeEntry, names: list[str]) -> None:
    rejected = set((entry.ai_metadata or {}).get("rejected_tags", []))
    for et in list(entry.entry_tags):
        if et.source == Origin.ai.value:
            entry.entry_tags.remove(et)
    db.flush()
    for n in names:
        if clean_tag(n) not in rejected:
            add_tag(db, entry, n, Origin.ai.value)
