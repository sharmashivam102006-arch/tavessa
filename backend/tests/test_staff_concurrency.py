"""Concurrent staff serves — Stage 5.

REAL PostgreSQL ONLY (ephemeral pgserver), reusing the approach of
tests/test_kitchen_concurrency.py: one module-scoped cluster (a fresh
initdb per test is flaky), per-request
sessions, the limiter disabled only while this module's fixture is
active, delta assertions on the shared module DB.

What this proves: the ``SELECT ... FOR UPDATE`` row lock in
``staff_service.serve_order`` serialises concurrent serves on the
same ready order — exactly one succeeds, the rest see the new state
and 409, and the order ends served at version 4 with its items
served. SQLite cannot prove this (its locking semantics differ), so
this test must run where pgserver is installed.
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
STAFF_KEY = "dev-staff-only"
KITCHEN_KEY = "dev-kitchen-only"


def _pg_url(server) -> str:
    uri = server.get_uri()
    assert uri.startswith("postgresql://"), uri
    return uri.replace("postgresql://", "postgresql+psycopg://", 1)


@pytest.fixture(scope="module")
def pg_client():
    cluster_dir = pg_cluster_dir("pgk5")
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


def _place_ready_order(mk_client, token, menu, restaurant_id, key):
    """Place an order and drive it to ready via the kitchen API."""
    body = {"items": [{"menuItemId": menu["Butter Naan"]["id"], "quantity": 1}]}
    with mk_client() as client:
        response = client.post(
            "/api/v1/orders",
            json=body,
            headers={**headers(token), "Idempotency-Key": key},
        )
    assert response.status_code == 201, response.text
    order = response.json()["order"]
    with mk_client() as client:
        for status in ("preparing", "ready"):
            response = client.patch(
                f"/api/v1/kitchen/orders/{order['id']}/status"
                f"?restaurant_id={restaurant_id}",
                json={"status": status},
                headers={"X-Kitchen-Dev-Key": KITCHEN_KEY},
            )
            assert response.status_code == 200, response.text
    return order


def _serve_burst(mk_client, order_id, restaurant_id, workers=WORKERS):
    """Fire `workers` simultaneous PATCH serve on one ready order."""
    barrier = threading.Barrier(workers)

    def _one(_i):
        with mk_client() as client:
            barrier.wait(timeout=30)
            return client.patch(
                f"/api/v1/staff/orders/{order_id}/serve"
                f"?restaurant_id={restaurant_id}",
                headers={"X-Staff-Dev-Key": STAFF_KEY},
            )

    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(_one, range(workers)))


def test_concurrent_serve_same_order_serialises(pg_client):
    order = _place_ready_order(
        pg_client["mk_client"],
        pg_client["token"],
        pg_client["menu"],
        pg_client["restaurant_id"],
        f"s-conc-{uuid.uuid4().hex}",
    )
    responses = _serve_burst(
        pg_client["mk_client"], order["id"], pg_client["restaurant_id"]
    )
    ok = [r for r in responses if r.status_code == 200]
    conflicts = [r for r in responses if r.status_code == 409]
    assert len(ok) == 1, [(r.status_code, r.text) for r in responses]
    assert len(conflicts) == WORKERS - 1
    assert all(r.json()["error"]["code"] == "conflict" for r in conflicts)
    assert ok[0].json()["data"]["status"] == "served"
    assert ok[0].json()["data"]["servedAt"]
    # Exactly one serve happened: version 1 -> 4, items served.
    session = pg_client["session_factory"]()
    try:
        row = session.get(Order, uuid.UUID(order["id"]))
        assert row.status == "served"
        assert row.version == 4
        assert row.served_at is not None
        assert all(i.status == "served" for i in row.items)
        # The session is still active — serving never closes it.
        assert row.table_session.status == "active"
    finally:
        session.close()
