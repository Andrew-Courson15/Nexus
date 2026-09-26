"""The AI organizer: builds the derived layer around a creative entry.

Steps for each entry:
  1. Preserve original material (never written here).
  2. Determine entry type          (only if the user hasn't set it)
  3. Generate a title              (only if the user hasn't set it)
  4. Generate a summary
  5. Extract concepts
  6. Extract potential themes
  7. Identify potential projects   (suggestions only — no auto-linking)
  8. Identify potential relationships (stored as "suggested")
  9. Generate tags                 (marked source="ai"; user removals respected)
 10. Store everything as AI-generated data, with its uncertainty
 11. Record a revision and refresh the search index
"""
from __future__ import annotations

import dataclasses
import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import CreativeEntry, Origin, Project, Relationship, utcnow
from app.services import text as T
from app.services.ai import AIProvider, AIProviderError, EntryContext, get_ai_provider
from app.services.history import record_revision, replace_ai_tags
from app.services.relationships import relationship_candidates, store_suggestions
from app.services.search import index_entry

log = logging.getLogger("nexus.organizer")


@dataclass
class OrganizeOutcome:
    ok: bool
    provider: str
    relationships: list[Relationship]
    error: str | None = None


class Organizer:
    def __init__(self, db: Session, provider: AIProvider | None = None):
        self.db = db
        self._provider = provider

    @property
    def provider(self) -> AIProvider:
        if self._provider is None:
            self._provider = get_ai_provider()
        return self._provider

    def organize(self, entry: CreativeEntry) -> OrganizeOutcome:
        """Run the AI and store its results.

        Slow model calls happen first, with no database write pending, so the
        archive never stays locked while the model thinks (captures keep
        working). Results are then written in one quick step, based on a fresh
        read of the entry so edits made meanwhile are respected.
        """
        # Phase 1 - analyse (no writes pending)
        ctx = EntryContext.from_entry(entry)
        self.db.commit()
        try:
            provider = self.provider
            result = provider.analyze_entry(ctx)
        except Exception as exc:  # AI failure must never affect the stored original
            error = str(exc) if isinstance(exc, AIProviderError) else f"AI analysis failed: {exc}"
            self.db.expire(entry)
            meta = dict(entry.ai_metadata or {})
            meta["analysis_error"] = error
            entry.ai_metadata = meta
            self.db.flush()
            index_entry(self.db, entry)
            return OrganizeOutcome(False, getattr(self._provider, "name", "unknown"), [], error)

        # Phase 2 - related entries (still no writes pending)
        new_ctx = dataclasses.replace(
            ctx,
            title=ctx.title if ctx.title_is_user else result.title,
            entry_type=ctx.entry_type if entry.entry_type_source == Origin.user.value else result.entry_type,
            concepts=result.concepts,
            summary=result.summary,
        )
        suggestions = []
        candidates = relationship_candidates(self.db, entry.id)
        if candidates:
            try:
                suggestions = provider.find_relationships(new_ctx, candidates)
            except Exception:  # relationship hints are optional; keep the analysis
                log.exception("Relationship discovery failed for entry %s", entry.id)

        # Phase 3 - write everything quickly, on a fresh copy of the entry
        self.db.expire(entry)
        if entry.deleted_at is not None:
            return OrganizeOutcome(False, provider.name, [], "Entry was moved to the trash during analysis.")
        meta = dict(entry.ai_metadata or {})

        # Titles / types the user chose are never overwritten by the AI.
        if entry.title_source != Origin.user.value:
            entry.title = result.title[:300]
            entry.title_source = Origin.ai.value
        if entry.entry_type_source != Origin.user.value:
            entry.entry_type = result.entry_type
            entry.entry_type_source = Origin.ai.value

        entry.ai_summary = result.summary
        entry.ai_interpretation = result.interpretation
        entry.ai_inference = result.inference
        entry.confidence = result.confidence
        used_by = result.provider_name or provider.name
        entry.ai_provider = used_by
        entry.analyzed_at = utcnow()

        meta.pop("analysis_error", None)
        meta.update(
            suggested_title=result.title,
            suggested_entry_type=result.entry_type,
            type_confidence=result.type_confidence,
            concepts=result.concepts,
            themes=result.themes,
            keywords=result.keywords,
            uncertainties=result.uncertainties,
            method=result.method,
            potential_projects=self._potential_projects(entry, result.concepts, result.keywords),
        )
        if result.visual is not None:
            meta["visual"] = result.visual
        else:
            meta.pop("visual", None)
        entry.ai_metadata = meta

        replace_ai_tags(self.db, entry, result.tags)
        rels = store_suggestions(self.db, entry, suggestions)
        if rels:
            entry.ai_inference = (entry.ai_inference or "") + (
                f"\nMay connect to {len(rels)} existing entr{'y' if len(rels) == 1 else 'ies'} "
                "(see AI-suggested relationships)."
            )
        record_revision(self.db, entry, f"AI analysis ({used_by})")
        index_entry(self.db, entry)
        return OrganizeOutcome(True, used_by, rels)

    def _potential_projects(self, entry: CreativeEntry, concepts: list[str], keywords: list[str]) -> list[dict]:
        """Projects whose name/description share vocabulary with the entry. Suggestions only."""
        member_ids = {p.id for p in entry.projects}
        mine = set(keywords) | set(concepts)
        out = []
        for p in self.db.scalars(select(Project)).all():
            if p.id in member_ids:
                continue
            ptext = f"{p.name} {p.description or ''}"
            theirs = set(T.keywords(ptext)) | set(T.concepts_for(ptext))
            shared = mine & theirs
            if not shared:
                continue
            score = round(min(0.9, T.overlap(mine, theirs) + 0.1 * (len(shared) - 1)), 2)
            if score >= 0.2:
                out.append({"id": p.id, "name": p.name, "confidence": score,
                            "reason": "Shared vocabulary: " + ", ".join(sorted(shared)[:6])})
        return sorted(out, key=lambda d: -d["confidence"])[:5]
