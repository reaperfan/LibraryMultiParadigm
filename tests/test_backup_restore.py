"""Mentés–visszaállítás körkörös próba és a visszaállítás-ellenőrzés (helyi SQLite-tal)."""

from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from backend.config import Settings
from backend.database import build_engine
from backend.models import Loan
from maintenance.backup import create_backup, load_backup, restore_backup
from maintenance.verify import verify_restore

REFERENCE = date(2026, 9, 11)


def test_backup_restore_roundtrip_and_verification(db_session, tmp_path: Path) -> None:
    # a mintaadatokon felül egy új, később felvett rekord is a mentés része legyen
    db_session.add(Loan(book_id=6, member_id=3, loaned_at=date(2026, 9, 1), due_date=date(2026, 9, 15)))
    db_session.commit()
    source_url = str(db_session.get_bind().url)

    path = create_backup(source_url, "dpg-source", tmp_path / "backups", "run-test")
    payload = load_backup(path)
    assert payload["manifest"]["source_id"] == "dpg-source"
    assert payload["manifest"]["row_counts"] == {"books": 8, "members": 4, "loans": 7, "maintenance_probes": 0}

    target_url = f"sqlite:///{(tmp_path / 'target.db').as_posix()}"
    inserted = restore_backup(payload, target_url)
    assert inserted["loans"] == 7

    report = verify_restore(payload, target_url, REFERENCE, Settings())
    assert report.ok, report.problems
    # A szabály elvárt eredménye a mintaadatokból: #2: 8 nap×50, #3: 18×50, #4: 3×50, #6: 46×50→plafon
    assert report.rule_result == {2: 400, 3: 900, 4: 150, 6: 2000}

    # azonosítóképzés: új rekord a visszaállított célban nem ütközik
    engine = build_engine(target_url)
    with sessionmaker(bind=engine)() as session:
        loan = Loan(book_id=1, member_id=1, loaned_at=REFERENCE, due_date=date(2026, 9, 25))
        session.add(loan)
        session.commit()
        assert loan.id == 8
        assert session.scalar(select(Loan).where(Loan.id == 7)).book_id == 6
    engine.dispose()


def test_restore_refuses_non_empty_target(db_session, tmp_path: Path) -> None:
    source_url = str(db_session.get_bind().url)
    payload = load_backup(create_backup(source_url, "dpg-source", tmp_path, "run-test"))
    target_url = f"sqlite:///{(tmp_path / 'target.db').as_posix()}"
    restore_backup(payload, target_url)
    with pytest.raises(RuntimeError, match="nem üres"):
        restore_backup(payload, target_url)  # újrafuttatás nem duplikál
    assert restore_backup(payload, target_url, allow_reset=True)["loans"] == 6


def test_verify_detects_tampered_target(db_session, tmp_path: Path) -> None:
    source_url = str(db_session.get_bind().url)
    payload = load_backup(create_backup(source_url, "dpg-source", tmp_path, "run-test"))
    target_url = f"sqlite:///{(tmp_path / 'target.db').as_posix()}"
    restore_backup(payload, target_url)
    engine = build_engine(target_url)
    with sessionmaker(bind=engine)() as session:
        loan = session.get(Loan, 2)
        loan.due_date = date(2026, 9, 10)  # a díj így 50 lenne 400 helyett
        session.commit()
    engine.dispose()
    report = verify_restore(payload, target_url, REFERENCE, Settings())
    assert not report.ok
    assert report.checks["content_checksum"] is False and report.checks["rule_result"] is False
