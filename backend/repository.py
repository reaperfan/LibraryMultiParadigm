"""Adatbázis-lekérdezések és mentési műveletek (ORM). Üzleti szabály itt nincs."""

from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.models import Book, Loan, Member


def list_books(db: Session, query: str | None = None, author: str | None = None) -> list[Book]:
    stmt = select(Book).order_by(Book.title)
    if query:
        stmt = stmt.where(Book.title.ilike(f"%{query}%"))
    if author:
        stmt = stmt.where(Book.author.ilike(f"%{author}%"))
    return list(db.scalars(stmt))


def get_book(db: Session, book_id: int) -> Book | None:
    return db.get(Book, book_id)


def get_book_by_isbn(db: Session, isbn: str) -> Book | None:
    return db.scalar(select(Book).where(Book.isbn == isbn))


def add_book(db: Session, book: Book) -> Book:
    db.add(book)
    db.flush()
    return book


def list_members(db: Session) -> list[Member]:
    return list(db.scalars(select(Member).order_by(Member.name)))


def get_member(db: Session, member_id: int) -> Member | None:
    return db.get(Member, member_id)


def get_member_by_email(db: Session, email: str) -> Member | None:
    return db.scalar(select(Member).where(Member.email == email))


def add_member(db: Session, member: Member) -> Member:
    db.add(member)
    db.flush()
    return member


def active_loan_count_for_book(db: Session, book_id: int) -> int:
    stmt = select(func.count(Loan.id)).where(Loan.book_id == book_id, Loan.returned_at.is_(None))
    return db.scalar(stmt) or 0


def active_loans_for_member(db: Session, member_id: int) -> list[Loan]:
    stmt = select(Loan).where(Loan.member_id == member_id, Loan.returned_at.is_(None))
    return list(db.scalars(stmt))


def list_loans(db: Session, status: str = "all") -> list[Loan]:
    """``status``: all | active | returned. A késedelmesség szabály, nem lekérdezés."""
    stmt = select(Loan).order_by(Loan.id)
    if status == "active":
        stmt = stmt.where(Loan.returned_at.is_(None))
    elif status == "returned":
        stmt = stmt.where(Loan.returned_at.is_not(None))
    return list(db.scalars(stmt))


def get_loan(db: Session, loan_id: int) -> Loan | None:
    return db.get(Loan, loan_id)


def add_loan(db: Session, loan: Loan) -> Loan:
    db.add(loan)
    db.flush()
    return loan


def count_rows(db: Session, model: type) -> int:
    return db.scalar(select(func.count()).select_from(model)) or 0


def loans_per_author(db: Session) -> dict[str, int]:
    stmt = (
        select(Book.author, func.count(Loan.id))
        .join(Loan, Loan.book_id == Book.id)
        .group_by(Book.author)
        .order_by(func.count(Loan.id).desc())
    )
    return {author: count for author, count in db.execute(stmt)}


def today() -> date:
    """Külön függvény, hogy a tesztek felülírhassák."""
    return date.today()
