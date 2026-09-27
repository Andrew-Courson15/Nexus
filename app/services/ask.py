"""Ask NEXUS: answer a question from the user's own archive, citing the entries used.

1. Find the entries most likely to hold the answer (the same words + meaning search the
   Search page uses; the most recent entries if nothing matches, e.g. "what have I been
   working on lately?").
2. Hand only those entries to the AI provider, which must cite them as [#id].
3. Return the answer with its sources, cited ones first. Citations of entries that weren't
   provided are dropped, so every link points at a real entry.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import CreativeEntry, EntryTag
from app.services.ai import EntryContext, get_ai_provider
from app.services.search import SemanticSearchService

MAX_SOURCES = 12
CITE_RE = re.compile(r"#(\d+)\b")
BRACKET_CITE_RE = re.compile(r"\[#(\d+)\]")


@dataclass
class Answer:
    question: str
    answer: str
    provider: str
    sources: list[CreativeEntry]
    cited_ids: list[int]
    note: str | None = None


def _recent(db: Session, limit: int) -> list[CreativeEntry]:
    return list(db.scalars(select(CreativeEntry).where(CreativeEntry.deleted_at.is_(None))
                           .options(selectinload(CreativeEntry.entry_tags).selectinload(EntryTag.tag),
                                    selectinload(CreativeEntry.projects), selectinload(CreativeEntry.attachments))
                           .order_by(CreativeEntry.created_at.desc()).limit(limit)))


def ask(db: Session, question: str) -> Answer:
    question = question.strip()
    found = SemanticSearchService(db).search(question, limit=MAX_SOURCES)
    entries = [h.entry for h in found.hits][:MAX_SOURCES]
    note = None
    if not entries:
        entries = _recent(db, MAX_SOURCES)
        if entries:
            note = "Nothing matched the question directly, so NEXUS looked at your most recent entries."
    provider = get_ai_provider()
    text = provider.answer_question(question, [EntryContext.from_entry(e) for e in entries])

    allowed = {e.id for e in entries}
    cited: list[int] = []
    for m in CITE_RE.finditer(text):
        eid = int(m.group(1))
        if eid in allowed and eid not in cited:
            cited.append(eid)
    # Keep the answer honest: a citation of an entry that wasn't provided is marked, not linked.
    text = BRACKET_CITE_RE.sub(lambda m: m.group(0) if int(m.group(1)) in allowed else "[unknown entry]", text)
    order = {eid: i for i, eid in enumerate(cited)}
    entries.sort(key=lambda e: order.get(e.id, len(order)))
    return Answer(question, text, provider.name, entries, cited, note)
