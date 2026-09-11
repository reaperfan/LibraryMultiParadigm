"""A teljes cserefolyamat próbája helyettesített Render API-val és helyi adatbázisokkal.

A Render-hívásokat egy állapotot tartó ``httpx.MockTransport`` szolgálja ki, a
backendet a valódi FastAPI-alkalmazás (ASGI-transport, tesztadatbázis). Felhős
erőforrás nem jön létre. A tényleges felhős cserepróba külön, engedélyezett
karbantartási futás (README, docs/maintenance_run.md).
"""

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from maintenance.backend_api import BackendClient
from maintenance.config import MaintenanceSettings
from maintenance.render_api import RenderClient
from maintenance.rotation import RotationController, RotationError
from maintenance.state import StateStore


class FakeRender:
    """Minimális Render API-szimuláció: postgres példányok, env-varok, deployok."""

    def __init__(self, source_url: str, new_url: str, fail_create_once: bool = False) -> None:
        self.instances = {
            "dpg-old": {
                "id": "dpg-old",
                "name": "old",
                "status": "available",
                "plan": "free",
                "region": "frankfurt",
                "version": "16",
                "ownerId": "tea-1",
            }
        }
        self.urls = {"dpg-old": source_url}
        self.new_url = new_url
        self.env = {"DATABASE_URL": source_url, "MAINTENANCE_MODE": "false"}
        self.deploys: dict[str, str] = {}
        self.fail_create_once = fail_create_once
        self.created = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        if path == "/v1/owners":
            return httpx.Response(200, json=[{"owner": {"id": "tea-1", "name": "ws"}}])
        if path == "/v1/postgres" and method == "GET":
            return httpx.Response(200, json=[{"postgres": i} for i in self.instances.values()])
        if path == "/v1/postgres" and method == "POST":
            self.created += 1
            if self.fail_create_once:
                self.fail_create_once = False
                # a példány létrejön, de a válasz elveszik (időtúllépés)
                body = json.loads(request.content)
                self.instances["dpg-new"] = {
                    "id": "dpg-new",
                    "name": body["name"],
                    "status": "available",
                    "plan": "free",
                }
                self.urls["dpg-new"] = self.new_url
                raise httpx.ReadTimeout("timeout", request=request)
            body = json.loads(request.content)
            self.instances["dpg-new"] = {
                "id": "dpg-new",
                "name": body["name"],
                "status": "available",
                "plan": body["plan"],
            }
            self.urls["dpg-new"] = self.new_url
            return httpx.Response(201, json=self.instances["dpg-new"])
        if path.startswith("/v1/postgres/"):
            pid = path.split("/")[3]
            if pid not in self.instances:
                return httpx.Response(404, json={"message": "not found"})
            if path.endswith("/connection-info"):
                return httpx.Response(
                    200, json={"externalConnectionString": self.urls[pid], "internalConnectionString": self.urls[pid]}
                )
            if method == "DELETE":
                del self.instances[pid]
                return httpx.Response(204)
            return httpx.Response(200, json=self.instances[pid])
        if path == "/v1/services/srv-1":
            return httpx.Response(200, json={"id": "srv-1", "name": "backend"})
        if path == "/v1/services/srv-1/env-vars" and method == "GET":
            return httpx.Response(200, json=[{"envVar": {"key": k, "value": v}} for k, v in self.env.items()])
        if path.startswith("/v1/services/srv-1/env-vars/") and method == "PUT":
            key = path.rsplit("/", 1)[1]
            self.env[key] = json.loads(request.content)["value"]
            return httpx.Response(200, json={"key": key, "value": self.env[key]})
        if path == "/v1/services/srv-1/deploys" and method == "POST":
            self.deploys["dep-1"] = "live"
            return httpx.Response(201, json={"id": "dep-1", "status": "created"})
        if path.startswith("/v1/services/srv-1/deploys/"):
            return httpx.Response(200, json={"id": "dep-1", "status": self.deploys["dep-1"]})
        return httpx.Response(500, json={"message": f"unhandled {method} {path}"})


def make_settings(tmp_path: Path, verify_url: str) -> MaintenanceSettings:
    return MaintenanceSettings(
        render_api_key="k",
        render_owner_id="tea-1",
        render_service_id="srv-1",
        render_postgres_id="dpg-old",
        backend_url="http://backend",
        admin_token="test-admin-token",
        verify_database_url=verify_url,
        backup_dir=tmp_path / "backups",
        state_dir=tmp_path / "state",
        poll_interval_seconds=0.0,
        poll_timeout_seconds=5.0,
        rule_reference_date="2026-09-11",
        max_retries=0,
        maintenance_at=datetime(2026, 9, 11, 10, 0, tzinfo=UTC),
    )


def build_controller(
    tmp_path: Path, source_url: str, fake: FakeRender, backend_app
) -> tuple[RotationController, StateStore]:
    settings = make_settings(tmp_path, f"sqlite:///{(tmp_path / 'verify.db').as_posix()}")
    store = StateStore(settings.state_dir)
    render = RenderClient(
        "k", "http://render/v1", transport=httpx.MockTransport(fake.handler), max_retries=settings.max_retries
    )
    backend = BackendClient("http://backend", "test-admin-token", transport=httpx.ASGITransport(app=backend_app))
    return RotationController(settings, render, backend, store), store


@pytest.mark.asyncio
async def test_full_rotation_succeeds(db_session, tmp_path: Path) -> None:
    from backend.main import app

    source_url = str(db_session.get_bind().url)
    new_url = f"sqlite:///{(tmp_path / 'new.db').as_posix()}"
    fake = FakeRender(source_url, new_url)
    controller, store = build_controller(tmp_path, source_url, fake, app)

    state = await controller.start(
        source_id="dpg-old",
        approved_source_id="dpg-old",
        allow_delete_source_id="dpg-old",
        reason="scheduled_maintenance",
        maintenance_at=controller.s.maintenance_at,
    )

    assert state.status == "done" and state.new_postgres_id == "dpg-new"
    assert "dpg-old" not in fake.instances  # a régi törölve, a törlés lezárult
    assert fake.env["DATABASE_URL"] == new_url  # a backend átállítva
    assert fake.env["MAINTENANCE_MODE"] == "false"  # karbantartás feloldva a végén
    assert store.load_active()["postgres_id"] == "dpg-new"  # tartósan rögzítve
    assert store.load_run() is None and not store.lock_file.exists()
    assert Path(state.backup_path).exists()
    assert not (await controller.backend.health())["maintenance"]


@pytest.mark.asyncio
async def test_rotation_refused_without_delete_permission(db_session, tmp_path: Path) -> None:
    from backend.main import app

    source_url = str(db_session.get_bind().url)
    fake = FakeRender(source_url, f"sqlite:///{(tmp_path / 'new.db').as_posix()}")
    controller, store = build_controller(tmp_path, source_url, fake, app)
    with pytest.raises(RotationError, match="törlési engedély"):
        await controller.start(
            source_id="dpg-old",
            approved_source_id="dpg-old",
            allow_delete_source_id=None,
            reason="manual",
            maintenance_at=None,
        )
    assert "dpg-old" in fake.instances and fake.env["MAINTENANCE_MODE"] == "false"
    assert store.load_run().status == "failed"


@pytest.mark.asyncio
async def test_interrupted_create_is_resumed_without_second_instance(db_session, tmp_path: Path) -> None:
    from backend.main import app

    source_url = str(db_session.get_bind().url)
    new_url = f"sqlite:///{(tmp_path / 'new.db').as_posix()}"
    fake = FakeRender(source_url, new_url, fail_create_once=True)
    controller, store = build_controller(tmp_path, source_url, fake, app)

    with pytest.raises(Exception):
        await controller.start(
            source_id="dpg-old",
            approved_source_id="dpg-old",
            allow_delete_source_id="dpg-old",
            reason="manual",
            maintenance_at=None,
        )
    failed = store.load_run()
    assert failed.status == "failed" and "create_new" not in failed.completed_steps
    assert fake.env["MAINTENANCE_MODE"] == "true"  # a karbantartás érvényben marad

    state = await controller.resume()
    assert state.status == "done" and state.new_postgres_id == "dpg-new"
    assert fake.created == 1  # egyeztetés: nem jött létre második példány
    assert fake.env["DATABASE_URL"] == new_url
