"""An in-process stand-in for the Ollama HTTP API (no model needed)."""
from __future__ import annotations

import json

import httpx

from app.services.ollama import OllamaClient, OllamaProvider

DEFAULT_ANALYSIS = {
    "title": "The Memory Blade",
    "entry_type": "comic",
    "type_confidence": 0.9,
    "summary": "A comic concept about a man who cuts through dimensions with a sword but loses a memory each time.",
    "interpretation": "A fantasy/sci-fi comic premise built on a power with a personal cost.",
    "inferences": ["The story may explore how identity survives memory loss."],
    "concepts": ["Dimensional travel", "memory loss", "sacrifice"],
    "themes": ["identity", "loss", "power and consequence"],
    "tags": ["Comic", "memory", "dimensions", "sword"],
    "uncertainties": ["The protagonist's name and world are not specified."],
    "confidence": 0.8,
}


# Words in the same group "mean" the same thing to the fake embedding model:
# each vector says which topics a text touches.
MEANING_GROUPS = [
    {"memory", "memories", "remember", "forget", "forgetting", "forgot", "amnesia", "recall"},
    {"dimension", "dimensions", "portal", "universe", "reality", "realities", "multiverse", "worlds"},
    {"monster", "monsters", "creature", "beast", "tentacle", "tentacled", "leviathan"},
    {"sword", "blade", "weapon", "katana"},
    {"cake", "lemon", "recipe", "bake", "baking", "sunday"},
    {"identity", "self", "yourself"},
    {"dragon", "dragons", "wyvern"},
]


def fake_embedding(text: str) -> list[float]:
    import re

    for prefix in ("search_query: ", "search_document: "):
        text = text.replace(prefix, "")
    words = re.findall(r"[a-z]+", text.lower())
    vec = [1.0 if any(w in g for w in words) else 0.0 for g in MEANING_GROUPS] + [0.05]
    return vec


class FakeOllama:
    def __init__(self, models=("test-model:latest",), capabilities=("completion",), analysis=None,
                 relationships=None, reachable=True, chat_status=200, raw_reply=None):
        self.models = list(models)
        self.capabilities = list(capabilities) if capabilities is not None else None
        self.analysis = analysis or dict(DEFAULT_ANALYSIS)
        self.relationships = relationships or []
        self.reachable = reachable
        self.chat_status = chat_status
        self.raw_reply = raw_reply
        self.requests: list[dict] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        if not self.reachable:
            raise httpx.ConnectError("connection refused", request=request)
        path = request.url.path
        if path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": m} for m in self.models]})
        body = json.loads(request.content or b"{}")
        self.requests.append({"path": path, **body})
        if path == "/api/embed":
            name = body.get("model", "")
            if not any(m.split(":")[0] == name.split(":")[0] for m in self.models):
                return httpx.Response(404, json={"error": f"model '{name}' not found"})
            return httpx.Response(200, json={"embeddings": [fake_embedding(t) for t in body["input"]]})
        if path == "/api/show":
            name = body.get("model", "")
            if not any(m.split(":")[0] == name.split(":")[0] for m in self.models):
                return httpx.Response(404, json={"error": f"model '{name}' not found"})
            data = {"details": {}}
            if self.capabilities is not None:
                data["capabilities"] = self.capabilities
            return httpx.Response(200, json=data)
        if path == "/api/chat":
            name = body.get("model", "")
            if not any(m.split(":")[0] == name.split(":")[0] for m in self.models):
                return httpx.Response(404, json={"error": f"model '{name}' not found, try pulling it first"})
            if self.chat_status != 200:
                return httpx.Response(self.chat_status, json={"error": "boom"})
            system = body["messages"][0]["content"]
            if self.raw_reply is not None:
                content = self.raw_reply
            elif "link entries" in system:
                content = json.dumps({"relationships": self.relationships})
            elif "Summarize" in system:
                content = json.dumps({"summary": "Short summary."})
            elif body.get("format") is None:
                content = "Plain answer citing #1."
            else:
                content = json.dumps(self.analysis)
            return httpx.Response(200, json={"message": {"role": "assistant", "content": content}, "done": True})
        return httpx.Response(404, json={"error": "unknown path"})

    def client(self) -> OllamaClient:
        return OllamaClient("http://ollama.test", timeout=5, transport=httpx.MockTransport(self.handler))

    def provider(self, model="test-model", vision_model=None) -> OllamaProvider:
        return OllamaProvider(model=model, vision_model=vision_model, client=self.client())

    def embedder(self, model="nomic-embed-text"):
        from app.services.embeddings import OllamaEmbeddingProvider

        return OllamaEmbeddingProvider(model=model, base_url="http://ollama.test",
                                       transport=httpx.MockTransport(self.handler))

    @property
    def embeds(self) -> list[dict]:
        return [r for r in self.requests if r["path"] == "/api/embed"]

    @property
    def chats(self) -> list[dict]:
        return [r for r in self.requests if r["path"] == "/api/chat"]
