"""Database engine, session factory and initialisation.

SQLite is used for version 1. Everything goes through SQLAlchemy so the
database URL can later point at PostgreSQL without touching the models.
"""
from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings

settings = get_settings()

_is_sqlite = settings.database_url.startswith("sqlite")
engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False} if _is_sqlite else {},
)

if _is_sqlite:

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_connection, _record):  # pragma: no cover - trivial
        cur = dbapi_connection.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA journal_mode=WAL")
        cur.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """Create folders, tables and the full-text index (idempotent)."""
    from app import models  # noqa: F401  (register models on Base.metadata)
    from app.services.search import ensure_search_index

    settings.ensure_dirs()
    Base.metadata.create_all(bind=engine)
    with engine.begin() as conn:
        ensure_search_index(conn)
