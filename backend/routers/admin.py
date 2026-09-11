"""Infrastruktúra-karbantartási végpontok (admin tokennel védve; nem alkalmazási végpontok)."""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend import schemas
from backend.database import get_db
from backend.maintenance_flag import maintenance_flag
from backend.models import MaintenanceProbe
from backend.routers.deps import require_admin

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])


@router.get("/maintenance", response_model=schemas.MaintenanceState)
def get_maintenance() -> schemas.MaintenanceState:
    return schemas.MaintenanceState(enabled=maintenance_flag.enabled)


@router.post("/maintenance", response_model=schemas.MaintenanceState)
def set_maintenance(payload: schemas.MaintenanceState) -> schemas.MaintenanceState:
    maintenance_flag.set(payload.enabled)
    return schemas.MaintenanceState(enabled=maintenance_flag.enabled)


@router.post("/write-probe", response_model=schemas.WriteProbeOut)
def write_probe(payload: schemas.WriteProbeRequest, db: Annotated[Session, Depends(get_db)]) -> schemas.WriteProbeOut:
    """Ellenőrzött próbaírás: karbantartás alatt is engedélyezett, csak a probe-táblát érinti."""
    probe = MaintenanceProbe(run_id=payload.run_id, created_at=datetime.now(UTC).replace(tzinfo=None))
    db.add(probe)
    db.flush()
    return schemas.WriteProbeOut(probe_id=probe.id, run_id=probe.run_id)
