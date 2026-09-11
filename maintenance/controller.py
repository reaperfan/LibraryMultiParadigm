"""A karbantartó vezérlőprogram belépési pontja.

Parancsok (a projekt gyökeréből, ``python -m maintenance.controller <parancs>``):

  check     egyszeri állapotellenőrzés (szolgáltatói állapot, lejárat, backend), döntés naplózása
  run       időzített ellenőrzés APSchedulerrel; engedélyezett csere esetén automatikus csere
  rotate    csere indítása most (a kijelölt forrásra adott engedélyekkel)
  resume    félbeszakadt csere folytatása a rögzített futásállapotból
  backup    csak mentés a forrásról (újrafelhasználható művelet)
  restore   mentés visszaállítása és ellenőrzése egy megadott üres céladatbázisba

Engedélyek: ``--approve-source <dpg-id>`` a csere, ``--allow-delete <dpg-id>`` a régi
példány törlésének kifejezett engedélye. Ütemezett módban ugyanezek a
``MAINT_APPROVED_SOURCE_ID`` / ``MAINT_ALLOW_DELETE_SOURCE_ID`` változókból jönnek.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from backend.config import Settings as BackendRuleSettings
from maintenance import backup as backup_ops
from maintenance import decisions
from maintenance.backend_api import BackendClient
from maintenance.config import MaintenanceSettings
from maintenance.render_api import RenderClient, parse_render_datetime
from maintenance.rotation import RotationController, RotationError, describe_error
from maintenance.state import StateStore
from maintenance.verify import verify_restore

logger = logging.getLogger("maintenance")


def setup_logging(state_dir: Path) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    handlers: list[logging.Handler] = [
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(state_dir / "maintenance.log", encoding="utf-8"),
    ]
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        handlers=handlers,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)


def current_source_id(settings: MaintenanceSettings, store: StateStore) -> str:
    """Az aktív példány: a legutóbbi sikeres csere rögzített eredménye, különben a konfiguráció."""
    active = store.load_active()
    return active["postgres_id"] if active else settings.render_postgres_id


async def check_once(settings: MaintenanceSettings, store: StateStore) -> decisions.RotationDecision:
    """Aszinkron állapotellenőrzés: Render API + backend /health, majd tiszta döntés."""
    source_id = current_source_id(settings, store)
    async with (
        RenderClient(
            settings.render_api_key, settings.render_api_base, settings.http_timeout_seconds, settings.max_retries
        ) as render,
        BackendClient(settings.backend_url, settings.admin_token, settings.http_timeout_seconds) as backend,
    ):
        try:
            info = await render.get_postgres(source_id)
        except Exception as exc:  # átmeneti API-hiba: nem csereok
            logger.warning("Szolgáltatói állapot nem lekérdezhető: %s", describe_error(exc))
            info = None
        health = await backend.health()
    status = info.get("status") if info else None
    expires_at = parse_render_datetime(info.get("expiresAt")) if info else None
    active = store.load_active()
    already = datetime.fromisoformat(active["maintenance_at"]) if active and active.get("maintenance_at") else None
    decision = decisions.decide_rotation(
        now=datetime.now(UTC),
        status=status,
        expires_at=expires_at,
        maintenance_at=settings.maintenance_at,
        expiry_threshold_days=settings.expiry_threshold_days,
        already_rotated_for=already,
    )
    logger.info(
        "Ellenőrzés: példány=%s állapot=%s lejárat=%s backend=%s -> csere=%s (%s)",
        source_id,
        status,
        expires_at.isoformat() if expires_at else None,
        health,
        decision.should_rotate,
        decision.detail,
    )
    return decision


async def rotate(
    settings: MaintenanceSettings,
    store: StateStore,
    *,
    approved: str,
    allow_delete: str | None,
    reason: str,
    resume: bool = False,
) -> int:
    async with (
        RenderClient(
            settings.render_api_key, settings.render_api_base, settings.http_timeout_seconds, settings.max_retries
        ) as render,
        BackendClient(settings.backend_url, settings.admin_token, settings.http_timeout_seconds) as backend,
    ):
        controller = RotationController(settings, render, backend, store, BackendRuleSettings())
        try:
            if resume:
                await controller.resume()
            else:
                await controller.start(
                    source_id=current_source_id(settings, store),
                    approved_source_id=approved,
                    allow_delete_source_id=allow_delete,
                    reason=reason,
                    maintenance_at=settings.maintenance_at,
                )
            return 0
        except RotationError as exc:
            logger.error("Csere leállítva: %s", exc)
        except Exception as exc:
            logger.error("Csere hibával leállt: %s", describe_error(exc))
        return 1


async def scheduled_loop(
    settings: MaintenanceSettings, store: StateStore, approved: str | None, allow_delete: str | None
) -> None:
    """Időzített ellenőrzés; a cserét csak a kijelölt forrásra adott engedéllyel indítja."""
    rotating = asyncio.Lock()

    async def job() -> None:
        if rotating.locked():
            logger.info("Csere fut, az ellenőrzés kimarad")
            return
        decision = await check_once(settings, store)
        if not decision.should_rotate:
            return
        source_id = current_source_id(settings, store)
        if approved != source_id:
            logger.warning(
                "Csere esedékes (%s), de nincs engedély a(z) %s forrásra; nem indul", decision.reason, source_id
            )
            return
        async with rotating:
            await rotate(settings, store, approved=approved, allow_delete=allow_delete, reason=decision.reason)

    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(
        job,
        "interval",
        minutes=settings.check_interval_minutes,
        id="db-check",
        replace_existing=True,
        max_instances=1,
        next_run_time=datetime.now(UTC),
    )
    scheduler.start()
    logger.info("Ütemező elindult: %d percenként ellenőrzés (Ctrl+C: leállítás)", settings.check_interval_minutes)
    try:
        while True:
            await asyncio.sleep(3600)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        scheduler.shutdown(wait=False)
        logger.info("Ütemező leállt")


def cmd_backup(settings: MaintenanceSettings, args: argparse.Namespace) -> int:
    url = args.source_url or settings.source_database_url
    if not url:
        logger.error("Adja meg a forrást (--source-url vagy MAINT_SOURCE_DATABASE_URL)")
        return 1
    path = backup_ops.create_backup(
        url, args.source_id or settings.render_postgres_id or "local", settings.backup_dir, "manual"
    )
    print(path)
    return 0


def cmd_restore(settings: MaintenanceSettings, args: argparse.Namespace) -> int:
    payload = backup_ops.load_backup(Path(args.backup))
    problems = decisions.validate_manifest(payload["manifest"], backup_ops.compute_checksum(payload["data"]))
    if problems:
        logger.error("Érvénytelen mentés: %s", problems)
        return 1
    backup_ops.restore_backup(payload, args.target_url, allow_reset=args.reset)
    report = verify_restore(
        payload, args.target_url, datetime.fromisoformat(settings.rule_reference_date).date(), BackendRuleSettings()
    )
    print("Ellenőrzés:", "SIKERES" if report.ok else "SIKERTELEN", report.checks)
    return 0 if report.ok else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Adatbázis-karbantartó vezérlő")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check", help="egyszeri állapotellenőrzés")
    for name in ("run", "rotate"):
        p = sub.add_parser(name)
        p.add_argument(
            "--approve-source",
            default=os.getenv("MAINT_APPROVED_SOURCE_ID"),
            help="a cserélhető forrás dpg-azonosítója",
        )
        p.add_argument(
            "--allow-delete",
            default=os.getenv("MAINT_ALLOW_DELETE_SOURCE_ID"),
            help="a törölhető régi példány dpg-azonosítója",
        )
    sub.add_parser("resume", help="félbeszakadt csere folytatása")
    b = sub.add_parser("backup")
    b.add_argument("--source-url")
    b.add_argument("--source-id")
    r = sub.add_parser("restore")
    r.add_argument("--backup", required=True)
    r.add_argument("--target-url", required=True)
    r.add_argument("--reset", action="store_true", help="a cél sémájának eldobása visszaállítás előtt")
    return parser


def main(argv: list[str] | None = None) -> int:
    settings = MaintenanceSettings()
    setup_logging(settings.state_dir)
    store = StateStore(settings.state_dir)
    args = build_parser().parse_args(argv)

    if args.command == "check":
        asyncio.run(check_once(settings, store))
        return 0
    if args.command == "run":
        asyncio.run(scheduled_loop(settings, store, args.approve_source, args.allow_delete))
        return 0
    if args.command == "rotate":
        if not args.approve_source:
            logger.error("A cseréhez kötelező a --approve-source <dpg-id> engedély")
            return 1
        return asyncio.run(
            rotate(settings, store, approved=args.approve_source, allow_delete=args.allow_delete, reason="manual")
        )
    if args.command == "resume":
        return asyncio.run(rotate(settings, store, approved="", allow_delete=None, reason="resume", resume=True))
    if args.command == "backup":
        return cmd_backup(settings, args)
    if args.command == "restore":
        return cmd_restore(settings, args)
    return 2


if __name__ == "__main__":
    sys.exit(main())
