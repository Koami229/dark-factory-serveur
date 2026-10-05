"""
Preuve automatisée : deux (puis vingt) INSERT concurrents
sur le même (table_id, start_time) ne créent qu'une seule réservation.
"""

from __future__ import annotations

import os
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

# Base de test isolée — DOIT être définie avant l'import de l'application.
_FD, _DB_PATH = tempfile.mkstemp(prefix="resa_test_", suffix=".db")
os.close(_FD)
os.environ["DATABASE_URL"] = f"sqlite:///{Path(_DB_PATH).as_posix()}"

from fastapi.testclient import TestClient  # noqa: E402

from database import Base, SessionLocal, engine  # noqa: E402
from main import app  # noqa: E402
from models import Reservation, create_reservation  # noqa: E402

SLOT = datetime(2026, 12, 24, 19, 0, tzinfo=timezone.utc)


def setup_module(_module) -> None:
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


def teardown_module(_module) -> None:
    engine.dispose()
    for suffix in ("", "-wal", "-shm"):
        path = Path(f"{_DB_PATH}{suffix}")
        if path.exists():
            path.unlink()


def _count_rows() -> int:
    db = SessionLocal()
    try:
        return db.query(Reservation).count()
    finally:
        db.close()


def test_two_concurrent_requests_create_only_one_reservation() -> None:
    """Le test demandé : 2 POST simultanés = 1 seule réservation persistée."""
    client = TestClient(app)
    payload = {
        "table_id": 3,
        "start_time": SLOT.isoformat().replace("+00:00", "Z"),
        "customer_name": "Concurrent",
        "party_size": 2,
    }

    def book() -> int:
        return client.post(
            "/reservations",
            json=payload,
            headers={"X-Idempotency-Key": str(uuid4())},
        ).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(book) for _ in range(2)]
        codes = [future.result() for future in as_completed(futures)]

    assert codes.count(201) == 1, f"Un seul 201 attendu, obtenu {codes}"
    assert codes.count(409) == 1, f"Un seul 409 attendu, obtenu {codes}"
    assert _count_rows() == 1


def test_twenty_concurrent_inserts_via_business_layer() -> None:
    """Même preuve au niveau métier (20 threads, 20 sessions SQLite)."""
    start = datetime(2026, 12, 25, 20, 30, tzinfo=timezone.utc)

    def attempt(index: int) -> str:
        db = SessionLocal()
        try:
            create_reservation(
                db,
                table_id=5,
                start_time=start,
                customer_name=f"T{index}",
                party_size=2,
                idempotency_key=str(uuid4()),
            )
            return "ok"
        except Exception:
            return "ko"
        finally:
            db.close()

    with ThreadPoolExecutor(max_workers=20) as pool:
        futures = [pool.submit(attempt, i) for i in range(20)]
        results = [f.result() for f in as_completed(futures)]

    assert results.count("ok") == 1
    assert results.count("ko") == 19
    db = SessionLocal()
    try:
        n = (
            db.query(Reservation)
            .filter(Reservation.table_id == 5, Reservation.start_time == start)
            .count()
        )
    finally:
        db.close()
    assert n == 1


def test_idempotency_key_replays_the_same_reservation() -> None:
    client = TestClient(app)
    key = str(uuid4())
    body = {
        "table_id": 9,
        "start_time": "2026-12-26T18:00:00Z",
        "customer_name": "Idempotent",
        "party_size": 4,
    }
    first = client.post("/reservations", json=body, headers={"X-Idempotency-Key": key})
    second = client.post("/reservations", json=body, headers={"X-Idempotency-Key": key})
    assert first.status_code == 201
    assert second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
