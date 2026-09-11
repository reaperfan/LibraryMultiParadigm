"""API-integrációs teszt: valódi FastAPI-kérés → feldolgozás → tesztadatbázis → válasz.

A ``client`` fixture a valódi alkalmazást és a valódi SQLAlchemy-kódot futtatja
egy elkülönített, minden tesztnél újra létrehozott SQLite-adatbázison.
"""

from sqlalchemy import select

from backend.models import Loan, Member


def test_loan_roundtrip_api_to_database(client, db_session) -> None:
    # előre betöltött adat lekérdezése
    response = client.get("/books/6")
    assert response.status_code == 200
    assert response.json()["available_copies"] == 2

    # létrehozás a szabályon keresztül (tag #3: 1 aktív, nem késedelmes kölcsönzés a 2026-09-11 referencia-napon)
    response = client.post("/loans?reference_date=2026-09-11", json={"book_id": 6, "member_id": 3})
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["due_date"] == "2026-09-25" and body["late_fee"] == 0
    loan_id = body["id"]

    # az adatbázis kapcsolódó állapota
    stored = db_session.scalar(select(Loan).where(Loan.id == loan_id))
    assert stored is not None and stored.book_id == 6 and stored.returned_at is None

    # visszaolvasás az API-n át: a szabad példányszám csökkent
    assert client.get("/books/6").json()["available_copies"] == 1

    # ugyanaz a könyv ugyanannak a tagnak: a szabály elutasít, az adatbázis nem változik
    response = client.post("/loans?reference_date=2026-09-11", json={"book_id": 6, "member_id": 3})
    assert response.status_code == 422
    assert "A tag már kint tartja ezt a könyvet" in response.json()["reasons"]
    db_session.expire_all()
    assert (
        db_session.scalar(select(Loan).where(Loan.book_id == 6, Loan.member_id == 3, Loan.returned_at.is_(None)))
        is not None
    )
    assert len(client.get("/loans?status=all").json()) == 7

    # visszavétel késedelemmel: 2026-09-30 → 5 nap × 50
    response = client.post(f"/loans/{loan_id}/return", json={"returned_at": "2026-09-30"})
    assert response.status_code == 200 and response.json()["late_fee"] == 250
    db_session.expire_all()
    assert db_session.get(Loan, loan_id).returned_at.isoformat() == "2026-09-30"


def test_error_responses(client) -> None:
    assert client.get("/books/999").status_code == 404
    assert client.post("/loans", json={"book_id": 999, "member_id": 1}).status_code == 404
    assert (
        client.post("/books", json={"title": "", "author": "x", "year": 1, "isbn": "1", "copies_total": 0}).status_code
        == 422
    )
    assert client.post("/members", json={"name": "Dup", "email": "anna.kiss@example.com"}).status_code == 409


def test_maintenance_mode_blocks_app_writes_but_allows_probe(client, db_session, admin_headers) -> None:
    assert client.post("/admin/maintenance", json={"enabled": True}).status_code == 401  # token nélkül tiltott
    assert client.post("/admin/maintenance", json={"enabled": True}, headers=admin_headers).json() == {"enabled": True}
    assert client.get("/health").json()["maintenance"] is True

    response = client.post("/members", json={"name": "Új Tag", "email": "uj@example.com"})
    assert response.status_code == 503
    assert db_session.scalar(select(Member).where(Member.email == "uj@example.com")) is None
    assert client.get("/books").status_code == 200  # olvasás továbbra is megy

    probe = client.post("/admin/write-probe", json={"run_id": "run-1"}, headers=admin_headers)
    assert probe.status_code == 200 and probe.json()["probe_id"] >= 1

    client.post("/admin/maintenance", json={"enabled": False}, headers=admin_headers)
    assert client.post("/members", json={"name": "Új Tag", "email": "uj@example.com"}).status_code == 201
