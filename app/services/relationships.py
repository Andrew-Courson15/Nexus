"""Relationship discovery and review.

AI-found links are stored with origin="ai", status="suggested" and the
evidence in `rationale`. They are NOT facts until the user accepts them.
Rejected links are remembered and never re-suggested.
"""
from __future__ import annotations

from sqlalchemy import and_, delete, or_, select
from sqlalchemy.orm import Session, selectinload

from app.models import CreativeEntry, Origin, Relationship, RelationshipStatus
from app.services.ai import AIProvider, EntryContext

MAX_CANDIDATES = 500


def relationships_for(db: Session, entry_id: int, include_rejected: bool = False) -> list[Relationship]:
    stmt = (
        select(Relationship)
        .where(or_(Relationship.source_entry_id == entry_id, Relationship.target_entry_id == entry_id))
        .options(selectinload(Relationship.source_entry), selectinload(Relationship.target_entry))
        .order_by(Relationship.confidence.desc().nullslast(), Relationship.id)
    )
    rels = db.scalars(stmt).all()
    return [
        r for r in rels
        if r.source_entry.deleted_at is None and r.target_entry.deleted_at is None
        and (include_rejected or r.status != RelationshipStatus.rejected.value)
    ]


def discover_relationships(db: Session, entry: CreativeEntry, provider: AIProvider) -> list[Relationship]:
    """Replace this entry's pending AI suggestions with a fresh set."""
    db.execute(
        delete(Relationship).where(
            Relationship.source_entry_id == entry.id,
            Relationship.origin == Origin.ai.value,
            Relationship.status == RelationshipStatus.suggested.value,
        )
    )
    # Any remaining link in either direction (user-made, reviewed, or suggested
    # from the other side) means this pair is already known.
    known = set()
    for r in db.scalars(
        select(Relationship).where(
            or_(Relationship.source_entry_id == entry.id, Relationship.target_entry_id == entry.id)
        )
    ):
        known.add(r.target_entry_id if r.source_entry_id == entry.id else r.source_entry_id)

    candidates = db.scalars(
        select(CreativeEntry)
        .where(and_(CreativeEntry.id != entry.id, CreativeEntry.deleted_at.is_(None)))
        .options(selectinload(CreativeEntry.attachments))
        .order_by(CreativeEntry.created_at.desc())
        .limit(MAX_CANDIDATES)
    ).all()
    contexts = [EntryContext.from_entry(c) for c in candidates if c.id not in known]
    if not contexts:
        return []

    suggestions = provider.find_relationships(EntryContext.from_entry(entry), contexts)
    created = []
    for s in suggestions:
        rel = Relationship(
            source_entry_id=entry.id,
            target_entry_id=s.target_id,
            relationship_type=s.relationship_type,
            confidence=s.confidence,
            origin=Origin.ai.value,
            status=RelationshipStatus.suggested.value,
            rationale=s.rationale,
        )
        db.add(rel)
        created.append(rel)
    db.flush()
    return created
