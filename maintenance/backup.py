"""Újrafelhasználható mentés és visszaállítás (JSON export–import SQLAlchemy-vel).

A mentés a forrás aktuális állapotát őrzi meg: minden ORM-tábla összes sorát,
az azonosítókkal együtt, típushelyes (ISO-dátum) ábrázolásban, valamint egy
manifesztet (készítés ideje, forrás-azonosító, sorszámok, ellenőrzőösszeg).

A visszaállítás üres céladatbázisban létrehozza a sémát, függőségi sorrendben
beszúrja a sorokat az eredeti azonosítókkal, majd PostgreSQL-en a szekvenciákat
a max(id)-ra állítja, hogy az új rekord ne okozzon azonosítóütközést.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import Table, inspect, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from backend import models  # noqa: F401  – a táblák regisztrálása
from backend.database import Base, build_engine, reset_sequences

logger = logging.getLogger(__name__)

FORMAT_VERSION = 1


def _serialize(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def _deserialize(table: Table, row: dict[str, Any]) -> dict[str, Any]:
    """ISO-szövegek visszaalakítása az oszloptípus alapján (dátum, időbélyeg)."""
    out: dict[str, Any] = {}
    for column in table.columns:
        value = row.get(column.name)
        if value is None:
            out[column.name] = None
            continue
        python_type = column.type.python_type
        if python_type is date:
            value = date.fromisoformat(value)
        elif python_type is datetime:
            value = datetime.fromisoformat(value)
        out[column.name] = value
    return out


def compute_checksum(data: dict[str, list[dict]]) -> str:
    """Determinisztikus SHA-256 a táblaadatokról (kulcsok rendezve)."""
    payload = json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def export_database(database_url: str) -> dict[str, list[dict]]:
    """Minden tábla sorai ``{táblanév: [sor, ...]}`` alakban, azonosító szerint rendezve."""
    engine = build_engine(database_url)
    data: dict[str, list[dict]] = {}
    with engine.connect() as conn:
        for table in Base.metadata.sorted_tables:
            stmt = select(table).order_by(*[c for c in table.primary_key.columns])
            data[table.name] = [{k: _serialize(v) for k, v in row._mapping.items()} for row in conn.execute(stmt)]
    engine.dispose()
    return data


def create_backup(source_url: str, source_id: str, backup_dir: Path, run_id: str) -> Path:
    """Mentés fájlba; a fájlnév a készítés idejét és a forrás azonosítóját tartalmazza."""
    backup_dir.mkdir(parents=True, exist_ok=True)
    data = export_database(source_url)
    created_at = datetime.now(UTC)
    manifest = {
        "format_version": FORMAT_VERSION,
        "created_at": created_at.isoformat(),
        "source_id": source_id,
        "run_id": run_id,
        "tables": list(data.keys()),
        "row_counts": {name: len(rows) for name, rows in data.items()},
        "checksum": compute_checksum(data),
    }
    path = backup_dir / f"backup_{created_at.strftime('%Y%m%dT%H%M%SZ')}_{source_id}.json"
    path.write_text(json.dumps({"manifest": manifest, "data": data}, ensure_ascii=False, indent=1), encoding="utf-8")
    logger.info("Mentés kész: %s (%s)", path, manifest["row_counts"])
    return path


def load_backup(path: Path) -> dict:
    """Mentés beolvasása; sérült JSON esetén ValueError."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"A mentés nem olvasható JSON: {path}") from exc
    if not isinstance(payload, dict) or "manifest" not in payload or "data" not in payload:
        raise ValueError(f"A mentés szerkezete hibás: {path}")
    return payload


def _assert_empty(session: Session) -> None:
    for table in Base.metadata.sorted_tables:
        count = session.execute(select(table).limit(1)).first()
        if count is not None:
            raise RuntimeError(f"A céladatbázis nem üres (tábla: {table.name}); visszaállítás megtagadva")


def restore_backup(payload: dict, target_url: str, *, allow_reset: bool = False) -> dict[str, int]:
    """Visszaállítás üres céladatbázisba; ``allow_reset`` esetén előbb eldobja a sémát.

    Visszaadja a táblánként beszúrt sorok számát. Egy tranzakcióban fut: hiba
    esetén semmi sem marad a célban.
    """
    engine = build_engine(target_url)
    data: dict[str, list[dict]] = payload["data"]
    if allow_reset:
        Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    inserted: dict[str, int] = {}
    Session_ = sessionmaker(bind=engine)
    with Session_() as session:
        try:
            _assert_empty(session)
            for table in Base.metadata.sorted_tables:
                rows = [_deserialize(table, r) for r in data.get(table.name, [])]
                if rows:
                    session.execute(table.insert(), rows)
                inserted[table.name] = len(rows)
            reset_sequences(session)
            session.commit()
        except Exception:
            session.rollback()
            raise
    engine.dispose()
    logger.info("Visszaállítás kész: %s -> %s", inserted, _redact(target_url))
    return inserted


def _redact(url: str) -> str:
    """Kapcsolati cím naplózáshoz, jelszó nélkül."""
    if "@" in url and "://" in url:
        scheme, rest = url.split("://", 1)
        creds, host = rest.rsplit("@", 1)
        user = creds.split(":", 1)[0]
        return f"{scheme}://{user}:***@{host}"
    return url


def wait_for_database(database_url: str, timeout: float, interval: float = 5.0) -> None:
    """Időkorlátos várakozás, amíg az adatbázis fogad kapcsolatot (``SELECT 1``).

    Új felhős példány a szolgáltatónál már ``available``, de a Postgres néha csak
    másodpercekkel később fogad SSL-kapcsolatot; ezt hidalja át.
    """
    engine = build_engine(database_url)
    deadline = time.monotonic() + timeout
    attempt = 0
    try:
        while True:
            attempt += 1
            try:
                with engine.connect() as conn:
                    conn.execute(text("SELECT 1"))
                logger.info("Adatbázis elérhető: %s (%d. próbálkozás)", _redact(database_url), attempt)
                return
            except OperationalError as exc:
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"Az adatbázis nem fogad kapcsolatot {timeout:.0f} mp után: {exc}") from exc
                logger.warning("Adatbázis még nem elérhető (%d. próbálkozás), várakozás %.0f mp", attempt, interval)
                time.sleep(interval)
    finally:
        engine.dispose()


def table_names(database_url: str) -> list[str]:
    engine = build_engine(database_url)
    try:
        return inspect(engine).get_table_names()
    finally:
        engine.dispose()
