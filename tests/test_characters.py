"""Characters: each character with what's linked to it and what mentions it."""


def cap(client, text, **kw):
    return client.post("/api/entries", json={"original_text": text, "analyze": False, **kw}).json()


def test_characters_list_links_and_mentions(client):
    faro = cap(client, "A retired luchador who guards the lighthouse", title="El Faro", entry_type="character")
    sketch = cap(client, "Pencil pose study", title="Pose study")
    scene = cap(client, "el faro climbs the stairs at dawn", title="Dawn scene")
    unrelated = cap(client, "Elfaro is not the name, nor is Faros", title="Noise")
    tagged = cap(client, "Grandmother of the tide", title="Abuela Marea", tags=["character"])
    client.post(f"/api/entries/{sketch['id']}/relationships",
                json={"target_entry_id": faro["id"], "relationship_type": "character_in"})

    chars = client.get("/api/characters").json()
    assert [c["character"]["id"] for c in chars] == [faro["id"], tagged["id"]]  # most connected first
    c = chars[0]
    assert [(x["entry"]["id"], x["relationship_type"], x["direction"], x["origin"]) for x in c["links"]] == \
        [(sketch["id"], "character_in", "incoming", "user")]
    assert [m["id"] for m in c["mentions"]] == [scene["id"]]  # whole words only, any case
    assert unrelated["id"] not in [m["id"] for m in c["mentions"]]
    assert c["link_count"] == 1 and c["mention_count"] == 1 and c["name_searched"] == "El Faro"


def test_trashed_and_rejected_are_left_out(client):
    hero = cap(client, "The hero", title="Nova", entry_type="character")
    a, b = cap(client, "Nova fights", title="Fight"), cap(client, "Idea", title="Idea")
    from app.database import SessionLocal
    from app.models import Relationship
    with SessionLocal() as db:
        db.add(Relationship(source_entry_id=hero["id"], target_entry_id=b["id"], relationship_type="related_to",
                            origin="ai", status="rejected"))
        db.commit()
    client.delete(f"/api/entries/{a['id']}")
    c = client.get("/api/characters").json()[0]
    assert c["links"] == [] and c["mentions"] == []


def test_untitled_or_sentence_titles_are_not_searched(client):
    cap(client, "someone", title="A very long description of a character", entry_type="character")
    assert client.get("/api/characters").json()[0]["name_searched"] is None


def test_places_and_worlds(client):
    town = cap(client, "A fishing town built on stilts", title="Puerto Niebla", entry_type="worldbuilding")
    cap(client, "a hero", title="Nova", entry_type="character")
    tagged = cap(client, "The ring under the pier", title="Pier ring", tags=["place"])
    scene = cap(client, "Rain over Puerto Niebla", title="Storm")
    worlds = client.get("/api/characters", params={"kind": "world"}).json()
    assert {w["character"]["id"] for w in worlds} == {town["id"], tagged["id"]}
    assert [m["id"] for m in worlds[0]["mentions"]] == [scene["id"]]
    assert [c["character"]["title"] for c in client.get("/api/characters").json()] == ["Nova"]
