"""Characters, and places & worlds: each one with the sketches, scenes and notes connected to it.

Two kinds of connection, kept apart so it's always clear where each came from:
- links: relationships between entries (made by you, or suggested by the AI and labelled so);
- mentions: entries whose own text contains the character's name (a plain text match, no AI).
"""
from __future__ import annotations

import re

from typing import Literal

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import CreativeEntry, EntryType, Relationship, RelationshipStatus
from app.routes.entries import _LOAD
from app.schemas import entry_summary

router = APIRouter(prefix="/api", tags=["characters"])

MAX_PER_CHARACTER = 24  # each list is capped; the counts stay complete


# What makes an entry a character, or a place/world: its type, or one of these tags.
KINDS = {
    "character": (EntryType.character.value, {"character"}),
    "world": (EntryType.worldbuilding.value, {"world", "place", "location", "setting", "worldbuilding"}),
}


def is_kind(e: CreativeEntry, kind: str = "character") -> bool:
    entry_type, tags = KINDS[kind]
    return e.entry_type == entry_type or any(et.tag.name in tags for et in e.entry_tags)


def is_character(e: CreativeEntry) -> bool:
    return is_kind(e, "character")


def name_of(e: CreativeEntry) -> str | None:
    """The name to look for in other entries' text, or None when the title isn't a usable name."""
    title = (e.title or "").strip()
    if len(title) < 3 or title.lower() == "untitled" or len(title) > 40 or len(title.split()) > 4:
        return None
    return title


def _ref(e: CreativeEntry) -> dict:
    s = entry_summary(e)
    pic = next((a for a in s.attachments if a.kind == "image" and a.thumb_url), None) or \
        next((a for a in s.attachments if a.thumb_url), None)
    return {"id": s.id, "title": s.title, "entry_type": s.entry_type, "created_at": s.created_at,
            "thumb_url": pic.thumb_url if pic else None, "excerpt": s.excerpt or s.ai_summary}


def _text(e: CreativeEntry) -> str:
    return " ".join(x for x in (e.title, e.original_text, e.transcript, e.user_notes) if x)


@router.get("/characters")
def list_characters(kind: Literal["character", "world"] = "character", project_id: int | None = None,
                    db: Session = Depends(get_db)) -> list[dict]:
    entries = db.scalars(select(CreativeEntry).where(CreativeEntry.deleted_at.is_(None)).options(*_LOAD)
                         .order_by(CreativeEntry.created_at.desc(), CreativeEntry.id.desc())).all()
    by_id = {e.id: e for e in entries}
    characters = [e for e in entries if is_kind(e, kind)
                  and (project_id is None or any(p.id == project_id for p in e.projects))]
    if not characters:
        return []
    wanted = {c.id for c in characters}
    links: dict[int, list] = {c.id: [] for c in characters}
    rows = db.execute(select(Relationship.source_entry_id, Relationship.target_entry_id, Relationship.relationship_type,
                             Relationship.origin, Relationship.status, Relationship.confidence)
                      .where(Relationship.status != RelationshipStatus.rejected.value)
                      .order_by(Relationship.confidence.desc().nullslast(), Relationship.id)).all()
    for src, tgt, kind, origin, status, _conf in rows:
        for me, other in ((src, tgt), (tgt, src)):
            if me in wanted and other in by_id:
                links[me].append((other, kind, "outgoing" if me == src else "incoming", origin, status))

    out = []
    for c in characters:
        linked, seen = [], {c.id}
        for other, kind, direction, origin, status in links[c.id]:
            if other in seen:
                continue
            seen.add(other)
            linked.append({"entry": other, "relationship_type": kind, "direction": direction, "origin": origin,
                           "status": status})
        mentions = []
        name = name_of(c)
        if name:
            pattern = re.compile(rf"(?<!\w){re.escape(name)}(?!\w)", re.IGNORECASE)
            mentions = [e.id for e in entries if e.id not in seen and pattern.search(_text(e))]
        out.append({
            "character": entry_summary(c).model_dump(mode="json"),
            "name_searched": name,
            "link_count": len(linked),
            "mention_count": len(mentions),
            "links": [{**x, "entry": _ref(by_id[x["entry"]])} for x in linked[:MAX_PER_CHARACTER]],
            "mentions": [_ref(by_id[i]) for i in mentions[:MAX_PER_CHARACTER]],
        })
    out.sort(key=lambda x: -(x["link_count"] + x["mention_count"]))
    return out
