"""VISUAL ART INTELLIGENCE: artwork analysis for comics, illustration and visual storytelling.

The model looks at artwork the way an illustrator, comic artist, storyboard
artist and visual storyteller would: first what is actually there, then how it
works, then possibilities. It never decides for the artist.

Pieces:
- prompts + JSON schema per mode (the schema is also used to clean replies)
- `start_analysis` / `run_analysis`: queue and execute an analysis (append-only
  versions in `artwork_analyses`)
- creative memory: confirmed preferences + evidence-backed observed patterns
- art knowledge base: the artist's own reference notes, retrieved per analysis
- evolution: versions of a piece linked with `evolved_from` relationships
"""
from __future__ import annotations

import logging
import re
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.config import get_settings
from app.models import (
    MULTI_IMAGE_MODES,
    ArtKnowledge,
    ArtKnowledgeChunk,
    ArtMode,
    ArtPreference,
    ArtworkAnalysis,
    CreativeEntry,
    Origin,
    Relationship,
    RelationshipStatus,
    utcnow,
)
from app.services import text as T
from app.services.ai import AIProviderError, ArtworkRequest, get_ai_provider
from app.services.images import ensure_preview, needs_preview, visual_file

log = logging.getLogger("nexus.art")

MAX_IMAGES = 6
PATTERN_MIN_ENTRIES = 3  # an observed preference needs this many different artworks as evidence

ART_TOPICS = [
    "comic illustration", "anatomy", "perspective", "composition", "storyboarding", "inking", "coloring",
    "lettering", "sequential art", "cartooning", "character design", "cinematography", "lighting", "other",
]

# Which library topics matter most for each mode (used to rank reference notes).
MODE_TOPICS = {
    "analysis": {"composition", "comic illustration", "character design", "lighting"},
    "critique": {"composition", "comic illustration", "anatomy", "inking", "coloring", "lighting"},
    "artist": {"character design", "comic illustration", "composition"},
    "storyboard": {"storyboarding", "cinematography", "sequential art"},
    "page": {"sequential art", "composition", "lettering", "storyboarding"},
    "sequence": {"sequential art", "cinematography", "storyboarding"},
    "compare": {"composition", "anatomy", "character design"},
    "consistency": {"character design", "anatomy"},
    "evolution": {"character design", "comic illustration"},
}

# Controlled vocabulary so repeated tendencies can be counted as evidence.
STYLE_SIGNALS = [
    "heavy silhouettes", "strong character shapes", "dramatic lighting", "high contrast", "heavy blacks",
    "cinematic compositions", "dynamic poses", "strong diagonals", "low camera angles", "close-up framing",
    "exaggerated proportions", "stylized anatomy", "realistic anatomy", "loose linework", "clean linework",
    "varied line weight", "crosshatching", "textured rendering", "minimal backgrounds", "detailed backgrounds",
    "muted palette", "saturated palette", "limited palette", "warm palette", "cool palette",
    "atmospheric depth", "flat colors", "painterly rendering", "graphic shapes", "expressive faces",
]

ARTWORK_TYPES = [
    "comic_panel", "comic_page", "storyboard", "character_design", "concept_art", "illustration", "cartoon",
    "manga", "sketch", "environment_design", "creature_design", "figure_drawing", "other",
]

SHOT_TYPES = [
    "", "extreme close-up", "close-up", "medium shot", "medium long shot", "full shot", "long shot",
    "establishing shot", "over-the-shoulder", "not applicable",
]

SYSTEM_PROMPT = """You are VISUAL ART INTELLIGENCE, the art-analysis part of NEXUS, a private creative archive.
You look at artwork the way an experienced illustrator, comic artist, storyboard artist and visual storyteller would.

Principles:
1. Understand before advising. Start with what is actually visible. Never invent details. If something is unclear, say so
   ("the object appears to be a weapon, although the exact form is unclear").
2. Keep VISIBLE information separate from INTERPRETATION. Never present an interpretation as confirmed story information.
3. Stylization is not an error. Exaggerated anatomy, simplified shapes and unusual proportions may be intentional: judge
   whether they are consistent and readable, not whether they are realistic. Never recommend making work more realistic
   unless realism is clearly the goal. Judge cartoons by cartooning principles (appeal, exaggeration, silhouette, staging,
   timing, squash and stretch), not as simplified realism. Do not suggest a different palette just because another is
   more conventional; explain how the existing one functions.
4. Be specific. Never give generic advice such as "add more detail". Say where in the image, what is there now, and why
   a change would matter (e.g. "The lower third has little visual information compared with the upper half; a foreground
   shadow or debris could add depth without competing with the character.").
5. Suggestions come in three levels: strengths = PRESERVE (already working, keep it), development_opportunities =
   DEVELOP (could be strengthened, with the reason), experiments = EXPERIMENT (optional possibilities, never mandatory).
6. Offer several creative_directions (for example more cinematic / more dynamic / more atmospheric) without choosing one.
7. Explain how the viewer's eye is likely to travel through the image. Ask: what is noticed first, second; what is told
   without words and what needs dialogue or captions; would the character read as a solid black silhouette?
8. Differences between drawings are not automatically mistakes; the artist may be redesigning on purpose.
9. You are not the artist. There is no single correct way to make art. Observe, understand, explain, connect, question,
   suggest, compare. The human artist decides.
Fields that do not apply to this artwork must be empty strings or empty lists. Reply with JSON only."""

MODE_INSTRUCTIONS = {
    "analysis": "Give a full layered analysis of the artwork.",
    "critique": (
        "CRITIQUE MODE: give a deeper critique that is constructive and specific. Fill the 'critique' object: "
        "what_i_see, what_is_working, what_could_be_stronger, why_it_matters, possible_approaches, "
        "questions_to_consider, optional_experiments."),
    "artist": (
        "ARTIST MODE: act as a knowledgeable creative collaborator. Do not rewrite the artist's concept. In "
        "'collaborator_response', respond to the artist (and their question, if they asked one) grounded in what you "
        "see. Put your most useful questions in 'questions', e.g. what role an element plays in the story and how "
        "that could be reinforced visually."),
    "storyboard": (
        "STORYBOARD MODE: these are rough storyboard sketches. Do NOT criticise rendering, polish or line quality. "
        "Focus on camera, shot type, staging, action, continuity, pacing, character placement and story clarity. "
        "If there are several images they are in reading order; use 'sequence' and 'panels'."),
    "page": (
        "COMIC PAGE MODE: analyse the page as a whole in 'page': layout, panel hierarchy, reading order (say if it is "
        "ambiguous, and whether it may read right-to-left, e.g. manga; do not assume), gutter spacing, transitions, "
        "splash/establishing/close-up/action panels, dialogue density, visual climax and page-turn potential (does the "
        "last panel work as a setup or a reveal?). Describe each panel in 'panels'."),
    "sequence": (
        "SEQUENCE MODE: the images are panels in reading order (Image 1 first). In 'panels', for each one say what "
        "information the reader receives and what changes from the previous panel. In 'sequence', evaluate "
        "continuity, character position, screen direction, camera consistency, time and action progression, "
        "transitions, pacing, panel rhythm, establishing information, cause and effect and reader comprehension."),
    "compare": (
        "COMPARE MODE: Image 1 is A and Image 2 is B. In 'comparison', explain what changed (composition, character, "
        "perspective, lighting, anatomy, linework, storytelling, readability) and the effect of each change. Do not "
        "declare one image better."),
    "consistency": (
        "CHARACTER CONSISTENCY MODE: the images show the same character. In 'consistency', compare facial structure, "
        "hair, costume, proportions, accessories, silhouette, weapon and markings. List differences, list the ones that "
        "look possibly intentional (a redesign), and what stays constant. Never call a difference a mistake outright."),
    "evolution": (
        "EVOLUTION MODE: the images are versions of the same piece or character in chronological order (Image 1 is the "
        "earliest). In 'consistency', describe how it evolved from version to version, what each change achieved, and "
        "what stayed constant (its identity). Use 'panels' for one note per version."),
}


# --------------------------------------------------------------------------- #
# Answer format
# --------------------------------------------------------------------------- #
def _s(enum: list[str] | None = None) -> dict:
    return {"type": "string", **({"enum": enum} if enum else {})}


def _list(item: dict | None = None) -> dict:
    return {"type": "array", "items": item or _s()}


def _obj(fields: dict[str, dict]) -> dict:
    return {"type": "object", "properties": fields, "required": list(fields)}


def _strings(*names: str) -> dict:
    return _obj({n: _s() for n in names})


PANELS = _list(_obj({"panel": _s(), "information": _s(), "what_changes": _s()}))


def art_schema(mode: str) -> dict:
    """JSON schema for a mode. Also the whitelist used to clean the model's reply."""
    props: dict[str, dict] = {
        "visual_description": _s(),
        "artwork_type": _s(ARTWORK_TYPES),
        "composition": _strings("focal_point", "character_placement", "depth_layers", "negative_space",
                                "directional_movement", "eye_flow", "balance", "cropping"),
        "artistic_analysis": _strings("shape_language", "silhouette", "linework", "value", "lighting", "color",
                                      "perspective", "depth", "texture_and_detail", "stylization"),
        "character_analysis": _strings("silhouette", "pose_and_gesture", "expression", "costume_and_design",
                                       "proportions_and_anatomy", "readability"),
        "environment": _s(),
        "storytelling": _strings("visible_information", "possible_interpretation", "visual_hierarchy",
                                 "emotional_read", "told_without_words", "needs_words"),
        "sequential_art": _obj({"panel_function": _s(), "shot_type": _s(SHOT_TYPES), "camera_angle": _s(),
                                "pacing": _s(), "reader_eye_flow": _s()}),
        "strengths": _list(),
        "development_opportunities": _list(),
        "experiments": _list(),
        "creative_directions": _list(_obj({"name": _s(), "description": _s()})),
        "questions": _list(),
        "uncertainties": _list(),
        "style_signals": _list(_s(STYLE_SIGNALS)),
        "knowledge_references": _list(),
    }
    if mode == "critique":
        props["critique"] = _obj({
            "what_i_see": _s(), "what_is_working": _s(), "what_could_be_stronger": _s(), "why_it_matters": _s(),
            "possible_approaches": _list(), "questions_to_consider": _list(), "optional_experiments": _list()})
    if mode == "artist":
        props["collaborator_response"] = _s()
    if mode == "page":
        props["page"] = _strings("layout", "panel_hierarchy", "reading_order", "gutters", "transitions",
                                 "dialogue_density", "visual_climax", "page_turn")
        props["panels"] = PANELS
    if mode in ("sequence", "storyboard"):
        props["sequence"] = _strings("continuity", "screen_direction", "camera_consistency", "time_progression",
                                     "action_progression", "transitions", "pacing", "reader_comprehension")
        props["panels"] = PANELS
    if mode == "compare":
        props["comparison"] = _obj({"what_changed": _list(), **{k: _s() for k in (
            "composition", "character", "perspective", "lighting", "anatomy", "linework", "storytelling",
            "readability")}})
    if mode in ("consistency", "evolution"):
        props["consistency"] = _obj({**{k: _s() for k in (
            "facial_structure", "hair", "costume", "proportions", "accessories", "silhouette", "weapon", "markings")},
            "differences": _list(), "possibly_intentional": _list(), "constants": _list()})
        if mode == "evolution":
            props["panels"] = PANELS
    return _obj(props)


def clean(value: Any, schema: dict, max_items: int = 12) -> Any:
    """Keep only what the schema allows: known keys, sane lengths, valid enum values."""
    kind = schema.get("type")
    if kind == "object":
        src = value if isinstance(value, dict) else {}
        return {k: clean(src.get(k), sub) for k, sub in schema["properties"].items()}
    if kind == "array":
        items = value if isinstance(value, list) else ([value] if isinstance(value, (str, dict)) and value else [])
        out = []
        for item in items:
            c = clean(item, schema["items"])
            if c in ("", {}, None) or (isinstance(c, dict) and not any(c.values())):
                continue
            if c not in out:
                out.append(c)
            if len(out) >= max_items:
                break
        return out
    text = "" if value is None or isinstance(value, (dict, list)) else " ".join(str(value).split())[:2500]
    if "enum" in schema:
        low = text.lower()
        return next((e for e in schema["enum"] if e.lower() == low), "")
    return text


# --------------------------------------------------------------------------- #
# Images, context, knowledge, preferences
# --------------------------------------------------------------------------- #
def entry_image(entry: CreativeEntry):
    """The entry's picture: an image upload, or failing that a document with a preview (e.g. a PDF)."""
    return (next((a for a in entry.attachments if a.kind == "image"), None)
            or next((a for a in entry.attachments if needs_preview(a.mime_type, a.size_bytes)), None))


def _load_entries(db: Session, ids: list[int]) -> list[CreativeEntry]:
    rows = db.scalars(select(CreativeEntry).where(CreativeEntry.id.in_(ids), CreativeEntry.deleted_at.is_(None))
                      .options(selectinload(CreativeEntry.attachments), selectinload(CreativeEntry.projects))).all()
    by_id = {e.id: e for e in rows}
    return [by_id[i] for i in ids if i in by_id]


def check_images(entries: list[CreativeEntry]) -> list[str]:
    """Problems that stop an analysis (missing or unreadable images). Makes missing previews; caller commits."""
    problems = []
    for e in entries:
        img = entry_image(e)
        if img is None:
            problems.append(f"“{e.title or 'Untitled'}” has no image.")
        elif problem := ensure_preview(img):
            problems.append(f"Couldn't read {problem}.")
        elif visual_file(img) is None:
            problems.append(f"“{img.original_filename}” ({img.mime_type}) can't be shown to the local model.")
    return problems


def confirmed_preferences(db: Session) -> list[str]:
    return list(db.scalars(select(ArtPreference.statement).where(ArtPreference.status == "confirmed")
                           .order_by(ArtPreference.id)))


def chunk_text(text: str, size: int = 900) -> list[str]:
    paras = [p.strip() for p in re.split(r"\n\s*\n", text or "") if p.strip()]
    chunks, cur = [], ""
    for p in paras:
        while len(p) > size:  # very long paragraph: split on sentences
            cut = p.rfind(". ", 0, size)
            cut = cut + 1 if cut > size // 3 else size
            chunks.append(p[:cut].strip())
            p = p[cut:].strip()
        if cur and len(cur) + len(p) + 2 > size:
            chunks.append(cur)
            cur = p
        else:
            cur = f"{cur}\n\n{p}" if cur else p
    if cur:
        chunks.append(cur)
    return chunks


def index_knowledge(db: Session, item: ArtKnowledge) -> None:
    """(Re)build the passages of a library item; add meaning vectors when available."""
    from app.services.embeddings import EmbeddingError, get_embedding_provider, normalize, pack

    item.chunks.clear()
    db.flush()
    provider = get_embedding_provider()
    use_vectors = provider.name != "none"
    for i, passage in enumerate(chunk_text(item.content)):
        vector = model = None
        if use_vectors:
            try:
                vector = pack(normalize(provider.embed_document(f"{item.title}\n{passage}")))
                model = provider.model
            except EmbeddingError:
                use_vectors = False  # keyword matching still works; stop trying for this item
        item.chunks.append(ArtKnowledgeChunk(idx=i, text=passage, vector=vector, model=model))


def relevant_knowledge(db: Session, query: str, mode: str, limit: int = 3) -> list[dict]:
    """The artist's reference passages most relevant to this analysis."""
    from app.services.embeddings import EmbeddingError, dot, get_embedding_provider, normalize, unpack

    rows = db.execute(select(ArtKnowledgeChunk, ArtKnowledge).join(ArtKnowledge)
                      .where(ArtKnowledge.deleted_at.is_(None))).all()
    if not rows:
        return []
    topics = MODE_TOPICS.get(mode, set())
    provider = get_embedding_provider()
    qvec = None
    if provider.name != "none":
        try:
            qvec = normalize(provider.embed_query(query))
        except EmbeddingError:
            qvec = None
    qwords = set(T.keywords(query))
    scored = []
    for chunk, item in rows:
        topic_bonus = 0.15 if item.topic in topics else 0.0
        if qvec is not None and chunk.vector and chunk.model == provider.model:
            score = dot(qvec, unpack(chunk.vector)) + topic_bonus
            keep = score >= 0.45
        else:
            shared = qwords & set(T.keywords(f"{item.title} {chunk.text}"))
            score = len(shared) / (len(qwords) or 1) + topic_bonus
            keep = len(shared) >= 2 or topic_bonus > 0
        if keep:
            scored.append((score, chunk, item))
    scored.sort(key=lambda t: -t[0])
    out, seen = [], set()
    for _, chunk, item in scored:
        if item.id in seen:
            continue
        seen.add(item.id)
        out.append({"id": item.id, "title": item.title, "source": item.source, "topic": item.topic,
                    "excerpt": chunk.text[:900]})
        if len(out) >= limit:
            break
    return out


def refresh_observed_preferences(db: Session) -> None:
    """Turn style signals seen across several different artworks into *suggested* observations.

    Nothing becomes a preference until the artist confirms it; rejected ones never come back.
    """
    latest: dict[int, list[str]] = {}
    for a in db.scalars(select(ArtworkAnalysis).where(ArtworkAnalysis.status == "done")
                        .order_by(ArtworkAnalysis.entry_id, ArtworkAnalysis.analysis_version)):
        latest[a.entry_id] = (a.details or {}).get("style_signals", [])
    alive = set(db.scalars(select(CreativeEntry.id).where(CreativeEntry.deleted_at.is_(None))))
    counts: dict[str, list[int]] = {}
    for entry_id, signals in latest.items():
        if entry_id in alive:
            for s in set(signals):
                counts.setdefault(s, []).append(entry_id)
    existing = {p.statement.lower(): p for p in db.scalars(select(ArtPreference))}
    for signal, entries in counts.items():
        if len(entries) < PATTERN_MIN_ENTRIES:
            continue
        pref = existing.get(signal.lower())
        if pref is None:
            db.add(ArtPreference(statement=signal, origin="observed", status="suggested", evidence=sorted(entries)))
        elif pref.origin == "observed" and pref.status == "suggested":
            pref.evidence = sorted(entries)


# --------------------------------------------------------------------------- #
# Evolution (art development history)
# --------------------------------------------------------------------------- #
def evolution_chain(db: Session, entry_id: int) -> list[CreativeEntry]:
    """All versions linked to this entry by `evolved_from`, oldest first."""
    rels = db.scalars(select(Relationship).where(
        Relationship.relationship_type == "evolved_from",
        or_(Relationship.origin == Origin.user.value, Relationship.status == RelationshipStatus.accepted.value),
    )).all()
    graph: dict[int, set[int]] = {}
    for r in rels:
        graph.setdefault(r.source_entry_id, set()).add(r.target_entry_id)
        graph.setdefault(r.target_entry_id, set()).add(r.source_entry_id)
    seen, todo = {entry_id}, [entry_id]
    while todo:
        for nxt in graph.get(todo.pop(), ()):
            if nxt not in seen:
                seen.add(nxt)
                todo.append(nxt)
    entries = _load_entries(db, list(seen))
    return sorted(entries, key=lambda e: (e.created_at, e.id))


# --------------------------------------------------------------------------- #
# Running analyses
# --------------------------------------------------------------------------- #
class ArtError(ValueError):
    pass


def start_analysis(db: Session, entry: CreativeEntry, mode: str, related_ids: list[int] | None = None,
                   question: str | None = None) -> ArtworkAnalysis:
    """Validate, create a queued analysis version, and hand it to the background worker."""
    from app.services.processing import Job, processing_queue

    if mode not in {m.value for m in ArtMode}:
        raise ArtError(f"Unknown mode '{mode}'.")
    related_ids = [i for i in dict.fromkeys(related_ids or []) if i != entry.id]
    entries = [entry] + _load_entries(db, related_ids)
    if len(entries) - 1 != len(related_ids):
        raise ArtError("Some of the selected artworks no longer exist.")
    if len(entries) > MAX_IMAGES:
        raise ArtError(f"Choose at most {MAX_IMAGES} images at a time.")
    if mode in MULTI_IMAGE_MODES and len(entries) < 2:
        raise ArtError("This mode compares several images: select at least two.")
    if mode == "compare" and len(entries) != 2:
        raise ArtError("Compare works on exactly two images (A and B).")
    if mode not in MULTI_IMAGE_MODES and mode != "storyboard" and len(entries) > 1:
        raise ArtError("This mode looks at one image.")
    if problems := check_images(entries):
        raise ArtError(" ".join(problems))

    version = (db.scalar(select(func.max(ArtworkAnalysis.analysis_version))
                         .where(ArtworkAnalysis.entry_id == entry.id)) or 0) + 1
    analysis = ArtworkAnalysis(entry_id=entry.id, analysis_version=version, mode=mode,
                               related_entry_ids=related_ids, question=(question or "").strip() or None,
                               status="queued")
    db.add(analysis)
    db.commit()
    processing_queue.submit(Job(entry.id, transcribe=False, analyze=False, embed=False,
                                art_analysis_id=analysis.id))
    db.expire_all()
    return analysis


def _build_request(db: Session, analysis: ArtworkAnalysis) -> ArtworkRequest:
    wanted = [analysis.entry_id] + list(analysis.related_entry_ids or [])
    entries = _load_entries(db, wanted)
    if not entries or entries[0].id != analysis.entry_id:
        raise ArtError("The artwork was moved to the trash.")
    if len(entries) != len(wanted):
        raise ArtError("Some of the selected artworks were moved to the trash, so this analysis can't run as asked.")
    if problems := check_images(entries):
        raise ArtError(" ".join(problems))
    images, lines, words = [], [], []
    for n, e in enumerate(entries, 1):
        img = entry_image(e)
        path, mime = visual_file(img)
        images.append({"path": str(path), "mime_type": mime})
        label = f"Image {n}: “{e.title or img.original_filename}” (captured {e.created_at:%Y-%m-%d})"
        if e.projects:
            label += " — project: " + ", ".join(p.name for p in e.projects if p.deleted_at is None)
        lines.append(label)
        own = "\n".join(t for t in (e.original_text, e.user_notes) if t)
        if own.strip():
            words.append(f"About image {n}:\n<<<\n{own.strip()[:2000]}\n>>>")

    query = " ".join([analysis.mode, analysis.question or ""] + [
        f"{e.title} {e.ai_summary or ''} {' '.join(T.keywords(e.original_text or ''))}" for e in entries])
    knowledge = relevant_knowledge(db, query, analysis.mode)
    prefs = confirmed_preferences(db)

    parts = [MODE_INSTRUCTIONS[analysis.mode], "IMAGES (attached in this order):\n" + "\n".join(lines)]
    if words:
        parts.append("THE ARTIST'S OWN WORDS (facts about intent come only from here):\n" + "\n\n".join(words))
    if prefs:
        parts.append("PREFERENCES THE ARTIST HAS CONFIRMED (context, not rules; never force them):\n"
                     + "\n".join(f"- {p}" for p in prefs))
    if knowledge:
        parts.append("REFERENCE NOTES FROM THE ARTIST'S OWN LIBRARY. When a point draws on one, put its exact title in "
                     "'knowledge_references' and mention it (\"as your notes on … put it\"):\n"
                     + "\n\n".join(f"[{k['title']}]{' (' + k['source'] + ')' if k['source'] else ''}\n{k['excerpt']}"
                                   for k in knowledge))
    if analysis.question:
        parts.append(f"THE ARTIST ASKS:\n{analysis.question}")
    parts.append("style_signals: choose only from this list, and only those that clearly apply (0-5): "
                 + ", ".join(STYLE_SIGNALS))
    return ArtworkRequest(mode=analysis.mode, system=SYSTEM_PROMPT, prompt="\n\n".join(parts),
                          schema=art_schema(analysis.mode), images=images,
                          knowledge_titles=[k["title"] for k in knowledge])


def run_analysis(db: Session, analysis_id: int) -> str:
    """Execute one queued analysis. The slow model call happens with no database write pending."""
    analysis = db.get(ArtworkAnalysis, analysis_id)
    if analysis is None or analysis.status == "done":
        return "skipped"
    analysis.status = "running"
    db.commit()
    try:
        request = _build_request(db, analysis)
        db.commit()
        provider = get_ai_provider()
        raw = provider.analyze_artwork(request)
    except (ArtError, AIProviderError) as exc:
        return _fail(db, analysis_id, str(exc))
    except Exception as exc:  # never lose the queue over one analysis
        log.exception("Art analysis %s failed", analysis_id)
        return _fail(db, analysis_id, f"Analysis failed: {exc}")

    try:
        return _store_result(db, analysis_id, request, provider, raw)
    except Exception as exc:
        log.exception("Saving art analysis %s failed", analysis_id)
        return _fail(db, analysis_id, f"Couldn't save the analysis: {exc}")


def _store_result(db: Session, analysis_id: int, request: ArtworkRequest, provider, raw: dict) -> str:
    data = clean(raw, request.schema)
    allowed = {t.lower(): t for t in request.knowledge_titles}
    data["knowledge_references"] = [allowed[r.lower()] for r in data["knowledge_references"] if r.lower() in allowed]
    analysis = db.get(ArtworkAnalysis, analysis_id)
    if analysis is None:
        return "skipped"
    analysis.visual_description = data.pop("visual_description")
    analysis.composition_analysis = data.pop("composition")
    analysis.artistic_analysis = data.pop("artistic_analysis")
    analysis.character_analysis = data.pop("character_analysis")
    analysis.storytelling_analysis = data.pop("storytelling")
    analysis.sequential_art_analysis = data.pop("sequential_art")
    analysis.strengths = data.pop("strengths")
    analysis.development_opportunities = data.pop("development_opportunities")
    analysis.experiments = data.pop("experiments")
    analysis.uncertainties = data.pop("uncertainties")
    analysis.details = data  # mode extras, creative directions, questions, style signals, references ...
    analysis.model = request.model_used or provider.name
    analysis.status = "done"
    analysis.error = None
    analysis.completed_at = utcnow()
    db.flush()
    refresh_observed_preferences(db)
    db.commit()
    return "done"


def _fail(db: Session, analysis_id: int, message: str) -> str:
    db.rollback()
    analysis = db.get(ArtworkAnalysis, analysis_id)
    if analysis is not None:
        analysis.status = "failed"
        analysis.error = message
        analysis.completed_at = utcnow()
        db.commit()
    return "failed"


def maybe_auto_analyze(db: Session, entry: CreativeEntry) -> ArtworkAnalysis | None:
    """After a new image is organised: queue the first layered analysis (once), if a vision model is set up."""
    if (not get_settings().art_auto_analyze or not any(a.kind == "image" for a in entry.attachments)
            or check_images([entry])):
        return None
    try:
        provider = get_ai_provider()
    except AIProviderError:
        return None
    if not getattr(provider, "supports_art", False):
        return None
    if db.scalar(select(func.count()).where(ArtworkAnalysis.entry_id == entry.id)):
        return None
    return start_analysis(db, entry, "analysis")
