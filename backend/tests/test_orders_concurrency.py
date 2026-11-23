"""Concurrent idempotency — Stage 3.1.

A genuine concurrency test: 8 threads hit POST /orders at once with
the same Idempotency-Key and assert exactly one order is created.

REAL PostgreSQL ONLY (ephemeral pgserver). This module is skipped
where pgserver is unavailable (pytest.importorskip), but it MUST run
in CI/dev where pgserver is installed — SQLite cannot prove this:
its locking/transaction semantics differ from Postgres, and the
shared in-memory fixture session in tests/conftest.py is not
thread-safe, so this module builds its own engine + per-request
sessions instead of using the ``client`` / ``db_session`` fixtures.

Rate limiting: the in-process slowapi limiter is shared by the whole
pytest process (60 POST /orders per minute per IP), and the full
suite already makes dozens of order placements — the concurrent
bursts here could trip it for reasons unrelated to idempotency. The
limiter is therefore disabled ONLY while this module's fixture is
active (``limiter.enabled = False``) and restored afterwards. This
is a test-only bypass; the production limit is unchanged.
"""
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest

pgserver = pytest.importorskip("pgserver")

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import QueuePool

from app.core.rate_limit import limiter
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models import DiningTable, Order, OrderItem, Restaurant
from app.seed.zaika import RESTAURANT_SLUG, seed_zaika
from app.services.table_service import create_table_token
from tests.helpers import headers, menu_by_name, open_session

WORKERS = 8


def _pg_url(server) -> str:
    """pgserver returns a bare postgresql:// URI (psycopg2 default);
    psycopg2 is not installed, so normalise to the psycopg3 driver
    (the same normalisation as app.db.session.build_engine)."""
    uri = server.get_uri()
    assert uri.startswith("postgresql://"), uri
    return uri.replace("postgresql://", "postgresql+psycopg://", 1)


@pytest.fixture
def pg_client(tmp_path):
    """Ephemeral real Postgres + per-request sessions + TestClient.

    Yields {"mk_client": callable returning a fresh TestClient,
    "session_factory": sessionmaker, "engine": engine}.
    """
    server = pgserver.get_server(str(tmp_path / "pg"))
    url = _pg_url(server)
    engine = create_engine(
        url, poolclass=QueuePool, pool_size=10, max_overflow=5
    )
    # Prove this is really Postgres, not SQLite.
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
    # Test-only limiter bypass (see module docstring); restore after.
    was_enabled = limiter.enabled
    limiter.enabled = False
    try:
        # Seed + tokens + sessions in one setup session.
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
            tokens = {}
            for number in (5, 6):
                table = (
                    setup.execute(
                        select(DiningTable).where(
                            DiningTable.restaurant_id == restaurant.id,
                            DiningTable.number == number,
                        )
                    )
                    .scalars()
                    .first()
                )
                raw, _ = create_table_token(setup, table)
                tokens[number] = raw
            setup.commit()
        finally:
            setup.close()
        # Open a session per table over HTTP (exercises the real path).
        with TestClient(app) as client:
            menus = {}
            for number, token in tokens.items():
                open_session(client, token)
                menus[number] = menu_by_name(client, token)[0]
        yield {
            "mk_client": lambda: TestClient(app),
            "session_factory": factory,
            "engine": engine,
            "tokens": tokens,
            "menus": menus,
        }
    finally:
        app.dependency_overrides.clear()
        limiter.enabled = was_enabled
        engine.dispose()
        server.cleanup()


def _order_body(menu):
    """Two lines, so duplicate order-items would be visible."""
    butter = menu["Old Delhi Butter Chicken"]
    naan = menu["Butter Naan"]
    return {
        "items": [
            {"menuItemId": butter["id"], "quantity": 2},
            {"menuItemId": naan["id"], "quantity": 1},
        ]
    }


def _post_burst(mk_client, token, body, key, workers=WORKERS):
    """Fire `workers` simultaneous POST /orders with the same key.
    A barrier releases all threads at once so the requests genuinely
    overlap instead of serialising by start-up jitter."""
    barrier = threading.Barrier(workers)
    results = []

    def _one(_i):
        with mk_client() as client:
            barrier.wait(timeout=30)
            response = client.post(
                "/api/v1/orders",
                json=body,
                headers={**headers(token), "Idempotency-Key": key},
            )
            return response

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(_one, range(workers)))
    return results


def test_concurrent_same_key_creates_exactly_one_order(pg_client):
    key = f"conc-{uuid.uuid4().hex}"
    token = pg_client["tokens"][5]
    body = _order_body(pg_client["menus"][5])

    responses = _post_burst(pg_client["mk_client"], token, body, key)

    # Every request succeeds (the owner creates, the rest replay).
    assert all(r.status_code == 201 for r in responses), [
        (r.status_code, r.text) for r in responses
    ]
    # Exactly one order, one order number, one order id everywhere.
    session = pg_client["session_factory"]()
    try:
        orders = session.execute(select(Order)).scalars().all()
        assert len(orders) == 1
        assert len({o.order_number for o in orders}) == 1
        order = orders[0]
        assert order.table_session_id is not None
        item_rows = session.execute(
            select(func.count())
            .select_from(OrderItem)
            .where(OrderItem.order_id == order.id)
        ).scalar_one()
        assert item_rows == 2  # the two lines of the single request
    finally:
        session.close()
    order_ids = {r.json()["order"]["id"] for r in responses}
    assert order_ids == {str(order.id)}
    # Exactly one owner response (no replay header); the rest replayed.
    replayed = [r for r in responses if r.headers.get("Idempotency-Replayed") == "true"]
    owners = [r for r in responses if "Idempotency-Replayed" not in r.headers]
    assert len(owners) == 1
    assert len(replayed) == WORKERS - 1


def test_concurrent_same_key_different_body_422(pg_client):
    key = f"conc-diff-{uuid.uuid4().hex}"
    token = pg_client["tokens"][5]
    menu = pg_client["menus"][5]
    body = _order_body(menu)
    _post_burst(pg_client["mk_client"], token, body, key)

    chai = menu["Masala Chai"]
    with pg_client["mk_client"]() as client:
        response = client.post(
            "/api/v1/orders",
            json={"items": [{"menuItemId": chai["id"], "quantity": 1}]},
            headers={**headers(token), "Idempotency-Key": key},
        )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "idempotency_key_reused"


def test_same_key_in_other_session_is_independent(pg_client):
    """Scope isolation: the same key string in a different session is
    an independent claim and creates its own order (Stage 3.1 scope
    is (restaurant_id, table_session_id, endpoint, key))."""
    key = f"conc-scope-{uuid.uuid4().hex}"
    body5 = _order_body(pg_client["menus"][5])
    body6 = _order_body(pg_client["menus"][6])

    responses5 = _post_burst(
        pg_client["mk_client"], pg_client["tokens"][5], body5, key
    )
    responses6 = _post_burst(
        pg_client["mk_client"], pg_client["tokens"][6], body6, key
    )
    assert all(r.status_code == 201 for r in responses5 + responses6)
    id5 = {r.json()["order"]["id"] for r in responses5}
    id6 = {r.json()["order"]["id"] for r in responses6}
    assert len(id5) == 1 and len(id6) == 1 and id5 != id6

    session = pg_client["session_factory"]()
    try:
        orders = session.execute(select(Order)).scalars().all()
        assert len(orders) == 2  # one per session, no cross-session leak
        assert len({o.order_number for o in orders}) == 2
    finally:
        session.close()
