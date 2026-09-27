"""Today → Rediscover: ideas from this day in earlier months, and a few untouched unfinished ones."""
from datetime import datetime


def _aged(client, text, created, updated=None, status="raw"):
    from app.database import SessionLocal
    from app.models import CreativeEntry

    e = client.post("/api/entries", json={"original_text": text, "title": text, "analyze": False}).json()
    with SessionLocal() as db:
        row = db.get(CreativeEntry, e["id"])
        row.created_at, row.updated_at, row.status = created, updated or created, status
        db.commit()
    return e["id"]


def test_on_this_day_and_resurfaced(client):
    last_month = _aged(client, "Lighthouse luchador", datetime(2026, 8, 27, 15))
    last_year = _aged(client, "Tide spirits", datetime(2025, 9, 27, 10))
    _aged(client, "Other day", datetime(2026, 8, 20, 10), status="completed")
    this_month = _aged(client, "Earlier today", datetime(2026, 9, 27, 1))
    done = _aged(client, "Finished comic", datetime(2026, 7, 1), status="completed")
    old = [_aged(client, f"Old idea {i}", datetime(2026, 6, i + 1)) for i in range(5)]

    r = client.get("/api/insights/rediscover", params={"today": "2026-09-27"}).json()
    assert [e["id"] for e in r["on_this_day"]] == [last_month, last_year]
    ids = [e["id"] for e in r["resurfaced"]]
    assert len(ids) == 3 and set(ids) <= set(old)  # unfinished, untouched for a month, not already shown
    assert this_month not in ids and done not in ids
    # The same few all day; tomorrow may bring others.
    assert ids == [e["id"] for e in client.get("/api/insights/rediscover", params={"today": "2026-09-27"}).json()["resurfaced"]]


def test_time_zone_decides_the_day(client):
    # 02:00 UTC on 28 Aug is still 27 Aug in New York (UTC-4: getTimezoneOffset() = 240).
    eid = _aged(client, "Late night idea", datetime(2026, 8, 28, 2))
    ny = client.get("/api/insights/rediscover", params={"today": "2026-09-27", "tz_offset": 240}).json()
    utc = client.get("/api/insights/rediscover", params={"today": "2026-09-27"}).json()
    assert [e["id"] for e in ny["on_this_day"]] == [eid] and utc["on_this_day"] == []
