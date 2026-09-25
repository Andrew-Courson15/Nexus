"""Test setup: every test session uses a throwaway data directory."""
import os
import shutil
import tempfile

import pytest

_TMP = tempfile.mkdtemp(prefix="nexus-test-")
os.environ["NEXUS_DATA_DIR"] = _TMP
os.environ.pop("NEXUS_DATABASE_URL", None)
os.environ["NEXUS_ALLOWED_HOSTS"] = "testserver,localhost,127.0.0.1"
os.environ["NEXUS_AI_PROVIDER"] = "local"
os.environ["NEXUS_TRANSCRIPTION_PROVIDER"] = "placeholder"
os.environ["NEXUS_PROCESSING_MODE"] = "sync"

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.database import Base, engine  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c
    # Clean slate between tests (keep the schema).
    with engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            conn.execute(table.delete())
        conn.execute(text("DELETE FROM entry_search"))


def pytest_sessionfinish(session, exitstatus):
    engine.dispose()
    shutil.rmtree(_TMP, ignore_errors=True)
