"""ORM-adatmodellek: könyvek, tagok, kölcsönzések és a karbantartási próbaírás táblája."""

from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.database import Base


class Book(Base):
    """Egy könyvcím a katalógusban, több példánnyal."""

    __tablename__ = "books"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    author: Mapped[str] = mapped_column(String(120), nullable=False)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    isbn: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    copies_total: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    loans: Mapped[list["Loan"]] = relationship(back_populates="book")


class Member(Base):
    """Könyvtári tag."""

    __tablename__ = "members"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    email: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    joined_at: Mapped[date] = mapped_column(Date, nullable=False)

    loans: Mapped[list["Loan"]] = relationship(back_populates="member")


class Loan(Base):
    """Egy példány kölcsönzése; ``returned_at`` üres, amíg a könyv kint van."""

    __tablename__ = "loans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    book_id: Mapped[int] = mapped_column(ForeignKey("books.id"), nullable=False)
    member_id: Mapped[int] = mapped_column(ForeignKey("members.id"), nullable=False)
    loaned_at: Mapped[date] = mapped_column(Date, nullable=False)
    due_date: Mapped[date] = mapped_column(Date, nullable=False)
    returned_at: Mapped[date | None] = mapped_column(Date, nullable=True)

    book: Mapped[Book] = relationship(back_populates="loans")
    member: Mapped[Member] = relationship(back_populates="loans")


class MaintenanceProbe(Base):
    """A karbantartó ellenőrzött próbaírásainak nyoma (nem alkalmazási adat)."""

    __tablename__ = "maintenance_probes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
