"""Small, dependency-free text helpers shared by the local organizer and search.

These are deliberately simple. They power the offline `local` AI provider and
natural-language search parsing; a real language model provider replaces
most of this when configured.
"""
from __future__ import annotations

import re
from collections import Counter

STOPWORDS = frozenset(
    """
    a about above after again against all almost also am an and any are aren't as at be because been
    before being below between both but by can can't cannot could couldn't did didn't do does doesn't
    doing don't down during each even ever every few for from further get gets getting go goes going
    gonna got had hadn't has hasn't have haven't having he he'd he'll he's her here here's hers herself
    him himself his how how's i i'd i'll i'm i've if in into is isn't it it's its itself just kind kinda
    let's like maybe me might more most much must mustn't my myself no nor not now of off on once one
    only or other ought our ours ourselves out over own really same shan't she she'd she'll she's should
    shouldn't so some something somehow such than that that's the their theirs them themselves then there
    there's these they they'd they'll they're they've thing things think this those though through thought
    to too under until up upon us very want wanna was wasn't way we we'd we'll we're we've were weren't what
    what's when when's where where's whether which while who who's whom why why's will with won't would
    wouldn't yeah yes yet you you'd you'll you're you've your yours yourself yourselves ok okay um uh
    actually basically stuff sort lot lots new idea ideas just had have guy guys someone somebody time times
    """.split()
)

# Words that point to an entry type. Weights favour concrete media types.
TYPE_SIGNALS: dict[str, dict[str, float]] = {
    "comic": {"comic": 3, "comics": 3, "manga": 3, "webcomic": 3, "graphic novel": 3, "panel": 1.5, "panels": 1.5},
    "movie": {"movie": 3, "film": 2.5, "feature film": 3, "screenplay": 3, "cinema": 2, "blockbuster": 2},
    "short_film": {"short film": 4, "short movie": 4, "short": 0.5},
    "story": {"story": 2.5, "novel": 3, "tale": 2, "plot": 1.5, "narrative": 1.5, "book": 1.5, "chapter": 2},
    "character": {"character": 2.5, "protagonist": 2, "villain": 1.5, "antagonist": 2, "hero": 1, "heroine": 1},
    "scene": {"scene": 3, "shot": 1.5, "sequence": 1.5, "opening": 1},
    "dialogue": {"dialogue": 3, "line": 1, "says": 1, "said": 0.5, "conversation": 1.5},
    "sketch": {"sketch": 3, "doodle": 3, "drawing": 2, "drew": 2},
    "artwork": {"painting": 3, "artwork": 3, "illustration": 3, "canvas": 2, "portrait": 2, "art piece": 3},
    "worldbuilding": {
        "worldbuilding": 4, "world": 1, "kingdom": 1.5, "realm": 1.5, "lore": 2.5, "faction": 2,
        "magic system": 3, "empire": 1.5, "civilization": 1.5, "mythology": 2,
    },
    "research": {"research": 3, "study": 2, "article": 2, "paper": 1.5, "learned": 1.5, "fact": 1.5, "history": 1.5},
    "brain_dump": {"brain dump": 4, "random thoughts": 3, "stream of consciousness": 3},
    "note": {"note": 2, "reminder": 2, "remember to": 2, "todo": 2},
    "idea": {"idea": 1.5, "concept": 1, "what if": 1.5},
}

# Single words that only describe the entry type (not useful as tags).
TYPE_WORDS = frozenset(
    w for signals in TYPE_SIGNALS.values() for w in signals if " " not in w
) | {"short", "film", "concept"}

# Normalised word -> concept. Concepts are the AI's grouping layer.
CONCEPT_LEXICON: dict[str, str] = {}
for _concept, _words in {
    "alternate realities": "dimension dimensional multiverse parallel alternate reality portal universe rift",
    "memory": "memory remember forget forgotten amnesia recall nostalgia remembering forgetting",
    "time": "timeline past future clock era century time-travel",
    "weapons": "sword blade gun weapon axe spear bow dagger katana",
    "magic": "magic spell wizard witch sorcery curse enchant enchanted mage sorcerer",
    "monsters": "monster creature beast demon dragon ghost zombie vampire kaiju",
    "villains": "villain antagonist nemesis tyrant evil",
    "heroes": "hero heroine champion vigilante",
    "death": "death die dead dying grave afterlife ghost funeral",
    "family": "mother father sister brother family son daughter parent grandmother grandfather",
    "love": "love romance lover crush heartbreak",
    "technology": "robot android machine cyborg computer technology hacker",
    "space": "space planet star galaxy alien spaceship astronaut moon orbit",
    "dreams": "dream nightmare sleep sleeping",
    "war": "war battle army soldier conflict rebellion",
    "nature": "forest ocean sea river mountain tree desert island",
    "city": "city urban street neighborhood skyline subway",
    "identity": "identity self mask name persona",
    "power": "power ability superpower strength",
    "sacrifice": "cost price sacrifice lose lost losing trade",
    "travel": "travel journey voyage road",
    "music": "music song band melody guitar",
    "humor": "funny joke comedy absurd",
}.items():
    for _w in _words.split():
        CONCEPT_LEXICON[_w] = _concept

# Concept -> potential themes (always presented as AI inference).
THEME_RULES: dict[str, list[str]] = {
    "memory": ["identity", "loss"],
    "sacrifice": ["sacrifice", "power and consequence"],
    "power": ["power and consequence"],
    "death": ["mortality"],
    "family": ["belonging"],
    "love": ["connection"],
    "alternate realities": ["choice and possibility"],
    "technology": ["what it means to be human"],
    "war": ["conflict"],
    "dreams": ["the subconscious"],
    "monsters": ["fear"],
    "villains": ["good versus evil"],
    "heroes": ["heroism"],
    "identity": ["identity"],
    "time": ["change"],
    "travel": ["transformation"],
}

TYPE_LABELS = {
    "short_film": "short film",
    "brain_dump": "brain dump",
    "worldbuilding": "worldbuilding",
}

_WORD_RE = re.compile(r"[A-Za-z][A-Za-z'\-]*")
_LEAD_INS = [
    re.compile(r"^\s*((ok(ay)?|so|hey|um+|uh+|alright|right)\b[\s,.:;-]*)+", re.I),
    re.compile(
        r"^(comic|movie|film|short\s+film|story|character|scene|dialogue|sketch|artwork|worldbuilding|research|note)"
        r"(\s+(idea|concept|note))?\s*[:\-—]\s*",
        re.I,
    ),
    re.compile(
        r"^(i\s+)?(just\s+)?(had|have|got|thought\s+of|came\s+up\s+with)\s+"
        r"(an?\s+|this\s+|another\s+)?((new|cool|random|weird|crazy|quick)\s+)?"
        r"(idea|thought|concept)\b[\s,.:;-]*",
        re.I,
    ),
    re.compile(
        r"^(for|about)\s+(an?\s+|the\s+)?([\w-]+\s+){0,2}?(where|about|in\s+which|that|involving|with)\s+",
        re.I,
    ),
    re.compile(r"^(for|about)\s+", re.I),
]


def normalize(word: str) -> str:
    """Crude singularisation so 'memories' ~ 'memory', 'swords' ~ 'sword'."""
    w = word.lower().strip("'-")
    if w.endswith("'s"):
        w = w[:-2]
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 4 and w.endswith(("ches", "shes", "xes", "sses")):
        return w[:-2]
    if len(w) > 3 and w.endswith("s") and not w.endswith(("ss", "us", "is")):
        return w[:-1]
    return w


def words(text: str) -> list[str]:
    return [m.group(0) for m in _WORD_RE.finditer(text or "")]


def keywords(text: str) -> list[str]:
    """Content words (normalised, stopwords removed), in order of first appearance."""
    seen: dict[str, None] = {}
    for w in words(text):
        lw = w.lower()
        if lw in STOPWORDS or len(lw) < 3:
            continue
        n = normalize(lw)
        if n in STOPWORDS or len(n) < 3:
            continue
        seen.setdefault(n, None)
    return list(seen)


def keyword_counts(text: str) -> Counter:
    c: Counter = Counter()
    for w in words(text):
        lw = w.lower()
        if lw in STOPWORDS or len(lw) < 3:
            continue
        n = normalize(lw)
        if n not in STOPWORDS:
            c[n] += 1
    return c


def concepts_for(text: str) -> list[str]:
    found: dict[str, None] = {}
    for w in words(text):
        c = CONCEPT_LEXICON.get(normalize(w)) or CONCEPT_LEXICON.get(w.lower())
        if c:
            found.setdefault(c, None)
    return list(found)


def themes_for(concepts: list[str]) -> list[str]:
    themes: dict[str, None] = {}
    for c in concepts:
        for t in THEME_RULES.get(c, []):
            themes.setdefault(t, None)
    return list(themes)


def type_scores(text: str) -> dict[str, float]:
    low = " " + re.sub(r"[^a-z']+", " ", (text or "").lower()) + " "
    tokens = {normalize(w) for w in words(low)} | {w.lower() for w in words(low)}
    scores: dict[str, float] = {}
    for etype, signals in TYPE_SIGNALS.items():
        s = 0.0
        for sig, weight in signals.items():
            if " " in sig:
                if f" {sig} " in low or f" {sig}s " in low:
                    s += weight
            elif sig in tokens:
                s += weight
        if s:
            scores[etype] = s
    return scores


def first_sentence(text: str) -> str:
    text = re.sub(r"\s+", " ", (text or "").strip())
    m = re.search(r"(.+?[.!?])(\s|$)", text)
    return (m.group(1) if m else text).strip()


def strip_lead_in(sentence: str) -> str:
    """Remove conversational openers like 'I just had an idea for a comic where'."""
    out = sentence.strip()
    for pat in _LEAD_INS:
        new = pat.sub("", out, count=1)
        if new != out and len(words(new)) >= 3:
            out = new
    return out.strip()


def type_label(entry_type: str) -> str:
    return TYPE_LABELS.get(entry_type, entry_type.replace("_", " "))


def overlap(a: set, b: set) -> float:
    """Overlap coefficient |A∩B| / min(|A|,|B|)."""
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))
