"""Project analysis: an AI overview of a whole project, built from its entries.

"Look at everything in my comic": who the characters are and where they appear, the story
threads and world details, what's strongest, what contradicts itself, what's still open.
Every point cites the entries it comes from ([#id]); citations of entries that aren't in the
project are dropped. It's a reading of the material, not canon: the project's own entries
stay the source of truth.

Runs in the background worker (it can take a minute), and every run is kept as a version.
The model sees each entry's own words first, then the AI layers (summary, latest art
analysis), clearly labeled, within a fixed text budget so the prompt fits the context window.
"""
from __future__ import annotations

import logging
from collections import Counter

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.models import ArtworkAnalysis, CreativeEntry, EntryTag, Project, ProjectAnalysis, ProjectEntry, utcnow
from app.services.ai import AIProviderError, get_ai_provider

log = logging.getLogger("nexus.project_analysis")

MAX_ENTRIES = 40
BUDGET_CHARS = 20_000
ACTIVE = ("queued", "running")


class ProjectAnalysisError(ValueError):
    pass


def _s(max_len: int = 600) -> dict:
    return {"type": "string", "maxLength": max_len}


def _cited(item: dict) -> dict:
    return {"type": "object", "properties": {**item, "entries": {"type": "array", "items": {"type": "integer"}}},
            "required": [*item, "entries"]}


SCHEMA = {
    "type": "object",
    "properties": {
        "overview": _s(1200),
        "characters": {"type": "array", "items": _cited({"name": _s(120), "description": _s()})},
        "story_threads": {"type": "array", "items": _cited({"thread": _s()})},
        "world": {"type": "array", "items": _cited({"element": _s()})},
        "strongest_ideas": {"type": "array", "items": _cited({"idea": _s(), "why": _s()})},
        "contradictions": {"type": "array", "items": _cited({"issue": _s()})},
        "open_questions": {"type": "array", "items": _s(300)},
        "gaps": {"type": "array", "items": _s(300)},
        "next_steps": {"type": "array", "items": _s(300)},
        "uncertainties": {"type": "array", "items": _s(300)},
    },
    "required": ["overview", "characters", "story_threads", "world", "strongest_ideas", "contradictions",
                 "open_questions", "gaps", "next_steps", "uncertainties"],
}

SYSTEM = """You are a thoughtful creative collaborator reviewing a whole creative project (for example a comic,
a story or a game) from the entries the creator has captured: ideas, notes, voice-note transcripts and sketches.
Use ONLY the entries provided. Never invent characters, plot, names or lore that aren't in them.
- Cite entries in every "entries" list by their numbers (the N in [#N]); use only numbers that were given.
- "USER'S WORDS", "NOTES" and "TRANSCRIPT" are the creator's own; "AI SUMMARY" and "ART ANALYSIS" are earlier AI
  descriptions (for pictures: what the picture seems to show). Treat the creator's words as decisions and AI
  descriptions as readings.
- characters: each distinct character (use the creator's name for them, or a short description if unnamed).
- story_threads: premises, plot lines or scenes that belong together. world: places, rules, objects, factions, tone.
- strongest_ideas: what already works best and why. contradictions: only real conflicts between entries.
- open_questions: what the material leaves undecided. gaps: what a project like this usually needs but is missing.
- next_steps: a few optional suggestions the creator is free to ignore.
- Leave a list empty rather than padding it. Keep sentences short and specific. Reply with JSON only."""


def _load_entries(db: Session, project_id: int) -> list[CreativeEntry]:
    rows = db.scalars(select(CreativeEntry)
                      .join(ProjectEntry, ProjectEntry.entry_id == CreativeEntry.id)
                      .where(ProjectEntry.project_id == project_id, CreativeEntry.deleted_at.is_(None))
                      .options(selectinload(CreativeEntry.entry_tags).selectinload(EntryTag.tag),
                               selectinload(CreativeEntry.attachments))
                      .order_by(CreativeEntry.created_at.desc(), CreativeEntry.id.desc())
                      .limit(MAX_ENTRIES)).all()
    return list(reversed(rows))  # oldest first: the order the project grew in


def _latest_art(db: Session, ids: list[int]) -> dict[int, str]:
    out: dict[int, str] = {}
    for a in db.scalars(select(ArtworkAnalysis).where(ArtworkAnalysis.entry_id.in_(ids),
                                                      ArtworkAnalysis.status == "done")
                        .order_by(ArtworkAnalysis.analysis_version)):
        if a.visual_description:
            out[a.entry_id] = a.visual_description
    return out


def build_prompt(db: Session, project: Project, entries: list[CreativeEntry]) -> str:
    art = _latest_art(db, [e.id for e in entries])
    per_entry = max(500, BUDGET_CHARS // max(1, len(entries)))
    blocks = []
    for e in entries:
        lines = [f'ENTRY [#{e.id}] ({e.entry_type}, {e.source_type} capture, {e.created_at:%Y-%m-%d}) '
                 f'"{e.title or "Untitled"}"' + (f" — shared by {e.shared_from.get('by')}" if e.shared_from else "")]
        for label, body in (("USER'S WORDS", e.original_text), ("NOTES", e.combined_notes),
                            ("TRANSCRIPT", e.transcript), ("AI SUMMARY", e.ai_summary), ("ART ANALYSIS", art.get(e.id))):
            if body and body.strip():
                lines.append(f"{label}: {body.strip()[:900]}")
        user_tags = [et.tag.name for et in e.entry_tags if et.source == "user"]
        if user_tags:
            lines.append("CREATOR'S TAGS: " + ", ".join(user_tags))
        block = "\n".join(lines)
        blocks.append(block if len(block) <= per_entry else block[:per_entry] + " [...]")
    head = f'PROJECT: "{project.name}" ({project.project_type})'
    if project.description:
        head += f"\nTHE CREATOR'S DESCRIPTION: {project.description[:600]}"
    return head + f"\n\n{len(entries)} ENTRIES, oldest first:\n\n" + "\n\n".join(blocks)


# -- cleaning the model's reply ------------------------------------------------
def _text(v, n: int = 600) -> str:
    return " ".join(str(v).split())[:n] if isinstance(v, (str, int, float)) else ""


def clean(raw: dict, valid_ids: set[int]) -> dict:
    """Keep only what the schema allows, with citations limited to the project's entries."""
    def ids(v) -> list[int]:
        out = []
        for x in v if isinstance(v, list) else []:
            try:
                i = int(x)
            except (TypeError, ValueError):
                continue
            if i in valid_ids and i not in out:
                out.append(i)
        return out

    def items(key: str, fields: tuple[str, ...], limit: int = 12) -> list[dict]:
        out = []
        for it in raw.get(key) if isinstance(raw.get(key), list) else []:
            if not isinstance(it, dict):
                continue
            row = {f: _text(it.get(f)) for f in fields}
            if row[fields[0]]:
                out.append({**row, "entries": ids(it.get("entries"))})
        return out[:limit]

    def strings(key: str, limit: int = 10) -> list[str]:
        return [t for t in (_text(x, 300) for x in (raw.get(key) if isinstance(raw.get(key), list) else [])) if t][:limit]

    return {
        "overview": _text(raw.get("overview"), 1200),
        "characters": items("characters", ("name", "description"), 20),
        "story_threads": items("story_threads", ("thread",)),
        "world": items("world", ("element",)),
        "strongest_ideas": items("strongest_ideas", ("idea", "why"), 8),
        "contradictions": items("contradictions", ("issue",), 8),
        "open_questions": strings("open_questions"),
        "gaps": strings("gaps"),
        "next_steps": strings("next_steps", 6),
        "uncertainties": strings("uncertainties"),
    }


def heuristic_overview(entries: list[CreativeEntry]) -> dict:
    """Without a language model: an honest, modest summary of what's in the project."""
    types = Counter(e.entry_type for e in entries)
    concepts = Counter(c for e in entries for c in (e.ai_metadata or {}).get("concepts", []))
    return {
        "overview": f"{len(entries)} entries: " + ", ".join(f"{n} {t.replace('_', ' ')}" for t, n in types.most_common())
                    + ". A local language model can read them and describe the project; this is only a count.",
        "characters": [{"name": e.title or "Untitled", "description": e.ai_summary or "", "entries": [e.id]}
                       for e in entries if e.entry_type == "character"][:20],
        "story_threads": [{"thread": f"Entries about {c}", "entries": [e.id for e in entries
                           if c in (e.ai_metadata or {}).get("concepts", [])]} for c, n in concepts.most_common(5) if n > 1],
        "world": [], "strongest_ideas": [], "contradictions": [], "open_questions": [], "gaps": [], "next_steps": [],
        "uncertainties": ["Made without a language model: no reading of the story, only what the entries are."],
    }


# -- running ---------------------------------------------------------------------
def start(db: Session, project: Project) -> ProjectAnalysis:
    """Queue a new version (or return the one already running)."""
    from app.services.processing import Job, processing_queue

    running = db.scalar(select(ProjectAnalysis).where(ProjectAnalysis.project_id == project.id,
                                                      ProjectAnalysis.status.in_(ACTIVE)))
    if running is not None:
        return running
    if not _load_entries(db, project.id):
        raise ProjectAnalysisError("This project has no entries yet. Capture a few ideas into it first.")
    version = (db.scalar(select(func.max(ProjectAnalysis.version)).where(ProjectAnalysis.project_id == project.id))
               or 0) + 1
    pa = ProjectAnalysis(project_id=project.id, version=version, status="queued")
    db.add(pa)
    db.commit()
    processing_queue.submit(Job(0, transcribe=False, analyze=False, embed=False, project_analysis_id=pa.id))
    db.expire_all()
    return db.get(ProjectAnalysis, pa.id)


def run(db: Session, analysis_id: int) -> str:
    """Worker: build the prompt, call the model with nothing written, then store the result."""
    pa = db.get(ProjectAnalysis, analysis_id)
    if pa is None or pa.status == "done":
        return "skipped"
    project = db.get(Project, pa.project_id)
    if project is None or project.deleted_at is not None:
        return _fail(db, analysis_id, "The project was moved to the trash.")
    pa.status = "running"
    db.commit()
    entries = _load_entries(db, project.id)
    if not entries:
        return _fail(db, analysis_id, "The project has no entries.")
    ids = {e.id for e in entries}
    try:
        prompt = build_prompt(db, project, entries)
        db.commit()  # nothing pending while the model thinks
        provider = get_ai_provider()
        try:
            result, model = clean(provider.analyze_project(SYSTEM, prompt, SCHEMA), ids), provider.name
        except AIProviderError as exc:
            if getattr(provider, "external", False) or provider.name.startswith("ollama"):
                raise
            log.info("Project analysis without a language model: %s", exc)
            result, model = heuristic_overview(entries), provider.name
    except AIProviderError as exc:
        return _fail(db, analysis_id, str(exc))
    except Exception as exc:  # never lose the queue over one analysis
        log.exception("Project analysis %s failed", analysis_id)
        return _fail(db, analysis_id, f"Analysis failed: {exc}")
    pa = db.get(ProjectAnalysis, analysis_id)
    pa.result, pa.model, pa.entry_ids = result, model, sorted(ids)
    pa.status, pa.completed_at, pa.error = "done", utcnow(), None
    db.commit()
    return "done"


def _fail(db: Session, analysis_id: int, message: str) -> str:
    db.rollback()
    pa = db.get(ProjectAnalysis, analysis_id)
    if pa is not None:
        pa.status, pa.error, pa.completed_at = "failed", message[:1000], utcnow()
        db.commit()
    return "failed"
