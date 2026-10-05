"""
Configuration SQLite pour le service de réservation.

Recette officielle SQLAlchemy pour SQLite concurrent :
- WAL : lecteurs non bloqués par un rédacteur
- isolation_level = None : on émet nous-mêmes le BEGIN
- BEGIN IMMEDIATE : verrou d'écriture pris dès le début de transaction
  (élimine la fenêtre de course read-then-write)
"""

from __future__ import annotations

import os

from sqlalchemy import create_engine, event
from sqlalchemy.orm import declarative_base, sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./restaurant.db")

# timeout = temps d'attente du verrou SQLite avant OperationalError
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False, "timeout": 30},
    pool_pre_ping=True,
)

Base = declarative_base()


@event.listens_for(engine, "connect")
def _configure_sqlite(dbapi_connection, _connection_record) -> None:
    """Active WAL, un timeout de lock, et désactive le BEGIN auto de pysqlite."""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=30000")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()
    # Empêche pysqlite d'émettre son propre BEGIN/COMMIT implicite.
    dbapi_connection.isolation_level = None


@event.listens_for(engine, "begin")
def _do_begin(conn) -> None:
    """Prend un verrou d'écriture immédiat : une seule transaction rédactrice à la fois."""
    conn.exec_driver_sql("BEGIN IMMEDIATE")


SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_db():
    """Dépendance FastAPI : une session par requête, toujours fermée."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """Crée les tables si elles n'existent pas encore."""
    # Import local pour enregistrer les modèles sur Base.metadata.
    from models import Reservation  # noqa: F401

    Base.metadata.create_all(bind=engine)
