"""The connections map."""


def cap(client, text, **kw):
    return client.post("/api/entries", json={"original_text": text, "analyze": False, **kw}).json()


def test_graph_shows_your_links_and_optionally_suggestions(client):
    p = client.post("/api/projects", json={"name": "Hollow Sun"}).json()
    a = cap(client, "Hero", project_ids=[p["id"]])
    b = cap(client, "Villain", project_ids=[p["id"]])
    c = cap(client, "Cake")
    client.post(f"/api/entries/{a['id']}/relationships", json={"target_entry_id": b["id"], "relationship_type": "contradicts"})
    from app.database import SessionLocal
    from app.models import Relationship
    with SessionLocal() as db:  # an AI suggestion and a rejected one
        db.add(Relationship(source_entry_id=a["id"], target_entry_id=c["id"], relationship_type="similar_to", origin="ai", status="suggested"))
        db.add(Relationship(source_entry_id=b["id"], target_entry_id=c["id"], relationship_type="related_to", origin="ai", status="rejected"))
        db.commit()

    g = client.get("/api/graph").json()
    assert {n["id"] for n in g["nodes"]} == {a["id"], b["id"], c["id"]}
    assert [(e["source"], e["target"], e["type"], e["yours"]) for e in g["edges"]] == [(a["id"], b["id"], "contradicts", True)]
    g = client.get("/api/graph", params={"include_suggested": True}).json()
    assert sorted(e["type"] for e in g["edges"]) == ["contradicts", "similar_to"]  # never the rejected one
    g = client.get("/api/graph", params={"project_id": p["id"], "include_suggested": True}).json()
    assert {n["id"] for n in g["nodes"]} == {a["id"], b["id"]} and len(g["edges"]) == 1


def test_trashed_entries_leave_the_map(client):
    a, b = cap(client, "One"), cap(client, "Two")
    client.post(f"/api/entries/{a['id']}/relationships", json={"target_entry_id": b["id"]})
    client.delete(f"/api/entries/{b['id']}")
    g = client.get("/api/graph").json()
    assert [n["id"] for n in g["nodes"]] == [a["id"]] and g["edges"] == []
