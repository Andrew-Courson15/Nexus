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


# Fields a revision restore brings back. `original_text` is never touched (it
# never changes), and project membership / relationships are left as they are.
RESTORABLE_FIELDS = (
    "title", "title_source", "entry_type", "entry_type_source", "status", "user_notes", "transcript",
    "ai_summary", "ai_interpretation", "ai_inference", "ai_provider", "confidence", "user_confirmed",
)


def restore_snapshot(db: Session, entry: CreativeEntry, snap: dict) -> list[str]:
    """Bring an entry back to a saved version. Returns the names of fields that changed.

    History is never rewritten: the caller records a new revision afterwards.
    """
    changed = []
    for f in RESTORABLE_FIELDS:
        if f in snap and getattr(entry, f) != snap[f]:
            setattr(entry, f, snap[f])
            changed.append(f)
    if "ai_metadata" in snap:
        old_meta = dict(snap.get("ai_metadata") or {})
        current = entry.ai_metadata or {}
        # Tags the user rejected stay rejected, whatever the old version said.
        rejected = set(old_meta.get("rejected_tags", [])) | set(current.get("rejected_tags", []))
        if rejected or "rejected_tags" in old_meta or "rejected_tags" in current:
            old_meta["rejected_tags"] = sorted(rejected)
        old_meta.pop("analysis_error", None)
        if old_meta != current:
            entry.ai_metadata = old_meta
            changed.append("ai_metadata")
    if "tags" in snap:
        wanted = {(t["name"], t["source"]) for t in snap["tags"]}
        have = {(et.tag.name, et.source) for et in entry.entry_tags}
        if wanted != have:
            for et in list(entry.entry_tags):
                entry.entry_tags.remove(et)
            db.flush()
            for name, source in sorted(wanted):
                add_tag(db, entry, name, source)
            changed.append("tags")
        # Tags brought back by the restore are no longer "removed by the user".
        meta = dict(entry.ai_metadata or {})
        names = {n for n, _ in wanted}
        if names & set(meta.get("rejected_tags", [])):
            meta["rejected_tags"] = sorted(set(meta["rejected_tags"]) - names)
            entry.ai_metadata = meta
    return changed
