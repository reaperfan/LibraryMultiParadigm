"""SQLAlchemy engine, session-gyár és a Session életciklusát kezelő függőség."""

import logging
from collections.abc import Iterator

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from backend.config import get_settings
from backend.errors import AppError

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    """Közös deklaratív alaposztály az ORM-modellekhez."""


def normalize_url(database_url: str) -> str:
    """A szolgáltatói ``postgres://`` / ``postgresql://`` címet a psycopg3-illesztőre irányítja."""
    for prefix in ("postgres://", "postgresql://"):
        if database_url.startswith(prefix):
            return "postgresql+psycopg://" + database_url[len(prefix) :]
    return database_url


def build_engine(database_url: str) -> Engine:
    """Engine létrehozása; SQLite esetén a több szálas használatot engedélyezzük."""
    database_url = normalize_url(database_url)
    connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    return create_engine(database_url, connect_args=connect_args, pool_pre_ping=True)


engine = build_engine(get_settings().database_url)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def reset_sequences(db: Session) -> None:
    """Explicit azonosítóval beszúrt sorok után a PostgreSQL-szekvenciákat a max(id)-ra állítja.

    SQLite-nál nincs teendő (a következő id automatikusan max+1). Ezt használja a
    kezdőadat-betöltés és a karbantartó visszaállítása is, hogy az új rekordok ne
    okozzanak azonosítóütközést.
    """
    if db.get_bind().dialect.name != "postgresql":
        return
    for table in Base.metadata.sorted_tables:
        if "id" not in table.columns:
            continue
        db.execute(
            text(
                f"SELECT setval(pg_get_serial_sequence('{table.name}', 'id'), "
                f"COALESCE((SELECT MAX(id) FROM {table.name}), 0) + 1, false)"
            )
        )


def get_db() -> Iterator[Session]:
    """FastAPI-függőség: kérésenként egy Session, sikeres kérés végén commit, hibánál rollback."""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except AppError:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        logger.exception("Adatbázis-művelet sikertelen, tranzakció visszagörgetve")
        raise
    finally:
        db.close()
