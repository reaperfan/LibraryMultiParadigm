"""Közös tesztkörnyezet: elkülönített, minden tesztnél újra létrehozott SQLite-adatbázis.

A környezeti változókat a backend importja ELŐTT állítjuk be, így a valódi
engine és a valódi ``get_db`` függőség a tesztadatbázisra mutat; élő
szolgáltatást, telepített adatbázist vagy titkot a tesztek nem használnak.
"""

import os
import tempfile
from collections.abc import Iterator
from pathlib import Path

TEST_DIR = Path(tempfile.mkdtemp(prefix="library-tests-"))
os.environ["DATABASE_URL"] = f"sqlite:///{(TEST_DIR / 'test.db').as_posix()}"
os.environ["ADMIN_TOKEN"] = "test-admin-token"
os.environ["MAINTENANCE_MODE"] = "false"
os.environ["SEED_ON_STARTUP"] = "true"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backend import models  # noqa: E402, F401
from backend.database import Base, SessionLocal, engine  # noqa: E402
from backend.maintenance_flag import maintenance_flag  # noqa: E402
from backend.seed import seed_if_empty  # noqa: E402


@pytest.fixture
def db_session() -> Iterator:
    """Üres séma + mintaadatok; a teszt után minden eldobva."""
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with SessionLocal() as session:
        seed_if_empty(session)
        yield session
    Base.metadata.drop_all(engine)


@pytest.fixture
def client(db_session) -> Iterator[TestClient]:
    from backend.main import app

    maintenance_flag.set(False)
    with TestClient(app) as test_client:
        yield test_client
    maintenance_flag.set(False)


@pytest.fixture
def admin_headers() -> dict[str, str]:
    return {"X-Admin-Token": "test-admin-token"}
