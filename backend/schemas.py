"""Pydantic-sémák a kérés- és választörzsekhez."""

from datetime import date

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class BookCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    author: str = Field(min_length=1, max_length=120)
    year: int = Field(ge=1450, le=2100)
    isbn: str = Field(min_length=10, max_length=20)
    copies_total: int = Field(ge=1, le=100)


class BookOut(BookCreate):
    model_config = ConfigDict(from_attributes=True)

    id: int
    available_copies: int


class MemberCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: EmailStr
    joined_at: date | None = None


class MemberOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    email: str
    joined_at: date


class LoanCreate(BaseModel):
    book_id: int
    member_id: int
    loaned_at: date | None = Field(default=None, description="Alapértelmezés: a mai nap")


class LoanDecisionOut(BaseModel):
    allowed: bool
    reasons: list[str]


class LoanOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    book_id: int
    member_id: int
    book_title: str
    member_name: str
    loaned_at: date
    due_date: date
    returned_at: date | None
    days_overdue: int
    late_fee: int


class ReturnRequest(BaseModel):
    returned_at: date | None = Field(default=None, description="Alapértelmezés: a mai nap")


class StatsOut(BaseModel):
    reference_date: date
    books: int
    members: int
    loans_total: int
    loans_active: int
    loans_overdue: int
    total_late_fee: int
    loans_per_author: dict[str, int]


class MaintenanceState(BaseModel):
    enabled: bool


class WriteProbeRequest(BaseModel):
    run_id: str = Field(min_length=1, max_length=64)


class WriteProbeOut(BaseModel):
    probe_id: int
    run_id: str
