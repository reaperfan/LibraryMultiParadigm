"""A visszaállítás ellenőrzése: tartalom, kapcsolatok, szabályeredmény, azonosítóképzés.

A rekordszám egyezése önmagában nem elég; az ellenőrzés
1. a mentés és a cél teljes tartalmát hasonlítja össze (ellenőrzőösszeggel),
2. a kölcsönzések idegen kulcsait (könyv, tag) ellenőrzi,
3. a témaspecifikus szabályt (késedelmi díjak) azonos referencia-nappal és
   beállításokkal futtatja a mentett és a visszaállított adatokon,
4. próbabeszúrással igazolja, hogy az új rekord nem ütközik meglévő azonosítóval
   (a próbasort ugyanabban a tranzakcióban visszagörgeti).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from backend.config import Settings
from backend.database import build_engine
from backend.models import Book, Loan, MaintenanceProbe, Member
from backend.services import rules
from maintenance.backup import compute_checksum, export_database

logger = logging.getLogger(__name__)


@dataclass
class VerificationReport:
    ok: bool = True
    checks: dict[str, bool] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)
    rule_result: dict[int, int] = field(default_factory=dict)

    def record(self, name: str, passed: bool, problem: str | None = None) -> None:
        self.checks[name] = passed
        if not passed:
            self.ok = False
            if problem:
                self.problems.append(problem)


def rule_result_from_rows(loan_rows: list[dict], reference_date: date, settings: Settings) -> dict[int, int]:
    """A szabály eredménye nyers (mentésbeli) sorokból; tiszta számítás."""
    views = [
        rules.LoanView(
            loan_id=int(r["id"]),
            due_date=date.fromisoformat(r["due_date"]) if isinstance(r["due_date"], str) else r["due_date"],
            returned_at=(
                date.fromisoformat(r["returned_at"]) if isinstance(r["returned_at"], str) else r["returned_at"]
            ),
        )
        for r in loan_rows
    ]
    return rules.overdue_summary(views, reference_date, settings.daily_late_fee, settings.max_late_fee)


def verify_restore(payload: dict, target_url: str, reference_date: date, settings: Settings) -> VerificationReport:
    report = VerificationReport()
    manifest, data = payload["manifest"], payload["data"]

    # 1. tartalom
    target_data = export_database(target_url)
    app_tables = [t for t in manifest["tables"] if t != "maintenance_probes"]
    for table in app_tables:
        expected, actual = len(data.get(table, [])), len(target_data.get(table, []))
        report.record(
            f"rowcount:{table}", expected == actual, f"{table}: {expected} mentett vs {actual} visszaállított sor"
        )
    source_app = {t: data.get(t, []) for t in app_tables}
    target_app = {t: target_data.get(t, []) for t in app_tables}
    report.record(
        "content_checksum",
        compute_checksum(source_app) == compute_checksum(target_app),
        "A visszaállított tartalom eltér a mentéstől",
    )

    # 2. kapcsolatok és 4. azonosítóképzés a célon
    engine = build_engine(target_url)
    Session_ = sessionmaker(bind=engine)
    with Session_() as session:
        book_ids = set(session.scalars(select(Book.id)))
        member_ids = set(session.scalars(select(Member.id)))
        loans = list(session.scalars(select(Loan)))
        broken = [ln.id for ln in loans if ln.book_id not in book_ids or ln.member_id not in member_ids]
        report.record("foreign_keys", not broken, f"Hibás kapcsolatú kölcsönzések: {broken}")

        # 3. szabály a célon
        target_views = [rules.LoanView(ln.id, ln.due_date, ln.returned_at) for ln in loans]
        target_rule = rules.overdue_summary(
            target_views, reference_date, settings.daily_late_fee, settings.max_late_fee
        )
        source_rule = rule_result_from_rows(data.get("loans", []), reference_date, settings)
        report.rule_result = target_rule
        report.record(
            "rule_result",
            source_rule == target_rule,
            f"A szabály eredménye eltér: mentés={source_rule} cél={target_rule}",
        )

        # 4. próbabeszúrás (visszagörgetve)
        try:
            probe = MaintenanceProbe(run_id="verify", created_at=datetime.now(UTC).replace(tzinfo=None))
            session.add(probe)
            session.flush()
            max_existing = max((int(r["id"]) for r in data.get("maintenance_probes", [])), default=0)
            report.record(
                "id_generation",
                probe.id is not None and probe.id > max_existing,
                f"Az új azonosító ({probe.id}) nem nagyobb a meglévő maximumnál ({max_existing})",
            )
        except Exception as exc:  # pl. azonosítóütközés
            report.record("id_generation", False, f"Próbabeszúrás sikertelen: {exc}")
        finally:
            session.rollback()
    engine.dispose()

    logger.info("Ellenőrzés %s: %s", "SIKERES" if report.ok else "SIKERTELEN", report.checks)
    for problem in report.problems:
        logger.error("Ellenőrzési hiba: %s", problem)
    return report
