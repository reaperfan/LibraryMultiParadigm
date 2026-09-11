"""Közös FastAPI-függőségek: szolgáltatáspéldány és admin-hitelesítés."""

from datetime import date
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Query, status
from sqlalchemy.orm import Session

from backend.config import Settings, get_settings
from backend.database import get_db
from backend.services.loan_service import LoanService


def get_loan_service(
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    reference_date: Annotated[
        date | None, Query(description="Díjszámítás referencia-napja (alapértelmezés: ma)")
    ] = None,
) -> LoanService:
    return LoanService(db, settings, reference_date)


ServiceDep = Annotated[LoanService, Depends(get_loan_service)]


def require_admin(
    x_admin_token: Annotated[str | None, Header()] = None,
    settings: Annotated[Settings, Depends(get_settings)] = None,  # type: ignore[assignment]
) -> None:
    """A karbantartási végpontok csak érvényes ``X-Admin-Token`` fejléccel hívhatók."""
    if not x_admin_token or x_admin_token != settings.admin_token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Érvénytelen vagy hiányzó admin token")
