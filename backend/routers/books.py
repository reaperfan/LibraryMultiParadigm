"""Könyvkatalógus-végpontok."""

from fastapi import APIRouter, HTTPException, Query, status

from backend import repository, schemas
from backend.models import Book
from backend.routers.deps import ServiceDep
from backend.services.loan_service import LoanService

router = APIRouter(prefix="/books", tags=["books"])


def _to_out(service: LoanService, book: Book) -> schemas.BookOut:
    return schemas.BookOut(
        id=book.id,
        title=book.title,
        author=book.author,
        year=book.year,
        isbn=book.isbn,
        copies_total=book.copies_total,
        available_copies=service.available_copies(book),
    )


@router.get("", response_model=list[schemas.BookOut])
def list_books(
    service: ServiceDep,
    q: str | None = Query(default=None, description="Címrészlet"),
    author: str | None = Query(default=None, description="Szerzőrészlet"),
) -> list[schemas.BookOut]:
    return [_to_out(service, b) for b in repository.list_books(service.db, q, author)]


@router.get("/{book_id}", response_model=schemas.BookOut)
def get_book(book_id: int, service: ServiceDep) -> schemas.BookOut:
    book = repository.get_book(service.db, book_id)
    if book is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Nincs ilyen könyv: {book_id}")
    return _to_out(service, book)


@router.post("", response_model=schemas.BookOut, status_code=status.HTTP_201_CREATED)
def create_book(payload: schemas.BookCreate, service: ServiceDep) -> schemas.BookOut:
    book = service.add_book(payload.model_dump())
    return _to_out(service, book)
