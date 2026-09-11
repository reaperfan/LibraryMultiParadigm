"""Egységtesztek a karbantartási döntésekhez: átmeneti szolgáltatói hiba, hibás/hiányzó
mentés, félbeszakadt létrehozás utáni újrafuttatás. A szolgáltatói hívásokat
``httpx.MockTransport`` helyettesíti; élő API-t a tesztek nem érnek el."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from maintenance import decisions
from maintenance.backup import compute_checksum, load_backup
from maintenance.render_api import RenderAPIError, RenderClient

NOW = datetime(2026, 9, 11, 12, 0, tzinfo=UTC)


# ----------------------------------------------------------------- csere-döntés
@pytest.mark.parametrize(
    ("status", "expires_in_days", "maintenance_at", "already", "expected"),
    [
        ("available", 30, None, None, False),  # nincs ok
        ("available", 2, None, None, True),  # lejárat 3 napon belül
        ("available", 3, None, None, True),  # határeset: pontosan a küszöbön
        ("available", 4, None, None, False),  # küszöb felett
        ("available", 30, NOW - timedelta(minutes=1), None, True),  # tervezett karbantartás elérve
        ("available", 30, NOW + timedelta(hours=1), None, False),  # még nem esedékes
        ("available", 30, NOW - timedelta(minutes=1), NOW - timedelta(minutes=1), False),  # már teljesítve
        (None, 1, NOW - timedelta(minutes=1), None, False),  # elérhetetlen szolgáltatás: nem ok
        ("creating", 1, None, None, False),  # nem használható állapot
        ("available", None, None, None, False),  # ismeretlen lejárat, nincs időpont
    ],
)
def test_decide_rotation(status, expires_in_days, maintenance_at, already, expected) -> None:
    expires_at = NOW + timedelta(days=expires_in_days) if expires_in_days is not None else None
    decision = decisions.decide_rotation(
        now=NOW,
        status=status,
        expires_at=expires_at,
        maintenance_at=maintenance_at,
        expiry_threshold_days=3,
        already_rotated_for=already,
    )
    assert decision.should_rotate is expected


def test_decide_rotation_compares_naive_times_as_utc() -> None:
    naive_maintenance = datetime(2026, 9, 11, 11, 59)  # naiv → UTC-nek tekintjük
    decision = decisions.decide_rotation(
        now=NOW, status="available", expires_at=None, maintenance_at=naive_maintenance, expiry_threshold_days=3
    )
    assert decision.should_rotate and decision.reason == decisions.RotationReason.SCHEDULED_MAINTENANCE


# ------------------------------------------------- átmeneti szolgáltatói hiba
@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (500, decisions.ErrorClass.RETRYABLE),
        (429, decisions.ErrorClass.RETRYABLE),
        (401, decisions.ErrorClass.AUTH),
        (403, decisions.ErrorClass.AUTH),
        (402, decisions.ErrorClass.QUOTA),
        (404, decisions.ErrorClass.NOT_FOUND),
        (400, decisions.ErrorClass.FATAL),
    ],
)
def test_classify_http_error(code: int, expected: decisions.ErrorClass) -> None:
    assert decisions.classify_http_error(code) == expected


@pytest.mark.asyncio
async def test_render_client_retries_transient_error_then_succeeds(monkeypatch) -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) < 3:
            return httpx.Response(503, json={"message": "temporarily unavailable"})
        return httpx.Response(200, json={"id": "dpg-1", "status": "available"})

    async def no_sleep(_: float) -> None:
        return None

    monkeypatch.setattr("maintenance.render_api.asyncio.sleep", no_sleep)
    async with RenderClient("key", transport=httpx.MockTransport(handler), max_retries=3) as client:
        info = await client.get_postgres("dpg-1")
    assert info["status"] == "available" and len(calls) == 3


@pytest.mark.asyncio
async def test_render_client_gives_up_after_max_retries(monkeypatch) -> None:
    async def no_sleep(_: float) -> None:
        return None

    monkeypatch.setattr("maintenance.render_api.asyncio.sleep", no_sleep)
    transport = httpx.MockTransport(lambda r: httpx.Response(500, json={"message": "boom"}))
    async with RenderClient("key", transport=transport, max_retries=2) as client:
        with pytest.raises(RenderAPIError) as exc:
            await client.get_postgres("dpg-1")
    assert exc.value.error_class == decisions.ErrorClass.RETRYABLE


@pytest.mark.asyncio
async def test_render_client_does_not_retry_auth_or_quota_errors() -> None:
    for code, expected in ((401, decisions.ErrorClass.AUTH), (402, decisions.ErrorClass.QUOTA)):
        transport = httpx.MockTransport(lambda r, c=code: httpx.Response(c, json={"message": "no"}))
        async with RenderClient("key", transport=transport) as client:
            with pytest.raises(RenderAPIError) as exc:
                await client.create_postgres(
                    name="x", owner_id="o", plan="free", region="frankfurt", version="16", db_name="d", db_user="u"
                )
        assert exc.value.error_class == expected


# ------------------------------------------------- hiányzó vagy hibás mentés
def test_validate_manifest_missing_and_corrupt(tmp_path: Path) -> None:
    assert decisions.validate_manifest(None) == ["A mentés metaadata hiányzik"]
    partial = {"format_version": 1, "created_at": "x", "source_id": "dpg-1"}
    problems = decisions.validate_manifest(partial)
    assert any("tables" in p for p in problems) and any("checksum" in p for p in problems)
    data = {"books": [{"id": 1}]}
    good = {
        "format_version": 1,
        "created_at": "x",
        "source_id": "dpg-1",
        "tables": ["books"],
        "row_counts": {"books": 1},
        "checksum": compute_checksum(data),
    }
    assert decisions.validate_manifest(good, compute_checksum(data)) == []
    tampered = compute_checksum({"books": [{"id": 2}]})
    assert decisions.validate_manifest(good, tampered) == [
        "A mentés ellenőrzőösszege nem egyezik (sérült vagy módosított állomány)"
    ]
    assert decisions.validate_manifest({**good, "format_version": 99}) == ["Nem támogatott mentésformátum: 99"]

    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError):
        load_backup(broken)
    bad_shape = tmp_path / "shape.json"
    bad_shape.write_text(json.dumps({"data": {}}), encoding="utf-8")
    with pytest.raises(ValueError):
        load_backup(bad_shape)


# ------------------------------- félbeszakadt létrehozás utáni újrafuttatás
def test_next_step_resumes_after_interrupted_create() -> None:
    done = ["preflight", "freeze_writes", "backup", "verify_local_restore", "delete_old"]
    assert decisions.next_step(done) == "create_new"
    assert decisions.next_step(list(decisions.STEPS)) is None


@pytest.mark.parametrize(
    ("recorded", "instances", "expected_id", "fragment"),
    [
        ("dpg-new", [{"id": "dpg-new", "name": "library-db-run1"}], "dpg-new", "rögzített példány létezik"),
        ("dpg-new", [], None, "nem található"),
        (None, [{"id": "dpg-a", "name": "library-db-run1"}], "dpg-a", "pontosan egy"),
        (None, [], None, "létrehozás indítható"),
        (
            None,
            [{"id": "dpg-a", "name": "library-db-run1"}, {"id": "dpg-b", "name": "library-db-run1"}],
            None,
            "bizonytalanság",
        ),
    ],
)
def test_reconcile_created_instance(recorded, instances, expected_id, fragment) -> None:
    found, why = decisions.reconcile_created_instance(recorded, instances, "library-db-run1")
    assert found == expected_id
    assert fragment in why
