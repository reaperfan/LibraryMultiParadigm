"""A példánycsere vezérlése lépésről lépésre (6.3. szakasz), folytatható futásállapottal.

A ``RotationController`` minden lépés után menti az állapotot; megszakadás után a
``resume`` ugyanazt a futást a következő lépéstől folytatja, nem hoz létre újabb
példányt találgatással, és nem duplikál adatot (a visszaállítás üres célt követel).
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, date, datetime
from pathlib import Path

from backend.config import Settings as BackendRuleSettings
from maintenance import backup as backup_ops
from maintenance import decisions
from maintenance.backend_api import BackendClient
from maintenance.config import MaintenanceSettings
from maintenance.render_api import RenderAPIError, RenderClient
from maintenance.state import RunState, StateStore
from maintenance.verify import rule_result_from_rows, verify_restore

logger = logging.getLogger(__name__)


class RotationError(Exception):
    """A csere biztonságosan leállt; a futásállapot és a mentés megmaradt."""


class RotationController:
    def __init__(
        self,
        settings: MaintenanceSettings,
        render: RenderClient,
        backend: BackendClient,
        store: StateStore,
        rule_settings: BackendRuleSettings | None = None,
    ) -> None:
        self.s = settings
        self.render = render
        self.backend = backend
        self.store = store
        self.rule_settings = rule_settings or BackendRuleSettings()
        self.reference_date = date.fromisoformat(settings.rule_reference_date)

    # ------------------------------------------------------------------ belépés
    async def start(
        self,
        *,
        source_id: str,
        approved_source_id: str,
        allow_delete_source_id: str | None,
        reason: str,
        maintenance_at: datetime | None,
    ) -> RunState:
        if self.store.load_run() is not None:
            raise RotationError("Van félbeszakadt futás; előbb a 'resume' paranccsal folytassa vagy zárja le")
        run_id = datetime.now(UTC).strftime("%Y%m%d%H%M%S") + "-" + uuid.uuid4().hex[:6]
        state = RunState(
            run_id=run_id,
            started_at=datetime.now(UTC).isoformat(),
            reason=reason,
            source_postgres_id=source_id,
            approved_source_id=approved_source_id,
            allow_delete_source_id=allow_delete_source_id,
            maintenance_at=maintenance_at.isoformat() if maintenance_at else None,
        )
        self.store.save_run(state)
        return await self.run(state)

    async def resume(self) -> RunState:
        state = self.store.load_run()
        if state is None:
            raise RotationError("Nincs folytatható futás")
        logger.info("Futás folytatása: %s, kész lépések: %s", state.run_id, state.completed_steps)
        state.status = "running"
        state.last_error = None
        return await self.run(state)

    async def run(self, state: RunState) -> RunState:
        if not self.store.acquire_lock(state.run_id):
            raise RotationError("Egyidejű csere fut; kilépés")
        try:
            while (step := decisions.next_step(state.completed_steps)) is not None:
                logger.info("=== [%s] lépés: %s", state.run_id, step)
                await getattr(self, f"step_{step}")(state)
                state.mark_step(step)
                self.store.save_run(state)
            state.status = "done"
            self.store.save_run(state)
            self.store.archive_run(state)
            logger.info("=== Csere sikeres: új aktív példány %s", state.new_postgres_id)
            return state
        except Exception as exc:
            state.status = "failed"
            state.last_error = f"{type(exc).__name__}: {exc}"
            self.store.save_run(state)
            logger.error(
                "A csere leállt a(z) '%s' lépésnél: %s. A karbantartási mód érvényben marad.",
                decisions.next_step(state.completed_steps),
                state.last_error,
            )
            raise
        finally:
            self.store.release_lock()

    # ------------------------------------------------------------------ lépések
    async def step_preflight(self, state: RunState) -> None:
        if state.source_postgres_id != state.approved_source_id:
            raise RotationError(
                f"A forrás ({state.source_postgres_id}) nem egyezik az engedélyezettel ({state.approved_source_id})"
            )
        owners = await self.render.list_owners()
        if not any(o["id"] == self.s.render_owner_id for o in owners):
            raise RotationError(f"A munkaterület ({self.s.render_owner_id}) nem érhető el ezzel az API-kulccsal")
        source = await self.render.get_postgres(state.source_postgres_id)
        if source is None:
            raise RotationError(
                "A forráspéldány nem található; normál csere nem futtatható (lásd README: helyreállítás)"
            )
        if source.get("ownerId") not in (None, self.s.render_owner_id):
            raise RotationError("A forráspéldány más munkaterülethez tartozik")
        service = await self.render.get_service(self.s.render_service_id)
        env = await self.render.get_env_vars(self.s.render_service_id)
        if "DATABASE_URL" not in env:
            raise RotationError("A backend szolgáltatásban nincs DATABASE_URL változó")
        if not self.s.verify_database_url:
            raise RotationError("MAINT_VERIFY_DATABASE_URL nincs beállítva (elkülönített próba-adatbázis szükséges)")
        backup_ops.table_names(self.s.verify_database_url)  # elérhetőség
        self.s.backup_dir.mkdir(parents=True, exist_ok=True)
        instances = await self.render.list_postgres(self.s.render_owner_id)
        free_count = sum(1 for i in instances if i.get("plan") == "free")
        if (
            self.s.render_plan == "free"
            and free_count >= 1
            and state.allow_delete_source_id != state.source_postgres_id
        ):
            raise RotationError(
                "Ingyenes csomag: egyszerre egy példány engedélyezett, és nincs törlési engedély a forrásra"
            )
        logger.info(
            "Előfeltételek rendben. Forrás=%s (%s, %s, pg%s) backend=%s új példány: plan=%s region=%s version=%s",
            source["id"],
            source.get("plan"),
            source.get("region"),
            source.get("version"),
            service.get("name"),
            self.s.render_plan,
            self.s.render_region,
            self.s.render_pg_version,
        )

    async def step_freeze_writes(self, state: RunState) -> None:
        # Tartós tiltás (újratelepítést is túlél) + azonnali futásidejű kapcsoló
        await self.render.set_env_var(self.s.render_service_id, "MAINTENANCE_MODE", "true")
        await self.backend.set_maintenance(True)
        logger.info("Írások tiltva; %.0f mp várakozás a folyamatban lévő kérésekre", self.s.poll_interval_seconds)
        await asyncio.sleep(self.s.poll_interval_seconds)
        code = await self.backend.try_app_write()
        if code != 503:
            raise RotationError(f"Az íráskorlátozás nem érvényes (próbaírás válasza: {code})")

    async def _source_url(self, state: RunState) -> str:
        if self.s.source_database_url:
            return self.s.source_database_url
        info = await self.render.get_connection_info(state.source_postgres_id)
        return info["externalConnectionString"]

    async def step_backup(self, state: RunState) -> None:
        url = await self._source_url(state)
        path = backup_ops.create_backup(url, state.source_postgres_id, self.s.backup_dir, state.run_id)
        payload = backup_ops.load_backup(path)
        problems = decisions.validate_manifest(payload["manifest"], backup_ops.compute_checksum(payload["data"]))
        if problems:
            raise RotationError(f"A mentés érvénytelen: {problems}")
        state.backup_path = str(path)
        self.store.save_run(state)

    def _payload(self, state: RunState) -> dict:
        if not state.backup_path or not Path(state.backup_path).exists():
            raise RotationError("A futáshoz tartozó mentés hiányzik; a csere nem folytatható")
        payload = backup_ops.load_backup(Path(state.backup_path))
        problems = decisions.validate_manifest(payload["manifest"], backup_ops.compute_checksum(payload["data"]))
        if problems:
            raise RotationError(f"A mentés érvénytelen: {problems}")
        if payload["manifest"]["source_id"] != state.source_postgres_id:
            raise RotationError("A mentés más forráshoz tartozik")
        return payload

    async def step_verify_local_restore(self, state: RunState) -> None:
        payload = self._payload(state)
        backup_ops.restore_backup(payload, self.s.verify_database_url, allow_reset=True)
        report = verify_restore(payload, self.s.verify_database_url, self.reference_date, self.rule_settings)
        if not report.ok:
            raise RotationError(f"A helyi próbavisszaállítás ellenőrzése sikertelen: {report.problems}")

    async def step_delete_old(self, state: RunState) -> None:
        if state.allow_delete_source_id != state.source_postgres_id:
            logger.info("Nincs törlési engedély; a régi példány megmarad")
            return
        if await self.render.get_postgres(state.source_postgres_id) is None:
            logger.info("A régi példány már törölve")
            return
        logger.warning("Régi példány törlése: %s", state.source_postgres_id)
        await self.render.delete_postgres(state.source_postgres_id)
        await self.render.wait_for_postgres_deleted(
            state.source_postgres_id, self.s.poll_interval_seconds, self.s.poll_timeout_seconds
        )
        logger.info("A törlés befejeződött")

    async def step_create_new(self, state: RunState) -> None:
        name = state.new_postgres_name or f"library-db-{state.run_id}"
        state.new_postgres_name = name
        instances = await self.render.list_postgres(self.s.render_owner_id)
        found, why = decisions.reconcile_created_instance(state.new_postgres_id, instances, name)
        logger.info("Egyeztetés: %s", why)
        if found is None and "bizonytalanság" in why:
            raise RotationError(why)
        if found is None:
            created = await self.render.create_postgres(
                name=name,
                owner_id=self.s.render_owner_id,
                plan=self.s.render_plan,
                region=self.s.render_region,
                version=self.s.render_pg_version,
                db_name=self.s.render_db_name,
                db_user=self.s.render_db_user,
            )
            found = created["id"]
            logger.info("Új példány létrehozva: %s", found)
        state.new_postgres_id = found
        self.store.save_run(state)
        await self.render.wait_for_postgres_status(
            found, "available", self.s.poll_interval_seconds, self.s.poll_timeout_seconds
        )

    async def step_restore_new(self, state: RunState) -> None:
        payload = self._payload(state)
        if await self.render.ensure_ip_allow_list(state.new_postgres_id):
            await asyncio.sleep(self.s.poll_interval_seconds)  # a hálózati szabály érvényre jutása
        info = await self.render.get_connection_info(state.new_postgres_id)
        target = info["externalConnectionString"]
        backup_ops.wait_for_database(target, self.s.poll_timeout_seconds, max(self.s.poll_interval_seconds, 1.0))
        try:
            backup_ops.restore_backup(payload, target)
        except RuntimeError as exc:
            # Újrafuttatás egy már sikeres, de nem rögzített visszaállítás után: nem duplikálunk
            logger.warning("%s – ellenőrzés meglévő tartalommal", exc)
        report = verify_restore(payload, target, self.reference_date, self.rule_settings)
        if not report.ok:
            raise RotationError(f"A felhős visszaállítás ellenőrzése sikertelen: {report.problems}")

    async def step_switch_backend(self, state: RunState) -> None:
        info = await self.render.get_connection_info(state.new_postgres_id)
        await self.render.set_env_var(self.s.render_service_id, "DATABASE_URL", info["internalConnectionString"])
        if state.deploy_id is None:
            deploy = await self.render.trigger_deploy(self.s.render_service_id)
            state.deploy_id = deploy["id"]
            self.store.save_run(state)
        await self.render.wait_for_deploy(
            self.s.render_service_id, state.deploy_id, self.s.poll_interval_seconds, self.s.poll_timeout_seconds
        )

    async def step_postcheck(self, state: RunState) -> None:
        payload = self._payload(state)
        health = await self.backend.health()
        if not health or not health.get("maintenance"):
            raise RotationError(f"A backend nincs karbantartási módban az átállás után: {health}")
        loans = await self.backend.list_loans(self.reference_date.isoformat())
        api_fees = {ln["id"]: ln["late_fee"] for ln in loans if ln["late_fee"] > 0}
        expected = payload["manifest"]["row_counts"].get("loans", 0)
        if len(loans) != expected:
            raise RotationError(f"A backend {len(loans)} kölcsönzést ad vissza, a mentésben {expected} van")
        expected_fees = rule_result_from_rows(payload["data"]["loans"], self.reference_date, self.rule_settings)
        if api_fees != expected_fees:
            raise RotationError(f"A backend szabályeredménye eltér: api={api_fees} mentés={expected_fees}")
        probe = await self.backend.write_probe(state.run_id)
        code = await self.backend.try_app_write()
        if code != 503:
            raise RotationError(f"Az alkalmazási írás nem tiltott az utóellenőrzéskor ({code})")
        logger.info(
            "Utóellenőrzés rendben: %d kölcsönzés, díjak egyeznek, próbaírás id=%s", len(loans), probe["probe_id"]
        )

    async def step_finalize(self, state: RunState) -> None:
        self.store.save_active(state.new_postgres_id, state.run_id, state.maintenance_at)
        await self.render.set_env_var(self.s.render_service_id, "MAINTENANCE_MODE", "false")
        await self.backend.set_maintenance(False)
        logger.info("Karbantartás feloldva; aktív példány: %s", state.new_postgres_id)


def describe_error(exc: Exception) -> str:
    if isinstance(exc, RenderAPIError):
        return f"Render API hiba ({exc.error_class}): {exc}"
    return f"{type(exc).__name__}: {exc}"
