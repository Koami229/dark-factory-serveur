"""
Modèles SQLAlchemy et logique métier des réservations.

La contrainte UNIQUE(table_id, start_time) est la barrière physique :
même si deux transactions passent le contrôle applicatif, SQLite
n'acceptera qu'une seule ligne pour un couple (table, créneau).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import DateTime, Integer, String, TypeDecorator, UniqueConstraint
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, mapped_column, Session

from database import Base

SLOT_MINUTES = 30
MIN_TABLE_ID = 1
MAX_TABLE_ID = 20
MIN_PARTY_SIZE = 1
MAX_PARTY_SIZE = 12


class AppError(Exception):
    """Erreur métier de base."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class InvalidSlotError(AppError):
    def __init__(self, message: str) -> None:
        super().__init__(message, status_code=422)


class DuplicateReservationError(AppError):
    def __init__(self, message: str = "Ce créneau est déjà réservé pour cette table.") -> None:
        super().__init__(message, status_code=409)


class NotFoundError(AppError):
    def __init__(self, message: str = "Réservation introuvable.") -> None:
        super().__init__(message, status_code=404)


class UTCDateTime(TypeDecorator):
    """Stocke tous les horodatages en UTC naïf (SQLite n'a pas de type TZ)."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, _dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    def process_result_value(self, value, _dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


class Reservation(Base):
    __tablename__ = "reservations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    table_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    start_time: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    customer_name: Mapped[str] = mapped_column(String(120), nullable=False)
    party_size: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    # Unique mais nullable : plusieurs réservations sans clé restent possibles.
    idempotency_key: Mapped[Optional[str]] = mapped_column(String(64), unique=True, nullable=True)

    __table_args__ = (
        UniqueConstraint("table_id", "start_time", name="uq_reservations_table_start"),
    )


def to_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def validate_slot(dt: datetime) -> datetime:
    """Les créneaux sont des blocs UTC de 30 minutes pile (mm=00 ou 30, ss=0)."""
    dt = to_utc(dt)
    if dt.second != 0 or dt.microsecond != 0 or dt.minute not in (0, 30):
        raise InvalidSlotError(
            "Les créneaux sont des blocs fixes de 30 minutes en UTC "
            "(minutes à 00 ou 30, secondes à 00)."
        )
    return dt


def validate_table_and_party(table_id: int, party_size: int) -> None:
    if not MIN_TABLE_ID <= table_id <= MAX_TABLE_ID:
        raise InvalidSlotError(f"table_id doit être entre {MIN_TABLE_ID} et {MAX_TABLE_ID}.")
    if not MIN_PARTY_SIZE <= party_size <= MAX_PARTY_SIZE:
        raise InvalidSlotError(
            f"party_size doit être entre {MIN_PARTY_SIZE} et {MAX_PARTY_SIZE}."
        )


def get_by_idempotency_key(db: Session, key: Optional[str]) -> Optional[Reservation]:
    if not key:
        return None
    return db.query(Reservation).filter(Reservation.idempotency_key == key).first()


def create_reservation(
    db: Session,
    table_id: int,
    start_time: datetime,
    customer_name: str,
    party_size: int,
    idempotency_key: Optional[str] = None,
) -> tuple[Reservation, bool]:
    """
    Crée une réservation sous verrou d'écriture SQLite.

    Retourne (reservation, is_replay) :
    - is_replay=True si la même X-Idempotency-Key a déjà abouti.
    """
    validate_table_and_party(table_id, party_size)
    slot = validate_slot(start_time)
    name = (customer_name or "").strip()
    if not name:
        raise InvalidSlotError("customer_name est obligatoire.")

    key = (idempotency_key or "").strip() or None

    # Rejeu idempotent : on renvoie l'enregistrement original sans rien créer.
    existing = get_by_idempotency_key(db, key)
    if existing is not None:
        return existing, True

    reservation = Reservation(
        table_id=table_id,
        start_time=slot,
        customer_name=name,
        party_size=party_size,
        created_at=datetime.now(timezone.utc),
        idempotency_key=key,
    )
    db.add(reservation)
    try:
        # BEGIN IMMEDIATE (voir database.py) sérialise les rédacteurs.
        # UNIQUE(table_id, start_time) refuse le second INSERT.
        db.commit()
        db.refresh(reservation)
        return reservation, False
    except IntegrityError:
        db.rollback()
        # Course sur la même clé d'idempotence : on rejoue le gagnant.
        replay = get_by_idempotency_key(db, key)
        if replay is not None:
            return replay, True
        raise DuplicateReservationError()


def list_reservations(db: Session) -> list[Reservation]:
    return (
        db.query(Reservation)
        .order_by(Reservation.start_time.asc(), Reservation.table_id.asc())
        .all()
    )


def delete_reservation(db: Session, reservation_id: int) -> None:
    reservation = db.query(Reservation).filter(Reservation.id == reservation_id).first()
    if reservation is None:
        raise NotFoundError()
    db.delete(reservation)
    db.commit()
