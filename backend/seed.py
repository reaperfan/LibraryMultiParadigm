"""Kezdő adatok betöltése a ``data/`` CSV-fájlokból, csak üres adatbázisba.

Az adatok fiktív mintaadatok (a könyvcímek valósak, a tagok kitaláltak).
"""

import csv
import logging
from datetime import date
from pathlib import Path

from sqlalchemy.orm import Session

from backend import repository
from backend.database import reset_sequences
from backend.models import Book, Loan, Member

logger = logging.getLogger(__name__)
DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def _read_csv(name: str) -> list[dict[str, str]]:
    with (DATA_DIR / name).open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def _date_or_none(value: str) -> date | None:
    return date.fromisoformat(value) if value else None


def seed_if_empty(db: Session) -> bool:
    """Betölti a mintaadatokat, ha a könyvek táblája üres. Visszaadja, történt-e betöltés."""
    if repository.count_rows(db, Book) > 0:
        return False
    for row in _read_csv("books.csv"):
        db.add(
            Book(
                id=int(row["id"]),
                title=row["title"],
                author=row["author"],
                year=int(row["year"]),
                isbn=row["isbn"],
                copies_total=int(row["copies_total"]),
            )
        )
    for row in _read_csv("members.csv"):
        db.add(
            Member(
                id=int(row["id"]), name=row["name"], email=row["email"], joined_at=date.fromisoformat(row["joined_at"])
            )
        )
    for row in _read_csv("loans.csv"):
        db.add(
            Loan(
                id=int(row["id"]),
                book_id=int(row["book_id"]),
                member_id=int(row["member_id"]),
                loaned_at=date.fromisoformat(row["loaned_at"]),
                due_date=date.fromisoformat(row["due_date"]),
                returned_at=_date_or_none(row["returned_at"]),
            )
        )
    db.flush()
    reset_sequences(db)
    db.commit()
    logger.info("Mintaadatok betöltve a %s könyvtárból", DATA_DIR)
    return True
