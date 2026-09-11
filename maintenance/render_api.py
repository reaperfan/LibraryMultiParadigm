"""Aszinkron Render API-kliens (``httpx.AsyncClient``) időkorláttal és korlátozott újrapróbálkozással.

Csak a cseréhez szükséges műveleteket fedi le:
PostgreSQL-példány lekérdezése/létrehozása/törlése, kapcsolati adatok, a backend
környezeti változóinak frissítése és új telepítés indítása/figyelése.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any

import httpx

from maintenance.decisions import ErrorClass, backoff_seconds, classify_http_error

logger = logging.getLogger(__name__)

DEFAULT_IP_ALLOW_LIST = [{"cidrBlock": "0.0.0.0/0", "description": "maintenance controller (external access)"}]


class RenderAPIError(Exception):
    def __init__(self, message: str, status_code: int | None, error_class: ErrorClass) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error_class = error_class


class RenderClient:
    """Vékony, újrafelhasználható kliens; ``async with`` blokkban használandó."""

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.render.com/v1",
        timeout: float = 30.0,
        max_retries: int = 3,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
            timeout=timeout,
            transport=transport,
        )
        self.max_retries = max_retries

    async def __aenter__(self) -> RenderClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._client.aclose()

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        """Kérés korlátozott újrapróbálkozással; átmeneti hibánál (429/5xx/hálózat) vár és újra próbál."""
        attempt = 0
        while True:
            attempt += 1
            try:
                response = await self._client.request(method, path, **kwargs)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                if attempt > self.max_retries:
                    raise RenderAPIError(f"Hálózati hiba: {exc}", None, ErrorClass.RETRYABLE) from exc
                wait = backoff_seconds(attempt)
                logger.warning(
                    "Hálózati hiba (%s), újrapróbálkozás %.0f mp múlva (%d/%d)", exc, wait, attempt, self.max_retries
                )
                await asyncio.sleep(wait)
                continue
            if response.is_success:
                return response.json() if response.content else None
            error_class = classify_http_error(response.status_code)
            message = f"Render API {method} {path} -> {response.status_code}: {response.text[:300]}"
            if error_class is ErrorClass.RETRYABLE and attempt <= self.max_retries:
                wait = backoff_seconds(attempt)
                logger.warning("%s; újrapróbálkozás %.0f mp múlva (%d/%d)", message, wait, attempt, self.max_retries)
                await asyncio.sleep(wait)
                continue
            # 404 várt válasz is lehet (törlés befejezésének ellenőrzése), ezért csak info szint
            logger.log(logging.INFO if error_class is ErrorClass.NOT_FOUND else logging.ERROR, message)
            raise RenderAPIError(message, response.status_code, error_class)

    # ----- owner -----
    async def list_owners(self) -> list[dict]:
        return [item["owner"] for item in await self._request("GET", "/owners")]

    # ----- postgres -----
    async def list_postgres(self, owner_id: str) -> list[dict]:
        data = await self._request("GET", "/postgres", params={"ownerId": owner_id, "limit": 50})
        return [item["postgres"] for item in data]

    async def get_postgres(self, postgres_id: str) -> dict | None:
        """None, ha a példány már nem létezik."""
        try:
            return await self._request("GET", f"/postgres/{postgres_id}")
        except RenderAPIError as exc:
            if exc.error_class is ErrorClass.NOT_FOUND:
                return None
            raise

    async def create_postgres(
        self,
        *,
        name: str,
        owner_id: str,
        plan: str,
        region: str,
        version: str,
        db_name: str,
        db_user: str,
        ip_allow_list: list[dict] | None = None,
    ) -> dict:
        body = {
            "name": name,
            "ownerId": owner_id,
            "plan": plan,
            "region": region,
            "version": version,
            "databaseName": db_name,
            "databaseUser": db_user,
            "enableHighAvailability": False,
            # API-n létrehozott példánynál alapból üres a lista = nincs külső hozzáférés;
            # a vezérlő (helyi gép) külső címen kapcsolódik, ezért engedélyezni kell.
            "ipAllowList": ip_allow_list if ip_allow_list is not None else DEFAULT_IP_ALLOW_LIST,
        }
        return await self._request("POST", "/postgres", json=body)

    async def ensure_ip_allow_list(self, postgres_id: str, ip_allow_list: list[dict] | None = None) -> bool:
        """Ha a példány IP-engedélylistája üres, beállítja; visszaadja, történt-e módosítás."""
        info = await self.get_postgres(postgres_id) or {}
        if info.get("ipAllowList"):
            return False
        wanted = ip_allow_list if ip_allow_list is not None else DEFAULT_IP_ALLOW_LIST
        await self._request("PATCH", f"/postgres/{postgres_id}", json={"ipAllowList": wanted})
        logger.info("IP-engedélylista beállítva a(z) %s példányon: %s", postgres_id, wanted)
        return True

    async def delete_postgres(self, postgres_id: str) -> None:
        await self._request("DELETE", f"/postgres/{postgres_id}")

    async def get_connection_info(self, postgres_id: str) -> dict:
        return await self._request("GET", f"/postgres/{postgres_id}/connection-info")

    # ----- service -----
    async def get_service(self, service_id: str) -> dict:
        return await self._request("GET", f"/services/{service_id}")

    async def get_env_vars(self, service_id: str) -> dict[str, str]:
        data = await self._request("GET", f"/services/{service_id}/env-vars", params={"limit": 100})
        return {item["envVar"]["key"]: item["envVar"].get("value", "") for item in data}

    async def set_env_var(self, service_id: str, key: str, value: str) -> None:
        """Egyetlen változó frissítése; a többi beállítás érintetlen marad."""
        await self._request("PUT", f"/services/{service_id}/env-vars/{key}", json={"value": value})

    async def trigger_deploy(self, service_id: str) -> dict:
        return await self._request("POST", f"/services/{service_id}/deploys", json={"clearCache": "do_not_clear"})

    async def get_deploy(self, service_id: str, deploy_id: str) -> dict:
        return await self._request("GET", f"/services/{service_id}/deploys/{deploy_id}")

    # ----- várakozás -----
    async def wait_for_postgres_status(
        self, postgres_id: str, wanted: str, poll_interval: float, timeout: float
    ) -> dict:
        """Időkorlátos állapotlekérdezés, amíg a példány el nem éri a kívánt állapotot."""
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            info = await self.get_postgres(postgres_id)
            status = info.get("status") if info else None
            logger.info("Példány %s állapota: %s", postgres_id, status)
            if status == wanted:
                return info  # type: ignore[return-value]
            if asyncio.get_running_loop().time() >= deadline:
                raise TimeoutError(
                    f"A(z) {postgres_id} példány nem érte el a(z) '{wanted}' állapotot {timeout:.0f} mp alatt"
                )
            await asyncio.sleep(poll_interval)

    async def wait_for_postgres_deleted(self, postgres_id: str, poll_interval: float, timeout: float) -> None:
        deadline = asyncio.get_running_loop().time() + timeout
        while await self.get_postgres(postgres_id) is not None:
            if asyncio.get_running_loop().time() >= deadline:
                raise TimeoutError(f"A(z) {postgres_id} példány törlése nem fejeződött be {timeout:.0f} mp alatt")
            await asyncio.sleep(poll_interval)

    async def wait_for_deploy(self, service_id: str, deploy_id: str, poll_interval: float, timeout: float) -> dict:
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            deploy = await self.get_deploy(service_id, deploy_id)
            status = deploy.get("status")
            logger.info("Telepítés %s állapota: %s", deploy_id, status)
            if status == "live":
                return deploy
            if status in {"build_failed", "update_failed", "canceled", "deactivated"}:
                raise RuntimeError(f"A telepítés sikertelen: {status}")
            if asyncio.get_running_loop().time() >= deadline:
                raise TimeoutError(f"A telepítés nem lett 'live' {timeout:.0f} mp alatt")
            await asyncio.sleep(poll_interval)


def parse_render_datetime(value: str | None) -> datetime | None:
    """Render ISO-időbélyeg (``...Z``) → időzónás datetime."""
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
