from app.services.search import parse_query


def cap(client, text, **kw):
    r = client.post("/api/entries", json={"original_text": text, **kw})
    assert r.status_code == 201
    return r.json()


def test_parse_natural_queries():
    q = parse_query("unfinished comic ideas")
    assert q.entry_types == ["comic"] and "raw" in q.statuses and q.terms == []
    q = parse_query("ideas about alternate realities")
    assert q.terms == ["alternate", "realities"] and not q.has_filters
    q = parse_query("sketches with monsters")
    assert q.entry_types == ["sketch"] and q.terms == ["monsters"]


def test_full_text_search_with_stemming(client):
    a = cap(client, "A villain who collects memories in glass jars.")
    cap(client, "A recipe idea for lemon cake.")
    res = client.post("/api/search", json={"query": "memory"}).json()
    assert res["mode"] == "fts5"
    assert [r["entry"]["id"] for r in res["results"]] == [a["id"]]
    assert "\x01" in res["results"][0]["snippet"]  # highlight markers


def test_villains_and_alternate_realities(client):
    v = cap(client, "The villain is a tyrant queen.")
    d = cap(client, "A parallel universe where cats rule.")
    assert [r["entry"]["id"] for r in client.post("/api/search", json={"query": "villains"}).json()["results"]] == [v["id"]]
    ids = [r["entry"]["id"] for r in client.post("/api/search", json={"query": "ideas about alternate realities"}).json()["results"]]
    assert ids == [d["id"]]  # matched through the AI concept layer


def test_unfinished_comic_ideas(client):
    c1 = cap(client, "A comic about a detective crow.")
    c2 = cap(client, "A comic about a haunted bakery.")
    cap(client, "A movie about a lighthouse.")
    client.put(f"/api/entries/{c2['id']}", json={"status": "completed"})
    ids = [r["entry"]["id"] for r in client.post("/api/search", json={"query": "unfinished comic ideas"}).json()["results"]]
    assert ids == [c1["id"]]


def test_filters_relax_when_nothing_matches(client):
    m = cap(client, "A monster made of subway maps.")
    res = client.post("/api/search", json={"query": "sketches with monsters"}).json()
    assert res["relaxed"] is True
    assert [r["entry"]["id"] for r in res["results"]] == [m["id"]]


def test_search_handles_fts_syntax_characters(client):
    cap(client, "Quotes and stars.")
    for q in ['"', "star*", "a OR", "NEAR(", "-x"]:
        assert client.post("/api/search", json={"query": q}).status_code == 200


def test_uploaded_file_names_are_searchable(client):
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (20, 20), "red").save(buf, "PNG")
    e = client.post("/api/uploads", files={"file": ("el_faro-pose2.png", buf.getvalue(), "image/png")},
                    data={"analyze": "false"}).json()
    client.post("/api/uploads", files={"file": ("IMG_2041.png", buf.getvalue(), "image/png")}, data={"analyze": "false"})
    hits = client.post("/api/search", json={"query": "faro"}).json()["results"]
    assert [h["entry"]["id"] for h in hits] == [e["id"]]
    assert client.post("/api/search", json={"query": "img"}).json()["results"] == []


def test_index_is_rebuilt_once_after_an_update(client):
    from sqlalchemy import text

    from app.database import SessionLocal
    from app.services import search

    e = client.post("/api/entries", json={"original_text": "lantern keeper", "analyze": False}).json()
    with SessionLocal() as db:
        db.execute(text(f"DELETE FROM {search.FTS_TABLE}"))
        db.execute(text("UPDATE nexus_meta SET value = 'old' WHERE key = 'search_index_version'"))
        db.commit()
        assert search.rebuild_if_outdated(db) == 1
        assert search.rebuild_if_outdated(db) == 0
    assert [h["entry"]["id"] for h in client.post("/api/search", json={"query": "lantern"}).json()["results"]] == [e["id"]]


def test_did_you_mean_when_a_word_is_misspelled(client):
    client.post("/api/entries", json={"original_text": "A villain who steals memories", "analyze": False})
    r = client.post("/api/search", json={"query": "villian memorise"}).json()
    assert r["results"] == [] and r["suggestion"] == "villain memories"
    assert client.post("/api/search", json={"query": "villain"}).json()["suggestion"] is None  # found: no suggestion
    assert client.post("/api/search", json={"query": "zzzzqqq"}).json()["suggestion"] is None  # nothing close
    # The cache notices new material.
    client.post("/api/entries", json={"original_text": "The lighthouse keeper", "analyze": False})
    assert client.post("/api/search", json={"query": "lighthuose"}).json()["suggestion"] == "lighthouse"
