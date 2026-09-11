"""Backend-beállítások környezeti változókból (.env támogatással).

Semmilyen kapcsolati adat vagy titok nincs a kódba égetve; a mintaértékek a
.env.example fájlban találhatók.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """A backend futásidejű konfigurációja."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "sqlite:///./library.db"
    admin_token: str = "change-me"
    maintenance_mode: bool = False
    seed_on_startup: bool = True
    log_level: str = "INFO"

    # Témaspecifikus szabály paraméterei
    loan_period_days: int = 14
    max_active_loans: int = 3
    daily_late_fee: int = 50
    max_late_fee: int = 2000


@lru_cache
def get_settings() -> Settings:
    """Gyorsítótárazott beállításpéldány (a tesztek felülírhatják a cache törlésével)."""
    return Settings()
