"""Engine + session management.

- PostgreSQL in every environment (``DATABASE_URL``), via psycopg v3.
  A bare ``postgresql://`` URL is rewritten to ``postgresql+psycopg://``,
  since SQLAlchemy's bare scheme defaults to the psycopg2 driver.
- SQLite is supported ONLY for the throwaway test suite (see
  ``tests/conftest.py``): it is never a deployment target and never the
  default. If ``DATABASE_URL`` points at SQLite outside tests, the app
  refuses to start — silently running the restaurant on SQLite would be
  a data-loss bug waiting to happen.
- ``pool_pre_ping`` so stale pooled connections are detected before use.
"""
import logging
import os

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import settings

logger = logging.getLogger(__name__)


def normalise_database_url(url: str) -> str:
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


def build_engine(database_url: str | None = None):
    url = normalise_database_url(database_url or settings.database_url)
    if url.startswith("sqlite"):
        # Tests only: conftest.py sets TAVESSA_ALLOW_SQLITE=1 before import.
        if os.environ.get("TAVESSA_ALLOW_SQLITE") != "1":
            raise RuntimeError(
                "SQLite DATABASE_URL is only allowed in tests. "
                "Set DATABASE_URL to a PostgreSQL URL."
            )
        return create_engine(url, connect_args={"check_same_thread": False})
    return create_engine(url, pool_pre_ping=True)


engine = build_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_db():
    """FastAPI dependency: one session per request, always closed."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
