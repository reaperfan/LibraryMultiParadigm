"""Statisztikai összesítés a felület diagramjaihoz."""

from fastapi import APIRouter

from backend import schemas
from backend.routers.deps import ServiceDep

router = APIRouter(prefix="/stats", tags=["stats"])


@router.get("", response_model=schemas.StatsOut)
def get_stats(service: ServiceDep) -> schemas.StatsOut:
    return schemas.StatsOut(**service.stats())
