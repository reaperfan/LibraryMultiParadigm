"""A karbantartó program beállításai (``.env`` / környezeti változók, ``MAINT_`` előtaggal)."""

from datetime import datetime
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class MaintenanceSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", env_prefix="MAINT_", extra="ignore")

    # Render API
    render_api_key: str = ""
    render_owner_id: str = ""  # workspace (owner) azonosító: tea-... / usr-...
    render_service_id: str = ""  # a backend webszolgáltatás: srv-...
    render_postgres_id: str = ""  # az aktuális (forrás) adatbázis: dpg-...
    render_region: str = "frankfurt"
    render_plan: str = "free"
    render_pg_version: str = "16"
    render_db_name: str = "library"
    render_db_user: str = "library"
    render_api_base: str = "https://api.render.com/v1"

    # Backend
    backend_url: str = "http://127.0.0.1:8000"
    admin_token: str = "change-me"

    # Adatbázis-kapcsolatok a vezérlő gépéről nézve
    source_database_url: str = ""  # a forrás külső kapcsolati címe (mentéshez)
    verify_database_url: str = ""  # elkülönített (pl. helyi) Postgres a próbavisszaállításhoz

    # Ütemezés és döntés
    check_interval_minutes: int = 60
    expiry_threshold_days: int = 3  # ennyi nappal a lejárat előtt esedékes a csere
    maintenance_at: datetime | None = None  # tervezett karbantartási időpont (ISO, időzónával)
    rule_reference_date: str = "2026-09-11"  # a visszaállítás-ellenőrzés rögzített referencia-napja

    # Időkorlátok, újrapróbálkozás
    http_timeout_seconds: float = 30.0
    max_retries: int = 3
    poll_interval_seconds: float = 10.0
    poll_timeout_seconds: float = 900.0

    # Helyi tartós állományok
    backup_dir: Path = Path("backups")
    state_dir: Path = Path("state")

    @field_validator("maintenance_at", mode="before")
    @classmethod
    def _empty_is_none(cls, value: object) -> object:
        """Üres környezeti változó = nincs tervezett karbantartási időpont."""
        return None if isinstance(value, str) and not value.strip() else value
