"""FastAPI-alkalmazás: routerek, hibakezelők, karbantartási middleware, indítás."""

import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from backend import models  # noqa: F401  – a modellek regisztrálása a metadatában
from backend.config import get_settings
from backend.database import Base, SessionLocal, engine
from backend.errors import ConflictError, NotFoundError, RuleViolationError
from backend.maintenance_flag import maintenance_flag
from backend.routers import admin, books, loans, members, stats
from backend.seed import seed_if_empty

settings = get_settings()
logging.basicConfig(
    level=settings.log_level.upper(),
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("backend")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Séma létrehozása és kezdőadatok betöltése induláskor."""
    maintenance_flag.set(settings.maintenance_mode)
    Base.metadata.create_all(engine)
    if settings.seed_on_startup:
        with SessionLocal() as db:
            seed_if_empty(db)
    logger.info("Backend elindult (maintenance=%s)", maintenance_flag.enabled)
    yield
    logger.info("Backend leáll")


app = FastAPI(title="Könyvtári kölcsönzés API", version="1.0.0", lifespan=lifespan)
for router in (books.router, members.router, loans.router, stats.router, admin.router):
    app.include_router(router)

WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


@app.middleware("http")
async def block_writes_in_maintenance(
    request: Request, call_next: Callable[[Request], Awaitable[JSONResponse]]
) -> JSONResponse:
    """Karbantartás alatt az alkalmazási írások 503-at kapnak; az admin útvonal kivétel."""
    if maintenance_flag.enabled and request.method in WRITE_METHODS and not request.url.path.startswith("/admin"):
        logger.warning("Írás elutasítva karbantartás alatt: %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"detail": "Karbantartás folyamatban, az adatmódosítás átmenetileg nem elérhető"},
            headers={"Retry-After": "300"},
        )
    return await call_next(request)


@app.exception_handler(NotFoundError)
async def not_found_handler(_: Request, exc: NotFoundError) -> JSONResponse:
    return JSONResponse(status_code=status.HTTP_404_NOT_FOUND, content={"detail": str(exc)})


@app.exception_handler(ConflictError)
async def conflict_handler(_: Request, exc: ConflictError) -> JSONResponse:
    return JSONResponse(status_code=status.HTTP_409_CONFLICT, content={"detail": str(exc)})


@app.exception_handler(RuleViolationError)
async def rule_handler(_: Request, exc: RuleViolationError) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        content={"detail": "A kölcsönzési szabály nem engedi a műveletet", "reasons": list(exc.reasons)},
    )


@app.get("/health", tags=["infra"])
def health() -> dict[str, str | bool]:
    return {"status": "ok", "maintenance": maintenance_flag.enabled}
