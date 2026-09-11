"""Tag-végpontok."""

from fastapi import APIRouter, HTTPException, status

from backend import repository, schemas
from backend.routers.deps import ServiceDep

router = APIRouter(prefix="/members", tags=["members"])


@router.get("", response_model=list[schemas.MemberOut])
def list_members(service: ServiceDep) -> list[schemas.MemberOut]:
    return [schemas.MemberOut.model_validate(m) for m in repository.list_members(service.db)]


@router.get("/{member_id}", response_model=schemas.MemberOut)
def get_member(member_id: int, service: ServiceDep) -> schemas.MemberOut:
    member = repository.get_member(service.db, member_id)
    if member is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Nincs ilyen tag: {member_id}")
    return schemas.MemberOut.model_validate(member)


@router.post("", response_model=schemas.MemberOut, status_code=status.HTTP_201_CREATED)
def create_member(payload: schemas.MemberCreate, service: ServiceDep) -> schemas.MemberOut:
    member = service.add_member(payload.model_dump())
    return schemas.MemberOut.model_validate(member)
