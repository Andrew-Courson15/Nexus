"""Local language-model provider using Ollama (https://ollama.com).

Everything stays on your machine: NEXUS talks to the Ollama server at
`NEXUS_OLLAMA_URL` (default http://127.0.0.1:11434).

Design notes
------------
* Structured output: every analysis call passes a JSON schema to Ollama's
  `format` option, and the reply is validated again here. Bad values are
  clamped or dropped, never trusted blindly.
* Images: when the entry has an image and the model supports vision, the
  image is sent along so the model can describe it (visual metadata).
* Fallback: if Ollama is not running, the model isn't installed, or the reply
  is unusable, the offline heuristic provider does the work and the entry
  records why. A capture never fails because of the model.
"""
from __future__ import annotations

import base64
import dataclasses
import json
import re
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, field_validator

from app.config import get_settings
from app.models import EntryType, RelationshipType
from app.services.images import DIRECT_MIME
from app.services.ai import (
    AIProvider,
    AIProviderError,
    AnalysisResult,
    ArtworkRequest,
    EntryContext,
    LocalHeuristicProvider,
    RelationshipSuggestion,
)

ENTRY_TYPES = [e.value for e in EntryType]
RELATIONSHIP_TYPES = [r.value for r in RelationshipType]
MAX_IMAGES = 3
MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_TEXT_CHARS = 12_000
RELATIONSHIP_CANDIDATES = 8
LONG_PART_CHARS = 6000     # very long material is read in parts of about this size
MAX_LONG_PARTS = 12        # ~70k characters, roughly an hour of speech
PART_SUMMARY_PROMPT = (
    "You summarize one part of a longer piece of a creator's material (often a voice note transcript). "
    "Keep every idea, name, character, place, decision and open question it mentions, in the creator's own terms. "
    "Don't add anything or interpret. 3-8 sentences. Reply as JSON: {\"summary\": \"...\"}")
MAX_RELATIONSHIP_SUGGESTIONS = 3   # show only the strongest few
MODEL_RELATIONSHIP_MIN = 0.7       # the model's own confidence a suggestion needs

_TYPE_SYNONYMS = {
    "film": "movie", "feature": "movie", "screenplay": "movie", "short film": "short_film", "short": "short_film",
    "drawing": "sketch", "doodle": "sketch", "art": "artwork", "painting": "artwork", "illustration": "artwork",
    "world": "worldbuilding", "lore": "worldbuilding", "novel": "story", "brain dump": "brain_dump",
    "graphic novel": "comic", "manga": "comic",
}


class OllamaError(RuntimeError):
    pass


# --------------------------------------------------------------------------- #
# HTTP client
# --------------------------------------------------------------------------- #
class OllamaClient:
    def __init__(self, base_url: str, timeout: float, keep_alive: str = "10m",
                 transport: httpx.BaseTransport | None = None):
        self.base_url = base_url.rstrip("/")
        self.keep_alive = keep_alive
        # trust_env=False: never route local-model traffic through a proxy.
        self._http = httpx.Client(base_url=self.base_url, timeout=httpx.Timeout(timeout, connect=5.0),
                                  trust_env=False, transport=transport)

    def _post(self, path: str, payload: dict, timeout: float | None = None) -> dict:
        try:
            r = self._http.post(path, json=payload, timeout=timeout) if timeout else self._http.post(path, json=payload)
        except httpx.ConnectError as exc:
            raise OllamaError(f"Ollama is not reachable at {self.base_url}. Is the Ollama app running?") from exc
        except httpx.TimeoutException as exc:
            raise OllamaError("The local model took too long to answer.") from exc
        except httpx.HTTPError as exc:
            raise OllamaError(f"Could not talk to Ollama: {exc}") from exc
        if r.status_code == 404 and "model" in payload:
            raise OllamaError(f"Model '{payload['model']}' is not installed. Run: ollama pull {payload['model']}")
        if r.status_code >= 400:
            try:
                detail = r.json().get("error", r.text)
            except ValueError:
                detail = r.text
            raise OllamaError(f"Ollama error ({r.status_code}): {str(detail)[:300]}")
        try:
            return r.json()
        except ValueError as exc:
            raise OllamaError("Ollama returned something that isn't JSON.") from exc

    def list_models(self, timeout: float = 3.0) -> list[str]:
        try:
            r = self._http.get("/api/tags", timeout=timeout)
            r.raise_for_status()
        except httpx.HTTPError as exc:
            raise OllamaError(f"Ollama is not reachable at {self.base_url}.") from exc
        return [m.get("name", "") for m in r.json().get("models", [])]

    def show(self, model: str, timeout: float = 5.0) -> dict:
        return self._post("/api/show", {"model": model}, timeout=timeout)

    def chat(self, model: str, messages: list[dict], schema: dict | None = None,
             temperature: float = 0.2) -> str:
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": False,
            "keep_alive": self.keep_alive,
            "options": {"temperature": temperature, "num_ctx": 8192},
        }
        if schema is not None:
            payload["format"] = schema
        data = self._post("/api/chat", payload)
        content = (data.get("message") or {}).get("content")
        if not isinstance(content, str):
            raise OllamaError("Ollama reply had no message content.")
        return content

    def chat_json(self, model: str, messages: list[dict], schema: dict) -> dict:
        content = self.chat(model, messages, schema)
        return parse_json_reply(content)


def parse_json_reply(content: str) -> dict:
    """Parse a model's JSON reply, tolerating code fences and stray text."""
    text = content.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fenced:
        text = fenced.group(1).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise OllamaError("The local model did not return valid JSON.") from None
        try:
            data = json.loads(text[start:end + 1])
        except json.JSONDecodeError as exc:
            raise OllamaError("The local model did not return valid JSON.") from exc
    if not isinstance(data, dict):
        raise OllamaError("The local model returned JSON of the wrong shape.")
    return data


# --------------------------------------------------------------------------- #
# Reply validation
# --------------------------------------------------------------------------- #
def _str_list(v: Any, max_items: int = 10, max_len: int = 80) -> list[str]:
    if v is None:
        return []
    if isinstance(v, str):
        v = [p for p in re.split(r"[,;\n]", v)]
    out: list[str] = []
    for item in v if isinstance(v, list) else []:
        if not isinstance(item, (str, int, float)):
            continue
        s = " ".join(str(item).split()).lstrip(" -•*")
        if max_len <= 80:  # tags/concepts: drop trailing punctuation; sentences keep theirs
            s = s.rstrip(" .;,")
        s = s[:max_len]
        if s and s.lower() not in (o.lower() for o in out):
            out.append(s)
        if len(out) >= max_items:
            break
    return out


def _clamp(v: Any, default: float = 0.5) -> float:
    try:
        return round(min(1.0, max(0.0, float(v))), 2)
    except (TypeError, ValueError):
        return default


class _Visual(BaseModel):
    model_config = ConfigDict(extra="ignore")
    possible_type: str | None = None
    visual_subjects: list[str] = []
    style: str | None = None
    characters: list[str] = []
    objects: list[str] = []
    colors: list[str] = []
    setting: str | None = None

    @field_validator("visual_subjects", "characters", "objects", "colors", mode="before")
    @classmethod
    def _lists(cls, v):
        return _str_list(v, 12)


class _Analysis(BaseModel):
    model_config = ConfigDict(extra="ignore")
    title: str = ""
    entry_type: str = "other"
    type_confidence: float = 0.5
    summary: str = ""
    interpretation: str = ""
    inferences: list[str] = []
    concepts: list[str] = []
    themes: list[str] = []
    tags: list[str] = []
    uncertainties: list[str] = []
    confidence: float = 0.5
    visual: _Visual | None = None

    @field_validator("inferences", "uncertainties", mode="before")
    @classmethod
    def _sentences(cls, v):
        return _str_list(v, 8, 400)

    @field_validator("concepts", "themes", "tags", mode="before")
    @classmethod
    def _short(cls, v):
        return _str_list(v, 8, 60)

    @field_validator("type_confidence", "confidence", mode="before")
    @classmethod
    def _conf(cls, v):
        return _clamp(v)

    @field_validator("title", "summary", "interpretation", "entry_type", mode="before")
    @classmethod
    def _text(cls, v):
        return "" if v is None else str(v).strip()


def _analysis_schema(with_visual: bool) -> dict:
    str_list = {"type": "array", "items": {"type": "string"}}
    props: dict[str, Any] = {
        "title": {"type": "string"},
        "entry_type": {"type": "string", "enum": ENTRY_TYPES},
        "type_confidence": {"type": "number"},
        "summary": {"type": "string"},
        "interpretation": {"type": "string"},
        "inferences": str_list,
        "concepts": str_list,
        "themes": str_list,
        "tags": str_list,
        "uncertainties": str_list,
        "confidence": {"type": "number"},
    }
    required = list(props)
    if with_visual:
        props["visual"] = {
            "type": "object",
            "properties": {
                "possible_type": {"type": "string", "enum": ENTRY_TYPES},
                "visual_subjects": str_list, "style": {"type": "string"}, "characters": str_list,
                "objects": str_list, "colors": str_list, "setting": {"type": "string"},
            },
            "required": ["possible_type", "visual_subjects", "style", "characters", "objects", "colors", "setting"],
        }
        required.append("visual")
    return {"type": "object", "properties": props, "required": required}


_RELATIONSHIP_SCHEMA = {
    "type": "object",
    "properties": {
        "relationships": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "candidate_id": {"type": "integer"},
                    "relationship_type": {"type": "string", "enum": RELATIONSHIP_TYPES},
                    "confidence": {"type": "number"},
                    "rationale": {"type": "string"},
                },
                "required": ["candidate_id", "relationship_type", "confidence", "rationale"],
            },
        }
    },
    "required": ["relationships"],
}

ANALYSIS_SYSTEM_PROMPT = f"""You are the organizing layer of NEXUS, a private creative archive that belongs to one person.
You describe and categorize their material. You never rewrite it and you never invent facts about it.

Rules:
- The user's own words are the only source of facts. Do not add names, plot points, or details they did not give.
- "summary": 1-2 sentences that faithfully restate the material.
- "interpretation": how you read it (what kind of creative material it is, genre, form, core premise).
- "inferences": things that MAY be true but were not stated (possible themes, tone, directions). Phrase each tentatively ("may", "could", "seems").
- "uncertainties": what you could not determine. If the material is short or ambiguous, say so and lower your confidence instead of guessing.
- "title": a short, evocative archive title of 2-6 words. No quotation marks.
- "entry_type": one of {", ".join(ENTRY_TYPES)}. Use "idea" for a general idea with no clear medium, "brain_dump" for loose unstructured thoughts, "other" if nothing fits.
- "concepts": 2-6 core ideas as short lowercase noun phrases. "themes": 0-5 potential themes, lowercase. "tags": 3-8 short lowercase tags useful for finding this later.
- "type_confidence" and "confidence" are numbers from 0 to 1.
- If an image is attached, describe only what is visible in "visual". Never guess what the artist intended as if it were fact.
Reply with JSON only."""

RELATIONSHIP_SYSTEM_PROMPT = f"""You link entries in a private creative archive.
Given a NEW entry and some CANDIDATE entries, return only candidates that share something SPECIFIC with the new entry:
the same character, the same story/world/premise, the same named place or object, a distinctive motif, or one clearly
being a version or development of the other.
Do NOT link entries just because they share generic features, for example: both are drawings/photos/sketches, both show
a person or figure, both have costumes, armor, a mask, a dark or night setting, a similar colour, pose or style, or both
mention common words like "website" or "comic". Those are not relationships.
Most new entries relate to zero or one candidate. Returning an empty list is normal and correct when nothing specific is shared.
relationship_type is one of: {", ".join(RELATIONSHIP_TYPES)}. Use "related_to" when unsure of the exact kind.
"rationale" is one short sentence naming the specific shared element. "confidence" is 0 to 1; use 0.8 or more only when
the shared element is unmistakable.
Use only the text provided. Reply with JSON only."""


ASK_SYSTEM_PROMPT = """You answer questions about the user's private creative archive (their ideas, stories,
characters, sketches and voice notes). Use ONLY the entries provided; never invent characters, plot, names or facts.
- Cite every point with the entry it comes from, written exactly like [#12]. Use only entry numbers that were given.
- Keep the user's ideas distinct from AI descriptions: "USER'S WORDS", "USER NOTES" and "TRANSCRIPT" are the user's
  own; "AI SUMMARY" is an earlier AI description (for pictures: what the picture seems to show). Say "the AI described"
  when you rely on an AI summary.
- If the entries don't answer the question, say so plainly and mention what they do contain.
- Be concise: one or two sentences, then a short bullet list when listing several things. Plain text, no headings."""


# The model's context is 8192 tokens (num_ctx). Entries get ~4-5k tokens between them, so the
# instructions and the question are never pushed out by long transcripts.
ASK_BUDGET_CHARS = 18_000


def ask_listing(context: list[EntryContext], max_entries: int = 12) -> str:
    blocks = []
    context = context[:max_entries]
    per_entry = max(400, ASK_BUDGET_CHARS // max(1, len(context)))
    for c in context:
        lines = [f"ENTRY [#{c.id}] ({c.entry_type}, {c.source_type} capture) \"{c.title or 'Untitled'}\""]
        for label, body in (("USER'S WORDS", c.original_text), ("USER NOTES", c.user_notes),
                            ("TRANSCRIPT", c.transcript), ("AI SUMMARY", c.summary)):
            if body and body.strip():
                lines.append(f"{label}: {body.strip()[:1200]}")
        if c.concepts:
            lines.append("CONCEPTS: " + ", ".join(c.concepts[:10]))
        block = "\n".join(lines)
        blocks.append(block if len(block) <= per_entry else block[:per_entry] + " [...]")
    return "\n\n".join(blocks)


# --------------------------------------------------------------------------- #
# Provider
# --------------------------------------------------------------------------- #
class OllamaProvider(AIProvider):
    external = False  # runs on your machine (or one you point it at)

    def __init__(self, model: str | None = None, vision_model: str | None = None,
                 client: OllamaClient | None = None):
        s = get_settings()
        self.model = model or s.ollama_model
        self.vision_model = vision_model or s.ollama_vision_model or self.model
        self.client = client or OllamaClient(s.ollama_url, s.ollama_timeout, s.ollama_keep_alive)
        self.fallback = LocalHeuristicProvider()
        self._capabilities: dict[str, list[str] | None] = {}

    @property
    def name(self) -> str:  # type: ignore[override]
        return f"ollama:{self.model}"

    # -- helpers -------------------------------------------------------------
    def _supports_vision(self, model: str) -> bool | None:
        """True/False if Ollama reports capabilities; None if it doesn't say."""
        if model not in self._capabilities:
            try:
                caps = self.client.show(model).get("capabilities")
            except OllamaError:
                return None  # Ollama down or model missing: ask again next time
            if caps is None:
                return None  # older Ollama that doesn't report capabilities
            self._capabilities[model] = caps
        return "vision" in self._capabilities[model]

    @staticmethod
    def _images(entry: EntryContext, notes: list[str]) -> list[str]:
        images = []
        for a in entry.attachments:
            if len(images) >= MAX_IMAGES or (a.get("kind") != "image" and not a.get("vision_path")):
                continue
            vision = a.get("vision_path") or (a.get("path") if a.get("mime_type") in DIRECT_MIME else None)
            if not vision:  # not JPEG/PNG, and no preview could be made from it
                notes.append(f"{a.get('original_filename')} ({a.get('mime_type')}) couldn't be converted into a "
                             "picture the local model can read; its visual content is UNKNOWN.")
                continue
            path = Path(vision)
            try:
                if path.stat().st_size > MAX_IMAGE_BYTES:
                    notes.append(f"{a.get('original_filename')} is too large to analyse; visual content is UNKNOWN.")
                    continue
                images.append(base64.b64encode(path.read_bytes()).decode("ascii"))
            except OSError:
                notes.append(f"{a.get('original_filename')} could not be read from disk.")
        return images

    @staticmethod
    def _user_message(entry: EntryContext) -> str:
        parts = [f"Capture source: {entry.source_type}."]
        if entry.title_is_user and entry.title:
            parts.append(f"The user titled it: {entry.title}")

        def block(label: str, body: str, field_name: str) -> None:
            if body.strip():
                if field_name in entry.condensed:
                    label += " — TOO LONG TO READ AT ONCE: below is an AI summary of each part, in order, not the exact words"
                clipped = body if len(body) <= MAX_TEXT_CHARS else body[:MAX_TEXT_CHARS] + "\n[...truncated]"
                parts.append(f"{label}:\n<<<\n{clipped}\n>>>")

        block("USER MATERIAL (their exact words)", entry.original_text, "original_text")
        block("USER NOTES (added later by the user)", entry.user_notes, "user_notes")
        block("TRANSCRIPT (machine transcription of the user's voice recording; may contain errors)",
              entry.transcript, "transcript")
        if not entry.analysis_text.strip():
            parts.append("There is no text. Work only from the attached image, if any, and say what is unknown.")
        return "\n\n".join(parts)

    def _condense_long(self, entry: EntryContext, notes: list[str]) -> EntryContext:
        """Very long material (e.g. a 30-minute voice log) is read in parts: the model summarizes each
        part, and the analysis works from those summaries, so the end of a long note isn't cut off."""
        changes: dict[str, str] = {}
        for name, what in (("transcript", "recording's transcript"), ("original_text", "text"), ("user_notes", "notes")):
            text = getattr(entry, name) or ""
            if len(text) <= MAX_TEXT_CHARS:
                continue
            parts = split_into_parts(text, LONG_PART_CHARS)
            read = parts[:MAX_LONG_PARTS]
            summaries = []
            for i, part in enumerate(read, 1):
                try:
                    raw = self.client.chat_json(self.model, [
                        {"role": "system", "content": PART_SUMMARY_PROMPT},
                        {"role": "user", "content": f"PART {i} OF {len(parts)}:\n<<<\n{part}\n>>>"},
                    ], {"type": "object", "properties": {"summary": {"type": "string"}}, "required": ["summary"]})
                    summary = " ".join(str(raw.get("summary", "")).split())[:1500]
                except OllamaError:
                    summary = ""
                summaries.append(f"[Part {i}/{len(parts)}] " + (summary or part[:700] + " [...]"))
            changes[name] = "\n".join(summaries)
            skipped = f" Only the first {MAX_LONG_PARTS} parts were read." if len(parts) > MAX_LONG_PARTS else ""
            notes.append(f"The {what} is long ({len(text):,} characters), so the AI read it in {len(read)} parts and "
                         f"worked from its own summary of each part; some details may be missing.{skipped} "
                         "Your full text is kept unchanged.")
        if not changes:
            return entry
        return dataclasses.replace(entry, **changes, condensed=tuple(changes))

    def _fallback_analysis(self, entry: EntryContext, reason: str) -> AnalysisResult:
        result = self.fallback.analyze_entry(entry)
        result.uncertainties.insert(
            0, f"The local model ({self.model}) wasn't used: {reason} These results come from the offline "
               "heuristic organizer instead. Re-run the organizer once Ollama is ready.")
        result.provider_name = f"{self.fallback.name} (fallback)"
        return result

    # -- AIProvider ----------------------------------------------------------
    def analyze_entry(self, entry: EntryContext) -> AnalysisResult:
        notes: list[str] = []
        images: list[str] = []
        model = self.model
        if has_image(entry):
            model = self.vision_model
            if self._supports_vision(model) is False:
                notes.append(f"The model {model} can't see images, so the picture's content is UNKNOWN. "
                             "Set NEXUS_OLLAMA_VISION_MODEL to a vision model to analyse images.")
            else:
                images = self._images(entry, notes)

        if not entry.analysis_text.strip() and not images:
            result = self.fallback.analyze_entry(entry)
            result.uncertainties = notes + result.uncertainties
            result.provider_name = self.fallback.name
            return result

        entry = self._condense_long(entry, notes)
        message: dict[str, Any] = {"role": "user", "content": self._user_message(entry)}
        if images:
            message["images"] = images
        messages = [{"role": "system", "content": ANALYSIS_SYSTEM_PROMPT}, message]
        try:
            try:
                raw = self.client.chat_json(model, messages, _analysis_schema(bool(images)))
            except OllamaError:
                if not images:
                    raise
                # Model may not accept images after all: retry with text only.
                notes.append(f"The model {model} could not analyse the image; its visual content is UNKNOWN.")
                message.pop("images", None)
                images = []
                if not entry.analysis_text.strip():
                    raise
                raw = self.client.chat_json(self.model, messages, _analysis_schema(False))
                model = self.model
            parsed = _Analysis.model_validate(raw)
        except OllamaError as exc:
            result = self._fallback_analysis(entry, str(exc))
            result.uncertainties[1:1] = notes
            return result
        except ValueError as exc:  # pydantic validation of a malformed reply
            result = self._fallback_analysis(entry, f"its reply could not be understood ({exc.__class__.__name__}).")
            result.uncertainties[1:1] = notes
            return result
        return self._to_result(parsed, entry, notes, bool(images), model)

    def _to_result(self, a: _Analysis, entry: EntryContext, notes: list[str], used_images: bool,
                   model: str) -> AnalysisResult:
        uncertainties = list(notes)
        etype = a.entry_type.lower().strip()
        etype = _TYPE_SYNONYMS.get(etype, etype.replace(" ", "_"))
        if etype not in ENTRY_TYPES:
            uncertainties.append(f"The model suggested an unknown type ('{a.entry_type}'); stored as 'other'.")
            etype, a.type_confidence = "other", min(a.type_confidence, 0.3)

        heuristic = self.fallback.analyze_entry(entry)  # cheap; supplies keywords + title fallback
        title = a.title.strip().strip('"“”\'')[:120] or heuristic.title
        concepts = [c.lower() for c in a.concepts]
        themes = [t.lower() for t in a.themes]
        tags = [t.lower() for t in a.tags] or concepts[:6]

        inference_lines = []
        if themes:
            inference_lines.append("Potential themes: " + ", ".join(themes) + ". (Inferred — not stated by you.)")
        inference_lines += a.inferences
        visual = None
        if used_images or has_image(entry):
            v = a.visual or _Visual()
            visual = {
                "possible_type": v.possible_type if v.possible_type in ENTRY_TYPES else None,
                "visual_subjects": v.visual_subjects or None,
                "style": v.style or None,
                "characters": v.characters or None,
                "objects": v.objects or None,
                "colors": v.colors or None,
                "setting": v.setting or None,
                "related_projects": None,
            }
            if not used_images:
                visual = {k: None for k in visual}
        uncertainties += a.uncertainties
        return AnalysisResult(
            title=title,
            entry_type=etype,
            type_confidence=a.type_confidence,
            summary=a.summary or heuristic.summary,
            interpretation=a.interpretation or heuristic.interpretation,
            inference="\n".join(inference_lines) or "No inferences offered.",
            concepts=concepts,
            themes=themes,
            tags=tags,
            keywords=heuristic.keywords,
            confidence=a.confidence,
            uncertainties=uncertainties,
            visual=visual,
            method=f"Local language model {model} via Ollama"
                   + (" (with image)" if used_images else "") + ". Everything stayed on this computer.",
            provider_name=f"ollama:{model}",
        )

    def summarize_entry(self, entry: EntryContext) -> str:
        try:
            raw = self.client.chat_json(self.model, [
                {"role": "system", "content": "Summarize the user's creative material faithfully in 1-2 sentences. "
                                              "Do not add details. Reply as JSON: {\"summary\": \"...\"}"},
                {"role": "user", "content": self._user_message(entry)},
            ], {"type": "object", "properties": {"summary": {"type": "string"}}, "required": ["summary"]})
            summary = str(raw.get("summary", "")).strip()
            if summary:
                return summary
        except OllamaError:
            pass
        return self.fallback.summarize_entry(entry)

    def find_relationships(self, entry: EntryContext, existing_entries: list[EntryContext]
                           ) -> list[RelationshipSuggestion]:
        threshold = get_settings().relationship_threshold
        # Cheap pre-filter so the model only judges plausible candidates: the
        # closest entries by meaning (if the meaning index is available) plus
        # entries that share concepts or words.
        pre = self.fallback.find_relationships(entry, existing_entries, threshold=0.1,
                                               limit=RELATIONSHIP_CANDIDATES)
        by_id = {c.id: c for c in existing_entries}
        ids: list[int] = []
        for eid in list(entry.similar_ids[:5]) + [p.target_id for p in pre]:
            if eid in by_id and eid not in ids:
                ids.append(eid)
        if not ids:
            return []
        candidates = [by_id[i] for i in ids[:RELATIONSHIP_CANDIDATES]]
        listing = "\n\n".join(
            f"CANDIDATE {c.id}: [{c.entry_type}] {c.title or 'Untitled'}\n"
            f"{(c.summary or c.analysis_text)[:600]}"
            for c in candidates
        )
        new_text = entry.analysis_text[:3000] or entry.summary
        try:
            raw = self.client.chat_json(self.model, [
                {"role": "system", "content": RELATIONSHIP_SYSTEM_PROMPT},
                {"role": "user", "content": f"NEW ENTRY: [{entry.entry_type}] {entry.title}\n{new_text}\n\n{listing}"},
            ], _RELATIONSHIP_SCHEMA)
        except OllamaError:
            return [p for p in pre if p.confidence >= threshold]

        out: list[RelationshipSuggestion] = []
        seen: set[int] = set()
        items = raw.get("relationships") if isinstance(raw.get("relationships"), list) else []
        for item in items:
            if not isinstance(item, dict):
                continue
            try:
                cid = int(item.get("candidate_id"))
            except (TypeError, ValueError):
                continue
            if cid not in by_id or cid in seen or cid == entry.id:  # ignore invented ids
                continue
            rtype = str(item.get("relationship_type", "related_to"))
            conf = _clamp(item.get("confidence"), 0.5)
            if conf < max(threshold, MODEL_RELATIONSHIP_MIN):
                continue
            seen.add(cid)
            out.append(RelationshipSuggestion(
                target_id=cid,
                relationship_type=rtype if rtype in RELATIONSHIP_TYPES else "related_to",
                confidence=min(conf, 0.95),
                rationale=str(item.get("rationale", "")).strip()[:300] or "Suggested by the local model.",
            ))
        return sorted(out, key=lambda r: -r.confidence)[:MAX_RELATIONSHIP_SUGGESTIONS]

    def answer_question(self, question: str, context: list[EntryContext]) -> str:
        if not context:
            return "I found nothing in your archive related to that question."
        try:
            return self.client.chat(self.model, [
                {"role": "system", "content": ASK_SYSTEM_PROMPT},
                {"role": "user", "content": f"{ask_listing(context)}\n\nQUESTION: {question}"},
            ], temperature=0.3).strip()
        except OllamaError:
            return self.fallback.answer_question(question, context)

    def analyze_project(self, system: str, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        try:
            return self.client.chat_json(self.model, [{"role": "system", "content": system},
                                                      {"role": "user", "content": prompt}], schema)
        except OllamaError as exc:
            raise AIProviderError(f"The local model couldn't analyze the project: {exc}") from exc

    supports_art = True

    def analyze_artwork(self, request: ArtworkRequest) -> dict[str, Any]:
        model = self.vision_model
        if self._supports_vision(model) is False:
            raise AIProviderError(
                f"The model {model} can't see images. Set NEXUS_OLLAMA_VISION_MODEL to a vision model "
                "(for example gemma3:12b) to use Visual Art Intelligence.")
        images = []
        for img in request.images:
            try:
                path = Path(img["path"])
                if path.stat().st_size > MAX_IMAGE_BYTES:
                    raise AIProviderError(f"{path.name} is too large to analyse (limit {MAX_IMAGE_BYTES // 2**20} MB).")
                images.append(base64.b64encode(path.read_bytes()).decode("ascii"))
            except OSError as exc:
                raise AIProviderError(f"Couldn't read an image from disk: {exc}") from exc
        messages = [{"role": "system", "content": request.system},
                    {"role": "user", "content": request.prompt, "images": images}]
        try:
            raw = self.client.chat_json(model, messages, request.schema)
        except OllamaError as exc:
            raise AIProviderError(str(exc)) from exc
        request.model_used = f"ollama:{model}"
        return raw

    def health(self) -> dict[str, Any]:
        try:
            installed = self.client.list_models(timeout=2.0)
        except OllamaError as exc:
            return {"ok": False, "detail": f"{exc} Captures still work; the offline organizer is used until it is.",
                    "model": self.model}

        def present(m: str) -> bool:
            return m in installed or f"{m}:latest" in installed or any(i.split(":")[0] == m for i in installed)

        missing = [m for m in {self.model, self.vision_model} if not present(m)]
        if missing:
            return {"ok": False, "model": self.model, "installed": installed,
                    "detail": "Ollama is running, but these models aren't installed: " + ", ".join(missing)
                              + ". Run: " + " && ".join(f"ollama pull {m}" for m in missing)}
        vision = self._supports_vision(self.vision_model)
        return {"ok": True, "model": self.model, "vision_model": self.vision_model, "installed": installed,
                "vision": vision,
                "detail": f"Connected to Ollama. Using {self.model}"
                          + ("" if vision is None else (" (can see images)." if vision else " (text only)."))}


def has_image(entry: EntryContext) -> bool:
    """An image upload, or a document with a picture preview (e.g. a PDF page)."""
    return any(a.get("kind") == "image" or a.get("vision_path") for a in entry.attachments)



def split_into_parts(text: str, size: int) -> list[str]:
    """Split text into parts of about `size` characters, at paragraph or sentence ends where possible."""
    pieces = re.split(r"(?<=[.!?])\s+|\n{2,}", text)
    parts, current = [], ""
    for piece in pieces:
        while len(piece) > size:  # one enormous "sentence" (no punctuation in the transcript)
            if current:
                parts.append(current)
                current = ""
            parts.append(piece[:size])
            piece = piece[size:]
        if current and len(current) + len(piece) + 1 > size:
            parts.append(current)
            current = piece
        else:
            current = f"{current} {piece}".strip()
    if current:
        parts.append(current)
    return parts
