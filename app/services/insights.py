"""Creative intelligence: recurring patterns backed by database evidence.

Every observation carries the list of entries that support it. No pattern is
reported without that evidence.
"""
from __future__ import annotations

from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import CreativeEntry


def find_patterns(db: Session, min_count: int = 3, limit: int = 10) -> list[dict]:
    entries = db.scalars(
        select(CreativeEntry).where(CreativeEntry.deleted_at.is_(None)).order_by(CreativeEntry.created_at)
    ).all()
    buckets: dict[tuple[str, str], list[CreativeEntry]] = defaultdict(list)
    for e in entries:
        meta = e.ai_metadata or {}
        for c in meta.get("concepts", []):
            buckets[("concept", c)].append(e)
        for t in meta.get("themes", []):
            buckets[("theme", t)].append(e)

    patterns = []
    for (kind, name), evidence in buckets.items():
        if len(evidence) < min_count:
            continue
        first, last = evidence[0].created_at, evidence[-1].created_at
        noun = "concepts involving" if kind == "concept" else "entries with the potential theme of"
        patterns.append({
            "kind": kind,
            "name": name,
            "count": len(evidence),
            "observation": f"You have created {len(evidence)} {noun} “{name}” "
                           f"between {first:%b %d, %Y} and {last:%b %d, %Y}.",
            "label": "AI inference from your archive",
            "evidence": [
                {"id": e.id, "title": e.title, "entry_type": e.entry_type, "created_at": e.created_at.isoformat()}
                for e in reversed(evidence)
            ],
        })
    # Concepts before themes (themes are one inference step further removed).
    patterns.sort(key=lambda p: (-p["count"], p["kind"] != "concept", p["name"]))
    return patterns[:limit]
