"""Concurrent kitchen transitions — Stage 4.

REAL PostgreSQL ONLY (ephemeral pgserver), reusing the approach of
tests/test_orders_concurrency.py: one module-scoped cluster (a fresh
initdb per test is flaky), per-request
sessions, the limiter disabled only while this module's fixture is
active, delta assertions on the shared module DB.

What this proves: the ``SELECT ... FOR UPDATE`` row lock in
``kitchen_service.transition_order`` serialises concurrent PATCHes
on the same order — exactly one succeeds, the rest see the new
state and 409, and the order ends at version 2 with its items in
``preparing``. SQLite cannot prove this (its locking semantics
differ), so this test must run where pgserver is installed.
"""
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest

pgserver = pytest.importorskip("pgserver")

from tests.pg_support import pg_cluster_dir, remove_pg_cluster_dir

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import QueuePool

from app.core.rate_limit import limiter
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models import DiningTable, Order, Restaurant
from app.seed.zaika import RESTAURANT_SLUG, seed_zaika
from app.services.table_service import create_table_token
from tests.helpers import headers, menu_by_name, open_session

WORKERS = 8
DEV_KEY = "dev-kitchen-only"


def _pg_url(server) -> str:
    uri = server.get_uri()
    assert uri.startswith("postgresql://"), uri
    return uri.replace("postgresql://", "postgresql+psycopg://", 1)


@pytest.fixture(scope="module")
def pg_client():
    cluster_dir = pg_cluster_dir("pgk4")
    server = pgserver.get_server(str(cluster_dir))
    url = _pg_url(server)
    engine = create_engine(url, poolclass=QueuePool, pool_size=10, max_overflow=5)
    assert engine.url.get_dialect().name == "postgresql", engine.url
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def _override_get_db():
        session = factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = _override_get_db
    was_enabled = limiter.enabled
    limiter.enabled = False
    try:
        setup = factory()
        try:
            seed_zaika(setup)
            restaurant = (
                setup.execute(
                    select(Restaurant).where(Restaurant.slug == RESTAURANT_SLUG)
                )
                .scalars()
                .first()
            )
            table = (
                setup.execute(
                    select(DiningTable).where(
                        DiningTable.restaurant_id == restaurant.id,
                        DiningTable.number == 5,
                    )
                )
                .scalars()
                .first()
            )
            raw, _ = create_table_token(setup, table)
            setup.commit()
            restaurant_id = restaurant.id
        finally:
            setup.close()
        with TestClient(app) as client:
            open_session(client, raw)
            menu, _ = menu_by_name(client, raw)
        yield {
            "mk_client": lambda: TestClient(app),
            "session_factory": factory,
            "token": raw,
            "menu": menu,
            "restaurant_id": restaurant_id,
        }
    finally:
        app.dependency_overrides.clear()
        limiter.enabled = was_enabled
        engine.dispose()
        server.cleanup()
        remove_pg_cluster_dir(cluster_dir)


def _place_order(mk_client, token, menu, key):
    body = {"items": [{"menuItemId": menu["Butter Naan"]["id"], "quantity": 1}]}
    with mk_client() as client:
        response = client.post(
            "/api/v1/orders",
            json=body,
            headers={**headers(token), "Idempotency-Key": key},
        )
    assert response.status_code == 201, response.text
    return response.json()["order"]


def _patch_burst(mk_client, order_id, restaurant_id, workers=WORKERS):
    """Fire `workers` simultaneous PATCH preparing on one order."""
    barrier = threading.Barrier(workers)

    def _one(_i):
        with mk_client() as client:
            barrier.wait(timeout=30)
            return client.patch(
                f"/api/v1/kitchen/orders/{order_id}/status"
                f"?restaurant_id={restaurant_id}",
                json={"status": "preparing"},
                headers={"X-Kitchen-Dev-Key": DEV_KEY},
            )

    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(_one, range(workers)))


def test_concurrent_patch_same_order_serialises(pg_client):
    order = _place_order(
        pg_client["mk_client"],
        pg_client["token"],
        pg_client["menu"],
        f"k-conc-{uuid.uuid4().hex}",
    )
    responses = _patch_burst(
        pg_client["mk_client"], order["id"], pg_client["restaurant_id"]
    )
    ok = [r for r in responses if r.status_code == 200]
    conflicts = [r for r in responses if r.status_code == 409]
    assert len(ok) == 1, [(r.status_code, r.text) for r in responses]
    assert len(conflicts) == WORKERS - 1
    assert all(
        r.json()["error"]["code"] == "conflict" for r in conflicts
    )
    # Exactly one transition happened: version 1 -> 2, items preparing.
    session = pg_client["session_factory"]()
    try:
        row = session.get(Order, uuid.UUID(order["id"]))
        assert row.status == "preparing"
        assert row.version == 2
        assert row.accepted_at is not None
        assert all(i.status == "preparing" for i in row.items)
    finally:
        session.close()
