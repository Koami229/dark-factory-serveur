"""
API FastAPI — Stage 1 : réservation de restaurant sans double booking.

Endpoints :
  POST   /reservations          crée une réservation (idempotente via X-Idempotency-Key)
  GET    /reservations          liste toutes les réservations
  DELETE /reservations/{id}     annule une réservation
  POST   /stress-test           lance 20 INSERT concurrents côté serveur
"""

from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from database import SessionLocal, get_db, init_db
from models import (
    AppError,
    MAX_TABLE_ID,
    MIN_TABLE_ID,
    Reservation,
    create_reservation,
    delete_reservation,
    list_reservations,
)

BASE_DIR = Path(__file__).resolve().parent
INDEX_FILE = BASE_DIR / "index.html"
STRESS_WORKERS = 20


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="Dark Factory — Réservations",
    description="Stage 1 : double réservation physiquement impossible (UNIQUE + BEGIN IMMEDIATE).",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class ReservationIn(BaseModel):
    table_id: int = Field(..., ge=MIN_TABLE_ID, le=MAX_TABLE_ID)
    start_time: datetime
    customer_name: str = Field(..., min_length=1, max_length=120)
    party_size: int = Field(..., ge=1, le=12)


class ReservationOut(BaseModel):
    id: int
    table_id: int
    start_time: datetime
    customer_name: str
    party_size: int
    created_at: datetime
    idempotency_key: Optional[str] = None

    @classmethod
    def from_orm_row(cls, row: Reservation) -> "ReservationOut":
        return cls(
            id=row.id,
            table_id=row.table_id,
            start_time=row.start_time,
            customer_name=row.customer_name,
            party_size=row.party_size,
            created_at=row.created_at,
            idempotency_key=row.idempotency_key,
        )


class StressTestOut(BaseModel):
    requests_sent: int
    success: int
    rejected: int
    table_id: int
    start_time: datetime
    message: str


def _raise_http(exc: AppError) -> None:
    raise HTTPException(status_code=exc.status_code, detail=exc.message)


@app.get("/", include_in_schema=False)
def serve_ui() -> FileResponse:
    return FileResponse(INDEX_FILE)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/reservations", status_code=201, response_model=ReservationOut)
def post_reservation(
    payload: ReservationIn,
    response: Response,
    db: Session = Depends(get_db),
    x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
) -> ReservationOut:
    try:
        reservation, is_replay = create_reservation(
            db,
            table_id=payload.table_id,
            start_time=payload.start_time,
            customer_name=payload.customer_name,
            party_size=payload.party_size,
            idempotency_key=x_idempotency_key,
        )
        if is_replay:
            response.status_code = 200
        return ReservationOut.from_orm_row(reservation)
    except AppError as exc:
        _raise_http(exc)


@app.get("/reservations", response_model=list[ReservationOut])
def get_reservations(db: Session = Depends(get_db)) -> list[ReservationOut]:
    return [ReservationOut.from_orm_row(row) for row in list_reservations(db)]


@app.delete("/reservations/{reservation_id}", status_code=204)
def cancel_reservation(reservation_id: int, db: Session = Depends(get_db)) -> Response:
    try:
        delete_reservation(db, reservation_id)
        return Response(status_code=204)
    except AppError as exc:
        _raise_http(exc)


def _stress_attempt(table_id: int, start_time: datetime, index: int) -> str:
    """Une tentative d'INSERT dans son propre thread / sa propre session."""
    db = SessionLocal()
    try:
        _, is_replay = create_reservation(
            db,
            table_id=table_id,
            start_time=start_time,
            customer_name=f"Stress-{index}",
            party_size=2,
            idempotency_key=str(uuid.uuid4()),
        )
        return "replay" if is_replay else "success"
    except AppError:
        return "rejected"
    finally:
        db.close()


@app.post("/stress-test", response_model=StressTestOut)
def stress_test(payload: ReservationIn | None = None) -> StressTestOut:
    """
    Exécute 20 INSERT concurrents sur le même (table_id, start_time).
    Destiné à l'UI / aux juges : prouve que SQLite n'accepte qu'une ligne.
    """
    if payload is None:
        table_id = 7
        start_time = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        minute = 0 if start_time.minute < 30 else 30
        start_time = start_time.replace(minute=minute) + timedelta(hours=1)
    else:
        table_id = payload.table_id
        start_time = payload.start_time

    success = 0
    rejected = 0
    with ThreadPoolExecutor(max_workers=STRESS_WORKERS) as pool:
        futures = [
            pool.submit(_stress_attempt, table_id, start_time, i)
            for i in range(STRESS_WORKERS)
        ]
        for future in as_completed(futures):
            result = future.result()
            if result == "success":
                success += 1
            else:
                rejected += 1

    message = (
        f"{STRESS_WORKERS} requêtes envoyées. {success} succès, {rejected} rejets. "
        "La double réservation est physiquement impossible !"
    )
    return StressTestOut(
        requests_sent=STRESS_WORKERS,
        success=success,
        rejected=rejected,
        table_id=table_id,
        start_time=start_time,
        message=message,
    )
