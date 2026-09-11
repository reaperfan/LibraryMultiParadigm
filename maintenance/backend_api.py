"""Aszinkron kliens a saját backend állapot- és admin-végpontjaihoz."""

from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)


class BackendClient:
    def __init__(
        self, base_url: str, admin_token: str, timeout: float = 30.0, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"X-Admin-Token": admin_token},
            timeout=timeout,
            transport=transport,
        )

    async def __aenter__(self) -> BackendClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._client.aclose()

    async def health(self) -> dict | None:
        """None, ha a backend nem érhető el (ez nem csereok, csak naplózandó)."""
        try:
            response = await self._client.get("/health")
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as exc:
            logger.warning("A backend nem érhető el: %s", exc)
            return None

    async def set_maintenance(self, enabled: bool) -> bool:
        response = await self._client.post("/admin/maintenance", json={"enabled": enabled})
        response.raise_for_status()
        return bool(response.json()["enabled"])

    async def list_loans(self, reference_date: str) -> list[dict]:
        response = await self._client.get("/loans", params={"status": "all", "reference_date": reference_date})
        response.raise_for_status()
        return response.json()

    async def list_books(self) -> list[dict]:
        response = await self._client.get("/books")
        response.raise_for_status()
        return response.json()

    async def write_probe(self, run_id: str) -> dict:
        response = await self._client.post("/admin/write-probe", json={"run_id": run_id})
        response.raise_for_status()
        return response.json()

    async def try_app_write(self) -> int:
        """Szándékosan tiltott alkalmazási írás; karbantartás alatt 503 a helyes válasz."""
        response = await self._client.post("/loans", json={"book_id": 1, "member_id": 1})
        return response.status_code
