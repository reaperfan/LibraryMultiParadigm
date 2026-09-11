"""A karbantartás döntési szabályai – tiszta függvények, I/O nélkül.

Ezek a függvények teszik egységtesztelhetővé, hogy a vezérlő *mikor* cserél,
*mit* próbál újra, és egy félbeszakadt futás *honnan* folytatható. A tényleges
hálózati és adatbázis-műveletek a ``render_api``, ``backup`` és ``rotation``
modulokban vannak.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum


class RotationReason(StrEnum):
    NONE = "none"
    SCHEDULED_MAINTENANCE = "scheduled_maintenance"
    EXPIRY_APPROACHING = "expiry_approaching"


@dataclass(frozen=True)
class RotationDecision:
    should_rotate: bool
    reason: RotationReason
    detail: str


def _as_utc(value: datetime) -> datetime:
    """Egységes időzóna: naiv értéket UTC-nek tekintünk, a többit UTC-re váltjuk."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def decide_rotation(
    *,
    now: datetime,
    status: str | None,
    expires_at: datetime | None,
    maintenance_at: datetime | None,
    expiry_threshold_days: int,
    already_rotated_for: datetime | None = None,
) -> RotationDecision:
    """Kell-e most cserélni.

    Csere oka: elért tervezett karbantartási időpont vagy közelgő lejárat.
    Nem ok: elérhetetlen szolgáltatás (``status`` None), üres tábla, ismeretlen
    lejárat. Egy már teljesített karbantartási időpont (``already_rotated_for``)
    nem indít ismételt cserét.
    """
    now = _as_utc(now)
    if status is None:
        return RotationDecision(False, RotationReason.NONE, "A szolgáltatói állapot nem elérhető; csere nem indul")
    if status != "available":
        return RotationDecision(False, RotationReason.NONE, f"A példány állapota '{status}', nem cserélhető")
    if maintenance_at is not None:
        m_at = _as_utc(maintenance_at)
        done = already_rotated_for is not None and _as_utc(already_rotated_for) == m_at
        if m_at <= now and not done:
            return RotationDecision(
                True, RotationReason.SCHEDULED_MAINTENANCE, f"Tervezett karbantartás esedékes ({m_at.isoformat()})"
            )
    if expires_at is not None:
        e_at = _as_utc(expires_at)
        if e_at - now <= timedelta(days=expiry_threshold_days):
            return RotationDecision(True, RotationReason.EXPIRY_APPROACHING, f"A példány {e_at.isoformat()}-kor lejár")
    return RotationDecision(False, RotationReason.NONE, "Nincs csereok")


class ErrorClass(StrEnum):
    RETRYABLE = "retryable"  # átmeneti hiba vagy híváskorlát: korlátozott újrapróbálkozás
    AUTH = "auth"  # jogosultsági hiba: leállás, nincs automatikus csomagváltás
    QUOTA = "quota"  # kvóta/fizetés: leállás, nincs automatikus csomagváltás
    NOT_FOUND = "not_found"
    FATAL = "fatal"


def classify_http_error(status_code: int) -> ErrorClass:
    if status_code in (401, 403):
        return ErrorClass.AUTH
    if status_code == 402:
        return ErrorClass.QUOTA
    if status_code == 429 or status_code >= 500:
        return ErrorClass.RETRYABLE
    if status_code == 404:
        return ErrorClass.NOT_FOUND
    return ErrorClass.FATAL


def backoff_seconds(attempt: int, base: float = 2.0, cap: float = 30.0) -> float:
    """Exponenciális várakozás: 2, 4, 8 ... legfeljebb ``cap`` mp."""
    return min(cap, base * (2 ** max(0, attempt - 1)))


REQUIRED_MANIFEST_KEYS = ("format_version", "created_at", "source_id", "tables", "row_counts", "checksum")


def validate_manifest(manifest: dict | None, computed_checksum: str | None = None) -> list[str]:
    """Hibalista a mentés metaadatairól; üres lista = érvényes."""
    problems: list[str] = []
    if not manifest:
        return ["A mentés metaadata hiányzik"]
    for key in REQUIRED_MANIFEST_KEYS:
        if key not in manifest:
            problems.append(f"Hiányzó kulcs a metaadatban: {key}")
    if manifest.get("format_version") not in (None, 1):
        problems.append(f"Nem támogatott mentésformátum: {manifest.get('format_version')}")
    if computed_checksum is not None and manifest.get("checksum") not in (None, computed_checksum):
        problems.append("A mentés ellenőrzőösszege nem egyezik (sérült vagy módosított állomány)")
    return problems


# A csere lépései végrehajtási sorrendben (6.3. szakasz)
STEPS = (
    "preflight",
    "freeze_writes",
    "backup",
    "verify_local_restore",
    "delete_old",
    "create_new",
    "restore_new",
    "switch_backend",
    "postcheck",
    "finalize",
)


def next_step(completed: list[str]) -> str | None:
    """A következő végrehajtandó lépés, vagy None, ha minden lépés kész."""
    for step in STEPS:
        if step not in completed:
            return step
    return None


def reconcile_created_instance(
    recorded_id: str | None, provider_instances: list[dict], run_name: str
) -> tuple[str | None, str]:
    """Időtúllépés utáni egyeztetés: létrejött-e már az új példány.

    Visszaad: (példány-azonosító vagy None, magyarázat). Ha a futás rögzített
    azonosítója megvan a szolgáltatónál, azt használjuk. Ha nincs rögzítve, de a
    futás nevével pontosan egy példány létezik, azt fogadjuk el. Több találatnál
    nem találgatunk: None, és a hívónak hibával le kell állnia.
    """
    if recorded_id:
        if any(inst.get("id") == recorded_id for inst in provider_instances):
            return recorded_id, "A rögzített példány létezik a szolgáltatónál"
        return None, "A rögzített példány nem található a szolgáltatónál; a létrehozás megismételhető"
    matches = [inst["id"] for inst in provider_instances if inst.get("name") == run_name]
    if len(matches) == 1:
        return matches[0], "A futás nevével pontosan egy példány található"
    if not matches:
        return None, "Nincs a futáshoz tartozó példány; a létrehozás indítható"
    return None, f"Több példány is a futás nevét viseli ({matches}); feloldhatatlan bizonytalanság"
