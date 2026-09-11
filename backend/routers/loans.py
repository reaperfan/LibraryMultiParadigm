"""Kölcsönzési végpontok – itt fut a témaspecifikus szabály."""

from typing import Literal

from fastapi import APIRouter, Query, status

from backend import schemas
from backend.models import Loan
from backend.routers.deps import ServiceDep
from backend.services.loan_service import LoanService

router = APIRouter(prefix="/loans", tags=["loans"])


def _to_out(service: LoanService, loan: Loan) -> schemas.LoanOut:
    return schemas.LoanOut(
        id=loan.id,
        book_id=loan.book_id,
        member_id=loan.member_id,
        book_title=loan.book.title,
        member_name=loan.member.name,
        loaned_at=loan.loaned_at,
        due_date=loan.due_date,
        returned_at=loan.returned_at,
        days_overdue=service.days_overdue_for(loan),
        late_fee=service.fee_for(loan),
    )


@router.get("", response_model=list[schemas.LoanOut])
def list_loans(
    service: ServiceDep,
    status_filter: Literal["all", "active", "overdue", "returned"] = Query(default="all", alias="status"),
) -> list[schemas.LoanOut]:
    return [_to_out(service, ln) for ln in service.list_loans(status_filter)]


@router.post("/check", response_model=schemas.LoanDecisionOut)
def check_loan(payload: schemas.LoanCreate, service: ServiceDep) -> schemas.LoanDecisionOut:
    """A szabály előnézete: engedélyezhető-e a kérés, és ha nem, miért."""
    decision = service.evaluate(payload.book_id, payload.member_id)
    return schemas.LoanDecisionOut(allowed=decision.allowed, reasons=list(decision.reasons))


@router.post("", response_model=schemas.LoanOut, status_code=status.HTTP_201_CREATED)
def create_loan(payload: schemas.LoanCreate, service: ServiceDep) -> schemas.LoanOut:
    loan = service.create_loan(payload.book_id, payload.member_id, payload.loaned_at)
    return _to_out(service, loan)


@router.post("/{loan_id}/return", response_model=schemas.LoanOut)
def return_loan(loan_id: int, payload: schemas.ReturnRequest, service: ServiceDep) -> schemas.LoanOut:
    loan = service.return_loan(loan_id, payload.returned_at)
    return _to_out(service, loan)
