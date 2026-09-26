"""Database engine, session factory and initialisation.

SQLite is used for version 1. Everything goes through SQLAlchemy so the
database URL can later point at PostgreSQL without touching the models.
"""
from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import MetaData, create_engine, event, inspect, text
from sqlalchemy.engine import Connection
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings

settings = get_settings()

_is_sqlite = settings.database_url.startswith("sqlite")
engine = create_engine(
    settings.database_url,
    # timeout: how long a write waits for another one to finish before giving up.
    connect_args={"check_same_thread": False, "timeout": 30} if _is_sqlite else {},
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


def add_missing_columns(conn: Connection, metadata: MetaData) -> list[str]:
    """Minimal forward migration: add new nullable columns to existing tables.

    Enough for additive changes between NEXUS versions. Anything more complex
    (renames, type changes) should move to Alembic.
    """
    added = []
    insp = inspect(conn)
    for table in metadata.sorted_tables:
        if not insp.has_table(table.name):
            continue
        existing = {c["name"] for c in insp.get_columns(table.name)}
        for col in table.columns:
            if col.name in existing:
                continue
            if not col.nullable:
                raise RuntimeError(f"Cannot auto-add NOT NULL column {table.name}.{col.name}")
            col_type = col.type.compile(dialect=conn.dialect)
            conn.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {col_type}'))
            added.append(f"{table.name}.{col.name}")
    return added


def ensure_unique_indexes(conn: Connection, metadata: MetaData) -> None:
    """Unique constraints on columns added by `add_missing_columns` (SQLite can't add them inline)."""
    for table in metadata.sorted_tables:
        for col in table.columns:
            if col.unique:
                conn.execute(text(
                    f'CREATE UNIQUE INDEX IF NOT EXISTS "uq_{table.name}_{col.name}" ON "{table.name}" ("{col.name}")'))


def init_db() -> None:
    """Create folders, tables and the full-text index (idempotent)."""
    from app import models  # noqa: F401  (register models on Base.metadata)
    from app.services.search import ensure_search_index

    settings.ensure_dirs()
    Base.metadata.create_all(bind=engine)
    with engine.begin() as conn:
        add_missing_columns(conn, Base.metadata)
        ensure_unique_indexes(conn, Base.metadata)
        ensure_search_index(conn)
