"""HTTP-kliens a saját FastAPI-backendhez időkorláttal és egységes hibakezeléssel."""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class ApiError(Exception):
    """A backend hibaválasza vagy hálózati hiba, felhasználónak szánt üzenettel."""

    def __init__(self, message: str, reasons: list[str] | None = None, status_code: int | None = None) -> None:
        super().__init__(message)
        self.reasons = reasons or []
        self.status_code = status_code


class ApiClient:
    """Vékony kliens: minden hívás ugyanazon az alap-URL-en és időkorláton megy át."""

    def __init__(self, base_url: str, timeout: float = 10.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        url = f"{self.base_url}{path}"
        try:
            response = httpx.request(method, url, timeout=self.timeout, **kwargs)
        except httpx.TimeoutException as exc:
            logger.error("Időtúllépés: %s %s", method, url)
            raise ApiError("A backend nem válaszolt időben (időtúllépés)") from exc
        except httpx.HTTPError as exc:
            logger.error("Hálózati hiba: %s %s – %s", method, url, exc)
            raise ApiError(f"Nem sikerült elérni a backendet: {exc}") from exc
        if response.is_success:
            return response.json() if response.content else None
        try:
            body = response.json()
        except ValueError:
            body = {"detail": response.text}
        detail = body.get("detail", "Ismeretlen hiba")
        if isinstance(detail, list):  # FastAPI validációs hiba
            detail = "; ".join(f"{'.'.join(map(str, e.get('loc', [])))}: {e.get('msg')}" for e in detail)
        logger.warning("Backend hiba %s: %s", response.status_code, detail)
        raise ApiError(str(detail), reasons=body.get("reasons"), status_code=response.status_code)

    # ----- olvasás -----
    def health(self) -> dict:
        return self._request("GET", "/health")

    def books(self, q: str | None = None, author: str | None = None) -> list[dict]:
        params = {k: v for k, v in {"q": q, "author": author}.items() if v}
        return self._request("GET", "/books", params=params)

    def members(self) -> list[dict]:
        return self._request("GET", "/members")

    def loans(self, status: str = "all") -> list[dict]:
        return self._request("GET", "/loans", params={"status": status})

    def stats(self) -> dict:
        return self._request("GET", "/stats")

    # ----- írás -----
    def check_loan(self, book_id: int, member_id: int) -> dict:
        return self._request("POST", "/loans/check", json={"book_id": book_id, "member_id": member_id})

    def create_loan(self, book_id: int, member_id: int) -> dict:
        return self._request("POST", "/loans", json={"book_id": book_id, "member_id": member_id})

    def return_loan(self, loan_id: int) -> dict:
        return self._request("POST", f"/loans/{loan_id}/return", json={})

    def create_book(self, payload: dict) -> dict:
        return self._request("POST", "/books", json=payload)

    def create_member(self, payload: dict) -> dict:
        return self._request("POST", "/members", json=payload)
