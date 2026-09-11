"""Szolgáltatásosztály: a kölcsönzési munkafolyamat vezérlése.

A ``LoanService`` köti össze a repository-réteget (adatbázis) és a tiszta
szabályfüggvényeket (``rules``). Az osztály tartja a beállításokat és a Session-t,
a metódusok pedig lépésekre bontott, követhető munkafolyamatot valósítanak meg.
"""

import logging
from datetime import date, timedelta

from sqlalchemy.orm import Session

from backend import repository
from backend.config import Settings
from backend.errors import ConflictError, NotFoundError, RuleViolationError
from backend.models import Book, Loan, Member
from backend.services import rules

logger = logging.getLogger(__name__)


class LoanService:
    """Kölcsönzések létrehozása, visszavétele, díjszámítása és statisztikája."""

    def __init__(self, db: Session, settings: Settings, reference_date: date | None = None) -> None:
        self.db = db
        self.settings = settings
        self.reference_date = reference_date or repository.today()

    # ----- katalógus -----------------------------------------------------

    def available_copies(self, book: Book) -> int:
        active = repository.active_loan_count_for_book(self.db, book.id)
        return rules.available_copies(book.copies_total, active)

    def add_book(self, data: dict) -> Book:
        if repository.get_book_by_isbn(self.db, data["isbn"]):
            raise ConflictError(f"Már létezik könyv ezzel az ISBN-nel: {data['isbn']}")
        book = repository.add_book(self.db, Book(**data))
        logger.info("Új könyv rögzítve: id=%s isbn=%s", book.id, book.isbn)
        return book

    def add_member(self, data: dict) -> Member:
        if repository.get_member_by_email(self.db, data["email"]):
            raise ConflictError(f"Már létezik tag ezzel az e-mail-címmel: {data['email']}")
        data = {**data, "joined_at": data.get("joined_at") or self.reference_date}
        member = repository.add_member(self.db, Member(**data))
        logger.info("Új tag rögzítve: id=%s", member.id)
        return member

    # ----- kölcsönzés ----------------------------------------------------

    def _build_context(self, book: Book, member: Member) -> rules.LoanRequestContext:
        """Tények összegyűjtése az adatbázisból a szabály számára."""
        active = repository.active_loans_for_member(self.db, member.id)
        overdue = [ln for ln in active if rules.days_overdue(ln.due_date, self.reference_date) > 0]
        return rules.LoanRequestContext(
            available_copies=self.available_copies(book),
            member_active_loans=len(active),
            member_overdue_loans=len(overdue),
            member_has_same_book=any(ln.book_id == book.id for ln in active),
            max_active_loans=self.settings.max_active_loans,
        )

    def evaluate(self, book_id: int, member_id: int) -> rules.LoanDecision:
        """Döntés előnézete adatmódosítás nélkül."""
        book = repository.get_book(self.db, book_id)
        member = repository.get_member(self.db, member_id)
        if book is None:
            raise NotFoundError(f"Nincs ilyen könyv: {book_id}")
        if member is None:
            raise NotFoundError(f"Nincs ilyen tag: {member_id}")
        return rules.evaluate_loan_request(self._build_context(book, member))

    def create_loan(self, book_id: int, member_id: int, loaned_at: date | None = None) -> Loan:
        """Kölcsönzés rögzítése, ha a szabály engedi; egyébként RuleViolationError."""
        decision = self.evaluate(book_id, member_id)
        if not decision.allowed:
            logger.warning("Kölcsönzés elutasítva (book=%s member=%s): %s", book_id, member_id, decision.reasons)
            raise RuleViolationError(decision.reasons)
        start = loaned_at or self.reference_date
        loan = Loan(
            book_id=book_id,
            member_id=member_id,
            loaned_at=start,
            due_date=start + timedelta(days=self.settings.loan_period_days),
        )
        repository.add_loan(self.db, loan)
        logger.info("Kölcsönzés rögzítve: id=%s book=%s member=%s due=%s", loan.id, book_id, member_id, loan.due_date)
        return loan

    def return_loan(self, loan_id: int, returned_at: date | None = None) -> Loan:
        loan = repository.get_loan(self.db, loan_id)
        if loan is None:
            raise NotFoundError(f"Nincs ilyen kölcsönzés: {loan_id}")
        if loan.returned_at is not None:
            raise ConflictError("A kölcsönzés már le van zárva")
        end = returned_at or self.reference_date
        if end < loan.loaned_at:
            raise RuleViolationError(("A visszahozás dátuma nem lehet a kölcsönzés előtt",))
        loan.returned_at = end
        logger.info("Kölcsönzés lezárva: id=%s fee=%s", loan.id, self.fee_for(loan))
        return loan

    # ----- díj és statisztika -------------------------------------------

    def _view(self, loan: Loan) -> rules.LoanView:
        return rules.LoanView(loan_id=loan.id, due_date=loan.due_date, returned_at=loan.returned_at)

    def fee_for(self, loan: Loan) -> int:
        return rules.loan_fee_at(
            self._view(loan), self.reference_date, self.settings.daily_late_fee, self.settings.max_late_fee
        )

    def days_overdue_for(self, loan: Loan) -> int:
        end = loan.returned_at or self.reference_date
        return rules.days_overdue(loan.due_date, end)

    def list_loans(self, status: str = "all") -> list[Loan]:
        """``overdue`` státusz: aktív és a referencia-napon már késedelmes."""
        if status == "overdue":
            return [ln for ln in repository.list_loans(self.db, "active") if self.days_overdue_for(ln) > 0]
        return repository.list_loans(self.db, status)

    def stats(self) -> dict:
        active = repository.list_loans(self.db, "active")
        overdue = [ln for ln in active if self.days_overdue_for(ln) > 0]
        return {
            "reference_date": self.reference_date,
            "books": repository.count_rows(self.db, Book),
            "members": repository.count_rows(self.db, Member),
            "loans_total": repository.count_rows(self.db, Loan),
            "loans_active": len(active),
            "loans_overdue": len(overdue),
            "total_late_fee": sum(self.fee_for(ln) for ln in overdue),
            "loans_per_author": repository.loans_per_author(self.db),
        }
