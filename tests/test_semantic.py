import pytest

from app.database import SessionLocal
from app.models import EntryEmbedding
from app.services import embeddings, processing
from app.services.embeddings import normalize, pack, unpack
from tests.fake_ollama import FakeOllama


@pytest.fixture()
def fake(monkeypatch):
    f = FakeOllama(models=["nomic-embed-text:latest", "test-model"])
    monkeypatch.setattr(embeddings, "get_embedding_provider", lambda: f.embedder())
    return f


def cap(client, text, **kw):
    r = client.post("/api/entries", json={"original_text": text, **kw})
    assert r.status_code == 201, r.text
    return r.json()


def search(client, q):
    return client.post("/api/search", json={"query": q}).json()


def test_vectors_round_trip():
    v = normalize([3.0, 4.0])
    assert v == [0.6, 0.8]
    assert list(unpack(pack(v))) == pytest.approx(v)


def test_entries_are_indexed_with_document_prefix(client, fake):
    e = cap(client, "Hero loses his memories whenever he crosses into another universe.")
    with SessionLocal() as db:
        row = db.get(EntryEmbedding, e["id"])
        assert row is not None and row.model == "nomic-embed-text" and row.dim == len(unpack(row.vector))
    assert fake.embeds[0]["input"][0].startswith("search_document: ")
    detail = client.get(f"/api/entries/{e['id']}").json()
    assert detail["embedding_model"] == "nomic-embed-text" and detail["embedding_id"]


def test_search_by_meaning_finds_entries_without_shared_words(client, fake):
    hero = cap(client, "Hero loses his memories whenever he crosses into another universe.")
    cap(client, "Lemon cake recipe for Sunday.")
    res = search(client, "forgetting everything")
    assert res["mode"].endswith("+meaning")
    ids = [r["entry"]["id"] for r in res["results"]]
    assert ids == [hero["id"]]  # the cake is not "about" this, so it is left out
    hit = res["results"][0]
    assert hit["matched_by"] == ["meaning"] and 0 < hit["similarity"] <= 1
    assert fake.embeds[-1]["input"][0].startswith("search_query: ")


def test_words_and_meaning_combine(client, fake):
    hero = cap(client, "Hero loses his memories whenever he crosses into another universe.")
    cap(client, "A monster made of subway maps.")
    res = search(client, "memories")
    top = res["results"][0]
    assert top["entry"]["id"] == hero["id"]
    assert sorted(top["matched_by"]) == ["meaning", "words"]


def test_filters_apply_to_meaning_matches(client, fake):
    comic = cap(client, "A girl with amnesia draws maps of places she can't recall.", entry_type="comic")
    cap(client, "Hero loses his memories whenever he crosses into another universe.", entry_type="story")
    ids = [r["entry"]["id"] for r in search(client, "comic about forgetting")["results"]]
    assert ids == [comic["id"]]


def test_meaning_search_falls_back_when_ollama_down(client, monkeypatch):
    down = FakeOllama(reachable=False)
    monkeypatch.setattr(embeddings, "get_embedding_provider", lambda: down.embedder())
    cap(client, "A villain who collects memories.")
    res = search(client, "memories")
    assert len(res["results"]) == 1 and res["results"][0]["matched_by"] == ["words"]
    assert "unavailable" in res["note"]


def test_missing_embedding_model_is_reported(client, monkeypatch):
    f = FakeOllama(models=["gemma3:12b"])
    monkeypatch.setattr(embeddings, "get_embedding_provider", lambda: f.embedder())
    e = cap(client, "A villain who collects memories.")
    assert "ollama pull nomic-embed-text" in e["ai_metadata"]["embedding_error"]
    info = client.get("/api/settings").json()["search"]
    assert info["semantic_enabled"] is False and "ollama pull" in info["health"]["detail"]


def test_edits_refresh_the_index(client, fake):
    e = cap(client, "A villain who collects memories.")
    with SessionLocal() as db:
        before = db.get(EntryEmbedding, e["id"]).content_hash
    client.put(f"/api/entries/{e['id']}", json={"user_notes": "She rides a dragon."})
    with SessionLocal() as db:
        assert db.get(EntryEmbedding, e["id"]).content_hash != before
    assert search(client, "wyvern rider")["results"][0]["entry"]["id"] == e["id"]
    # status-only edits that don't change the text don't re-embed
    n = len(fake.embeds)
    client.put(f"/api/entries/{e['id']}", json={"status": "exploring"})
    assert len(fake.embeds) == n


def test_reindex_backfills_old_entries(client, monkeypatch):
    for t in ("A villain who collects memories.", "A dragon made of glass.", "Lemon cake recipe."):
        cap(client, t)  # indexed with embeddings switched off
    f = FakeOllama(models=["nomic-embed-text"])
    monkeypatch.setattr(embeddings, "get_embedding_provider", lambda: f.embedder())
    before = client.get("/api/settings").json()["search"]["index"]
    assert before == {"entries": 3, "indexed": 0, "pending": 3}
    assert client.post("/api/search/reindex").json() == {"queued": 3}
    after = client.get("/api/settings").json()["search"]
    assert after["index"] == {"entries": 3, "indexed": 3, "pending": 0} and after["semantic_enabled"] is True
    assert client.post("/api/search/reindex").json() == {"queued": 0}


def test_filter_only_queries_skip_meaning(client, fake):
    cap(client, "A comic about a detective crow.", entry_type="comic")
    n = len(fake.embeds)
    res = search(client, "unfinished comic ideas")
    assert len(res["results"]) == 1 and res["results"][0]["matched_by"] == ["filters"]
    assert len(fake.embeds) == n


def test_queue_keeps_full_and_index_jobs_separate(monkeypatch):
    import dataclasses

    from app.config import get_settings

    s = get_settings()
    monkeypatch.setattr(processing, "get_settings", lambda: dataclasses.replace(s, processing_mode="background"))
    q = processing.ProcessingQueue()  # not started: jobs just wait
    q.submit(processing.Job(7, transcribe=False, analyze=False))
    q.submit(processing.Job(7))
    q.submit(processing.Job(7))  # duplicate full job is dropped
    assert q._queue.qsize() == 2


def test_meaning_relaxes_filters_when_nothing_matches_them(client, fake):
    hero = cap(client, "Hero loses his memories whenever he crosses into another universe.", entry_type="comic")
    res = search(client, "stories about amnesia")  # nothing is typed "story"
    assert [r["entry"]["id"] for r in res["results"]] == [hero["id"]]
    assert res["relaxed"] is True and res["results"][0]["matched_by"] == ["meaning"]


NEUTRAL = {"title": "Sea Idea", "entry_type": "idea", "type_confidence": 0.5, "summary": "An idea.",
           "interpretation": "An idea.", "inferences": [], "concepts": [], "themes": [], "tags": ["idea"],
           "uncertainties": [], "confidence": 0.5}


def _related_scenario(client, monkeypatch, with_meaning: bool):
    from app.services import organizer

    fake = FakeOllama(models=["test-model", "nomic-embed-text"], analysis=dict(NEUTRAL))
    if with_meaning:
        monkeypatch.setattr(embeddings, "get_embedding_provider", lambda: fake.embedder())
    monkeypatch.setattr(organizer, "get_ai_provider", lambda: fake.provider())
    old = cap(client, "A leviathan sleeps beneath the harbor.")
    fake.relationships = [{"candidate_id": old["id"], "relationship_type": "similar_to", "confidence": 0.8,
                           "rationale": "Both are giant creatures lurking under the city."}]
    new = cap(client, "The monster under the bridge.")
    return fake, old, client.get(f"/api/entries/{new['id']}/relationships").json()


def test_related_ideas_use_meaning_even_without_shared_words(client, monkeypatch):
    fake, old, rels = _related_scenario(client, monkeypatch, with_meaning=True)
    assert [(r["other"]["id"], r["relationship_type"]) for r in rels] == [(old["id"], "similar_to")]
    assert rels[0]["label"] == "AI suggested relationship"
    assert f"CANDIDATE {old['id']}" in fake.chats[-1]["messages"][1]["content"]


def test_without_meaning_index_word_prefilter_finds_nothing(client, monkeypatch):
    fake, _, rels = _related_scenario(client, monkeypatch, with_meaning=False)
    assert rels == []
    assert not any("link entries" in c["messages"][0]["content"] for c in fake.chats)


def test_bad_embedding_reply_never_loses_the_analysis(client, monkeypatch):
    import httpx

    from app.services.embeddings import OllamaEmbeddingProvider

    def broken(request):
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "nomic-embed-text"}]})
        return httpx.Response(200, content=b"<html>not json</html>")

    bad = OllamaEmbeddingProvider(base_url="http://x.test", transport=httpx.MockTransport(broken))
    monkeypatch.setattr(embeddings, "get_embedding_provider", lambda: bad)
    cap(client, "A villain who collects memories.")
    e = cap(client, "A hero who loses his memories.")
    assert e["processing_status"] == "done" and e["ai_summary"]
    assert "unreadable" in e["ai_metadata"]["embedding_error"]
    assert "unavailable" in search(client, "memories")["note"]


def test_meaning_neighbours_skip_trashed_and_self(client, fake):
    from app.services.embeddings import similar_entries

    a = cap(client, "A leviathan sleeps beneath the harbor.")
    b = cap(client, "A tentacled creature in the subway.")
    client.delete(f"/api/entries/{a['id']}")
    with SessionLocal() as db:
        assert similar_entries(db, "the monster under the bridge", allowed_ids={b["id"]}) == [b["id"]]
        assert similar_entries(db, "the monster under the bridge", allowed_ids=set()) == []
