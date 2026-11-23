"""Test setup — Stage 1.

The app refuses SQLite outside tests (see app/db/session.py). Tests are
the one exception: they run against an in-memory SQLite database via a
dedicated engine, so the suite runs anywhere without a Postgres server.
Any test that depends on Postgres-only behaviour (Stage 3+) must run
against test Postgres instead — see tests/README.md when that lands.
"""
import os

os.environ["DATABASE_URL"] = "sqlite://"
os.environ["SECRET_KEY"] = "test-only-secret"
os.environ["TAVESSA_ALLOW_SQLITE"] = "1"

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.rate_limit import limiter
from app.db.base import Base
from app.db.session import get_db
from app.main import app

# The slowapi limiter is an in-process, per-IP guard for production
# abuse (60 order placements/minute from one IP). The whole SQLite
# suite shares one TestClient IP ("testclient") and one process, so
# the caps are exhausted by legitimate test traffic long before the
# suite finishes — order placement then 429s and unrelated tests
# fail in a cascade. No test asserts rate-limit behaviour, so the
# limiter is disabled for the suite (the Postgres concurrency
# modules already did this in their own fixtures). The limiter still
# runs in production; this only affects tests.
limiter.enabled = False

engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base.metadata.create_all(bind=engine)


@pytest.fixture
def db_session():
    session = TestingSession()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client(db_session):
    def _override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
