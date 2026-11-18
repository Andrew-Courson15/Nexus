"""Develop this idea: the local AI offers directions and questions for one of your ideas.

Everything it says is an AI INFERENCE: options for you to consider, never decisions, and never
written into your own words. Each result is kept (newest first) in the entry's AI notes, with the
model and time, so you can compare them. Nothing is sent off the computer.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models import CreativeEntry, utcnow
from app.services.ai import AIProviderError, EntryContext, get_ai_provider

KEEP = 10  # results kept per entry
MAX_TEXT = 6000

SYSTEM = """DEVELOP AN IDEA. You help a creator (comics, film, art, stories) develop THEIR OWN idea.
Rules:
- Work only from the entry below. Don't invent facts about their world beyond what's written; when you
  propose something new, present it as an option ("could", "what if").
- Offer 3 clearly different directions the idea could grow in, each with a short name and 1-3 sentences.
- Ask 3 to 5 open questions that would help the creator decide (about character, stakes, world, form, tone).
- Suggest one small, concrete next step they could take today (a sketch, a scene, a line of dialogue to try).
- Never rewrite or judge their words. Be encouraging and specific. Plain language, no jargon.
Reply as JSON matching the schema."""

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "directions": {"type": "array", "items": {"type": "object", "properties": {
            "name": {"type": "string"}, "description": {"type": "string"}}, "required": ["name", "description"]}},
        "questions": {"type": "array", "items": {"type": "string"}},
        "next_step": {"type": "string"},
    },
    "required": ["directions", "questions", "next_step"],
}


class DevelopError(RuntimeError):
    pass


def _prompt(entry: CreativeEntry) -> str:
    ctx = EntryContext.from_entry(entry)
    parts = [f"TITLE: {entry.title or 'Untitled'}", f"TYPE: {entry.entry_type.replace('_', ' ')}"]
    if ctx.original_text:
        parts.append(f"THE CREATOR'S WORDS:\n{ctx.original_text[:MAX_TEXT]}")
    if ctx.user_notes:
        parts.append(f"THEIR NOTES:\n{ctx.user_notes[:2000]}")
    if ctx.transcript:
        parts.append(f"THEIR VOICE NOTE (transcript):\n{ctx.transcript[:MAX_TEXT]}")
    visual = (entry.ai_metadata or {}).get("visual") or {}
    seen = [f"{k}: {', '.join(v) if isinstance(v, list) else v}" for k, v in visual.items() if v]
    if seen:
        parts.append("WHAT'S IN THEIR PICTURE (described by an AI):\n" + "\n".join(seen[:8]))
    if len(parts) == 2:
        raise DevelopError("There's nothing to develop yet: add some words or notes to this entry first.")
    return "\n\n".join(parts)


def _clean(raw: dict) -> dict:
    def text(v, limit=600):
        return " ".join(str(v or "").split())[:limit]

    directions = [{"name": text(d.get("name"), 80), "description": text(d.get("description"))}
                  for d in (raw.get("directions") or []) if isinstance(d, dict) and d.get("name")][:4]
    questions = [text(q, 300) for q in (raw.get("questions") or []) if str(q or "").strip()][:6]
    if not directions and not questions:
        raise DevelopError("The AI didn't come back with anything usable. Try again.")
    return {"directions": directions, "questions": questions, "next_step": text(raw.get("next_step"), 400)}


def develop(db: Session, entry: CreativeEntry) -> dict:
    """Ask the local model, then keep the result with the entry's AI notes. Returns the result."""
    prompt = _prompt(entry)
    entry_id = entry.id
    db.commit()  # nothing pending while the model thinks
    provider = get_ai_provider()
    if not provider.name.startswith("ollama") and not getattr(provider, "external", False):
        raise DevelopError("Developing ideas needs the local AI model (Ollama). See the setup check on the home screen.")
    try:
        result = _clean(provider.analyze_project(SYSTEM, prompt, SCHEMA))
    except AIProviderError as exc:
        raise DevelopError(str(exc).replace("analyze the project", "develop the idea")) from exc
    result.update(model=provider.name, created_at=utcnow().isoformat())
    entry = db.get(CreativeEntry, entry_id)  # fresh: edits made meanwhile are kept
    meta = dict(entry.ai_metadata or {})
    meta["developments"] = [result, *(meta.get("developments") or [])][:KEEP]
    entry.ai_metadata = meta
    db.commit()
    return result
