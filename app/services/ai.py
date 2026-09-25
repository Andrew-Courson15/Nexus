"""AI provider abstraction.

Every AI call in NEXUS goes through an `AIProvider`. Nothing else in the
application talks to a model directly, so providers can be swapped via the
`NEXUS_AI_PROVIDER` setting.

Providers receive plain `EntryContext` objects (not database rows) and return
plain results. They never write to the database; the organizer decides what to
store, and always stores it as AI-generated (derived) data.

Available providers
-------------------
local        Offline heuristic organizer (default). Sends nothing anywhere.
openai       Reserved. Not implemented in v1.
anthropic    Reserved. Not implemented in v1.
local_model  Reserved (e.g. Ollama / llama.cpp). Not implemented in v1.
"""
from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from app.config import get_settings
from app.services import text as T


# --------------------------------------------------------------------------- #
# Data passed to and from providers
# --------------------------------------------------------------------------- #
@dataclass
class EntryContext:
    id: int | None
    title: str = ""
    original_text: str = ""
    user_notes: str = ""
    transcript: str = ""
    entry_type: str | None = None
    source_type: str = "text"
    attachments: list[dict[str, Any]] = field(default_factory=list)
    concepts: list[str] | None = None  # previously extracted, if known

    @classmethod
    def from_entry(cls, entry: Any) -> EntryContext:
        """Build a context from a CreativeEntry-like object (duck-typed, no DB import)."""
        meta = getattr(entry, "ai_metadata", None) or {}
        return cls(
            id=entry.id,
            title=entry.title or "",
            original_text=entry.original_text or "",
            user_notes=entry.user_notes or "",
            transcript=entry.transcript or "",
            entry_type=entry.entry_type,
            source_type=entry.source_type,
            attachments=[
                {"kind": a.kind, "original_filename": a.original_filename, "mime_type": a.mime_type}
                for a in getattr(entry, "attachments", [])
            ],
            concepts=meta.get("concepts"),
        )

    @property
    def analysis_text(self) -> str:
        return "\n".join(p for p in (self.original_text, self.user_notes, self.transcript) if p)


@dataclass
class AnalysisResult:
    title: str
    entry_type: str
    type_confidence: float
    summary: str
    interpretation: str
    inference: str
    concepts: list[str]
    themes: list[str]
    tags: list[str]
    keywords: list[str]
    confidence: float
    uncertainties: list[str]
    visual: dict[str, Any] | None = None
    method: str = ""


@dataclass
class RelationshipSuggestion:
    target_id: int
    relationship_type: str
    confidence: float
    rationale: str


class AIProviderError(RuntimeError):
    """Raised when a provider cannot run (not implemented, not configured, API error)."""


class AIProvider(ABC):
    name: str = "base"
    #: True if this provider sends data off the machine.
    external: bool = False

    @abstractmethod
    def analyze_entry(self, entry: EntryContext) -> AnalysisResult: ...

    @abstractmethod
    def summarize_entry(self, entry: EntryContext) -> str: ...

    @abstractmethod
    def find_relationships(
        self, entry: EntryContext, existing_entries: list[EntryContext]
    ) -> list[RelationshipSuggestion]: ...

    @abstractmethod
    def answer_question(self, question: str, context: list[EntryContext]) -> str: ...


# --------------------------------------------------------------------------- #
# Default provider: offline heuristics
# --------------------------------------------------------------------------- #
class LocalHeuristicProvider(AIProvider):
    """Rule-based organizer that runs entirely offline.

    It is intentionally modest: it extracts keywords, maps them to concepts and
    potential themes, guesses a type, and derives a title/summary from the
    user's own words. It records its uncertainty instead of guessing boldly.
    """

    name = "local-heuristic-v1"
    external = False

    _SPLIT_CLAUSE = re.compile(r",|;|\s+but\s+|\s+-\s+|\s+—\s+|:")

    # -- analysis ------------------------------------------------------------
    def analyze_entry(self, entry: EntryContext) -> AnalysisResult:
        text = entry.analysis_text.strip()
        uncertainties: list[str] = []
        visual = None
        if entry.source_type == "image" or any(a.get("kind") == "image" for a in entry.attachments):
            visual = {
                "possible_type": None,
                "visual_subjects": None,
                "style": None,
                "characters": None,
                "objects": None,
                "colors": None,
                "setting": None,
                "related_projects": None,
            }
            uncertainties.append("The image itself has not been analysed; its visual content is UNKNOWN.")
        if entry.source_type == "voice" and not entry.transcript:
            uncertainties.append("No transcript is available; what was said in the recording is UNKNOWN.")

        entry_type, type_conf = self._guess_type(text, entry)
        if visual is not None:
            visual["possible_type"] = entry_type
        if type_conf < 0.5:
            uncertainties.append(f"Entry type is uncertain (best guess: {T.type_label(entry_type)}).")

        concepts = T.concepts_for(text)
        themes = T.themes_for(concepts)
        counts = T.keyword_counts(text)
        kw = sorted(T.keywords(text), key=lambda w: -counts[w])[:12]
        if text and not concepts:
            uncertainties.append("No familiar concepts recognised; tags are based on raw keywords only.")

        title = self._title(text, entry, entry_type)
        summary = self.summarize_entry(entry, entry_type=entry_type)
        interpretation = self._interpretation(entry_type, type_conf, concepts, kw, text)
        inference = self._inference(text, concepts, themes)

        tags: list[str] = list(concepts[:6])
        for w in kw:
            if len(tags) >= 8:
                break
            if w in T.TYPE_WORDS:
                continue
            if w not in tags and T.CONCEPT_LEXICON.get(w) not in tags:
                tags.append(w)

        richness = min(1.0, len(concepts) / 3) if text else 0.0
        confidence = round(type_conf * (0.6 + 0.4 * richness), 2)
        return AnalysisResult(
            title=title,
            entry_type=entry_type,
            type_confidence=round(type_conf, 2),
            summary=summary,
            interpretation=interpretation,
            inference=inference,
            concepts=concepts,
            themes=themes,
            tags=tags,
            keywords=kw,
            confidence=confidence,
            uncertainties=uncertainties,
            visual=visual,
            method="Offline keyword heuristics (no language model). Treat as a rough first pass.",
        )

    def summarize_entry(self, entry: EntryContext, entry_type: str | None = None) -> str:
        text = entry.analysis_text.strip()
        etype = entry_type or entry.entry_type or "idea"
        label = T.type_label(etype)
        if etype not in ("idea", "note", "brain_dump", "research", "other"):
            label += " concept"
        article = "An" if label[:1].lower() in "aeiou" else "A"
        if not text:
            kind = {"image": "An image", "voice": "A voice recording"}.get(entry.source_type, "A file")
            return f"{kind} with no accompanying text yet."
        premise = T.strip_lead_in(T.first_sentence(text))
        if len(premise) > 240:
            premise = premise[:237].rsplit(" ", 1)[0] + "…"
        if premise[:1].isupper() and not premise.startswith("I ") and premise[1:2].islower():
            premise = premise[0].lower() + premise[1:]
        return f"{article} {label}: {premise}"

    def _guess_type(self, text: str, entry: EntryContext) -> tuple[str, float]:
        scores = T.type_scores(text)
        if entry.source_type == "image":
            scores["sketch"] = scores.get("sketch", 0) + 1.5
        if not scores:
            if not text:
                return ("idea" if entry.source_type == "voice" else "other"), 0.2
            return ("brain_dump" if len(T.words(text)) > 150 else "idea"), 0.25
        ranked = sorted(scores.items(), key=lambda kv: -kv[1])
        best, best_score = ranked[0]
        # "idea" is a weak catch-all; prefer a concrete type when one is present.
        if best == "idea" and len(ranked) > 1 and ranked[1][1] >= 1.5:
            best, best_score = ranked[1]
        second = next((s for t, s in ranked if t != best), 0.0)
        if best_score >= 3 and best_score - second >= 1.5:
            conf = 0.75
        elif best_score >= 2:
            conf = 0.55
        else:
            conf = 0.35
        return best, conf

    def _title(self, text: str, entry: EntryContext, entry_type: str) -> str:
        if not text:
            name = next((a.get("original_filename") for a in entry.attachments if a.get("original_filename")), "")
            stem = re.sub(r"\.[A-Za-z0-9]+$", "", name).replace("_", " ").replace("-", " ").strip()
            prefix = {"image": "Image", "voice": "Voice note"}.get(entry.source_type, "File")
            return f"{prefix}: {stem}" if stem else f"Untitled {prefix.lower()}"
        premise = T.strip_lead_in(T.first_sentence(text))
        clause = self._SPLIT_CLAUSE.split(premise)[0].strip() or premise
        ws = T.words(clause) or T.words(premise)
        truncated = len(ws) > 9
        ws = ws[:9]
        while ws and ws[-1].lower() in T.STOPWORDS:
            ws.pop()
            truncated = True
        if not ws:
            return "Untitled " + T.type_label(entry_type)
        title = " ".join(ws)
        title = title[0].upper() + title[1:]
        return title + ("…" if truncated else "")

    def _interpretation(self, entry_type, type_conf, concepts, kw, text) -> str:
        if not text:
            return (
                f"Stored as a possible {T.type_label(entry_type)}. There is no text to interpret yet, "
                "so no concepts were extracted."
            )
        lbl = T.type_label(entry_type)
        article = "an" if lbl[:1] in "aeiou" else "a"
        parts = [f"Read as {article} {lbl} (type confidence {type_conf:.0%})."]
        if concepts:
            parts.append("Key concepts: " + ", ".join(concepts) + ".")
        if kw:
            parts.append("Most prominent words: " + ", ".join(kw[:6]) + ".")
        return " ".join(parts)

    def _inference(self, text: str, concepts: list[str], themes: list[str]) -> str:
        if not text:
            return "No inferences made: there is no text or transcript to work from."
        low = text.lower()
        lines: list[str] = []
        if themes:
            lines.append(
                "Potential themes: " + ", ".join(themes) + ". (Inferred from word patterns — not stated by you.)"
            )
        if "sacrifice" in concepts and re.search(r"\b(every|each) time\b|\bwhenever\b|\bcosts?\b|\bprice\b", low):
            lines.append("The concept may hinge on a recurring cost or trade-off attached to an ability or action.")
        if {"heroes", "villains"} & set(concepts) or re.search(r"\b(he|she|they)\b", low):
            lines.append("A central character appears to be implied.")
        if "?" in text:
            lines.append("The entry contains open questions that may still be undecided.")
        return "\n".join(lines) if lines else "No strong inferences could be made from this entry."

    # -- relationships ---------------------------------------------------------
    def find_relationships(
        self, entry: EntryContext, existing_entries: list[EntryContext]
    ) -> list[RelationshipSuggestion]:
        threshold = get_settings().relationship_threshold
        text = entry.analysis_text
        a_concepts = set(entry.concepts if entry.concepts is not None else T.concepts_for(text))
        a_kw = set(T.keywords(text))
        if not a_concepts and not a_kw:
            return []
        out: list[RelationshipSuggestion] = []
        for other in existing_entries:
            if other.id is None or other.id == entry.id:
                continue
            o_text = other.analysis_text
            b_concepts = set(other.concepts if other.concepts is not None else T.concepts_for(o_text))
            b_kw = set(T.keywords(o_text))
            shared_c = a_concepts & b_concepts
            shared_k = a_kw & b_kw
            if not shared_c and len(shared_k) < 2:
                continue
            score = 0.6 * T.overlap(a_concepts, b_concepts) + 0.4 * T.overlap(a_kw, b_kw)
            if score < threshold:
                continue
            reasons = []
            if shared_c:
                reasons.append("shared concepts: " + ", ".join(sorted(shared_c)))
            if shared_k:
                reasons.append("shared words: " + ", ".join(sorted(shared_k)[:8]))
            out.append(
                RelationshipSuggestion(
                    target_id=other.id,
                    relationship_type="similar_to" if score >= 0.7 else "related_to",
                    confidence=round(min(score, 0.95), 2),
                    rationale="; ".join(reasons).capitalize(),
                )
            )
        out.sort(key=lambda s: -s.confidence)
        return out[:10]

    # -- questions -------------------------------------------------------------
    def answer_question(self, question: str, context: list[EntryContext]) -> str:
        if not context:
            return "I found nothing in your archive related to that question."
        q = set(T.keywords(question))
        lines = [f"This offline provider can't reason about questions, but these {len(context)} entries look relevant:"]
        for c in context[:10]:
            shared = q & set(T.keywords(c.analysis_text))
            why = f" (mentions: {', '.join(sorted(shared))})" if shared else ""
            lines.append(f"- #{c.id} {c.title or 'Untitled'}{why}")
        return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Reserved providers (interfaces only in v1)
# --------------------------------------------------------------------------- #
class _NotImplementedProvider(AIProvider):
    external = True
    env_key: str | None = None

    def _fail(self):
        raise AIProviderError(
            f"The '{self.name}' AI provider is reserved for a future version and is not implemented yet. "
            "Set NEXUS_AI_PROVIDER=local to use the built-in offline organizer."
        )

    def analyze_entry(self, entry):
        self._fail()

    def summarize_entry(self, entry):
        self._fail()

    def find_relationships(self, entry, existing_entries):
        self._fail()

    def answer_question(self, question, context):
        self._fail()


class OpenAIProvider(_NotImplementedProvider):
    name = "openai"
    env_key = "OPENAI_API_KEY"


class AnthropicProvider(_NotImplementedProvider):
    name = "anthropic"
    env_key = "ANTHROPIC_API_KEY"


class LocalModelProvider(_NotImplementedProvider):
    """Future: a model running on this machine (Ollama, llama.cpp, ...)."""

    name = "local_model"
    external = False


PROVIDERS: dict[str, type[AIProvider]] = {
    "local": LocalHeuristicProvider,
    "heuristic": LocalHeuristicProvider,
    "openai": OpenAIProvider,
    "anthropic": AnthropicProvider,
    "local_model": LocalModelProvider,
}


def get_ai_provider(name: str | None = None) -> AIProvider:
    key = (name or get_settings().ai_provider or "local").lower()
    cls = PROVIDERS.get(key)
    if cls is None:
        raise AIProviderError(f"Unknown AI provider '{key}'. Options: {', '.join(sorted(PROVIDERS))}.")
    return cls()
