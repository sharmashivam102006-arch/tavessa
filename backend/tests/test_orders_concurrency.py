"""Concurrent idempotency — Stages 3.1 and 3.2.

Stage 3.1: a genuine concurrency test — 8 threads hit POST /orders
at once with the same Idempotency-Key and assert exactly one order
is created. Stage 3.2: crash-recovery tests — rollback leaves no
claim, abandoned (Stage 3.1) claims recover, a delayed owner blocks
on PostgreSQL's speculative insert until its transaction commits or
aborts, and the lock timeout bounds the wait.

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
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

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
from app.models import DiningTable, IdempotencyKey, Order, OrderItem, Restaurant, TableSession
from app.schemas.guest import OrderPlacementRequest
from app.seed.zaika import RESTAURANT_SLUG, seed_zaika
from app.services import order_service
from app.services.order_service import ORDER_ENDPOINT, _canonical_hash
from app.services.table_service import _now, create_table_token
from tests.helpers import headers, menu_by_name, open_session

WORKERS = 8


def _pg_url(server) -> str:
    """pgserver returns a bare postgresql:// URI (psycopg2 default);
    psycopg2 is not installed, so normalise to the psycopg3 driver
    (the same normalisation as app.db.session.build_engine)."""
    uri = server.get_uri()
    assert uri.startswith("postgresql://"), uri
    return uri.replace("postgresql://", "postgresql+psycopg://", 1)


@pytest.fixture(scope="module")
def pg_client(tmp_path_factory):
    """Ephemeral real Postgres + per-request sessions + TestClient.

    Module-scoped ON PURPOSE: each pgserver cluster costs a full
    initdb on the 512M /tmp tmpfs, and a fresh cluster per test made
    the suite flaky there (initdb failing mid-bootstrap under disk
    pressure). One cluster serves all the tests in this module;
    tests stay independent via unique idempotency keys and delta
    assertions (order counts before/after each burst), never
    absolute totals.

    Yields {"mk_client": callable returning a fresh TestClient,
    "session_factory": sessionmaker, "engine": engine}.
    """
    server = pgserver.get_server(str(tmp_path_factory.mktemp("pg31") / "pg"))
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

    session = pg_client["session_factory"]()
    try:
        orders_before = session.execute(
            select(func.count()).select_from(Order)
        ).scalar_one()
    finally:
        session.close()

    responses = _post_burst(pg_client["mk_client"], token, body, key)

    # Every request succeeds (the owner creates, the rest replay).
    assert all(r.status_code == 201 for r in responses), [
        (r.status_code, r.text) for r in responses
    ]
    # Exactly one NEW order, one order number, one order id everywhere.
    # (Delta, not absolute total: the module-scoped DB is shared.)
    order_ids = {r.json()["order"]["id"] for r in responses}
    assert len(order_ids) == 1
    order_id = uuid.UUID(next(iter(order_ids)))
    session = pg_client["session_factory"]()
    try:
        orders_total = session.execute(
            select(func.count()).select_from(Order)
        ).scalar_one()
        assert orders_total == orders_before + 1
        order = session.get(Order, order_id)
        assert order is not None
        item_rows = session.execute(
            select(func.count())
            .select_from(OrderItem)
            .where(OrderItem.order_id == order.id)
        ).scalar_one()
        assert item_rows == 2  # the two lines of the single request
    finally:
        session.close()
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

    session = pg_client["session_factory"]()
    try:
        orders_before = session.execute(
            select(func.count()).select_from(Order)
        ).scalar_one()
    finally:
        session.close()

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
        orders_total = session.execute(
            select(func.count()).select_from(Order)
        ).scalar_one()
        # Delta, not absolute total: the module-scoped DB is shared.
        assert orders_total == orders_before + 2  # one per session, no cross-session leak
        numbers = session.execute(
            select(Order.order_number).where(
                Order.id.in_([uuid.UUID(i) for i in id5 | id6])
            )
        ).scalars().all()
        assert len(set(numbers)) == 2
    finally:
        session.close()


# ----------------------------------------------------------------------
# Stage 3.2 — crash recovery
# ----------------------------------------------------------------------


def _scope_ids(factory):
    """(restaurant_id, {table_number: active session_id}) for the
    seeded module DB — the scope a legacy claim row must carry."""
    session = factory()
    try:
        restaurant = (
            session.execute(
                select(Restaurant).where(Restaurant.slug == RESTAURANT_SLUG)
            )
            .scalars()
            .first()
        )
        session_ids = {}
        for table in session.execute(
            select(DiningTable).where(
                DiningTable.restaurant_id == restaurant.id
            )
        ).scalars():
            row = session.execute(
                select(TableSession).where(
                    TableSession.table_id == table.id,
                    TableSession.status == "active",
                )
            ).scalars().first()
            if row is not None:
                session_ids[table.number] = row.id
        return restaurant.id, session_ids
    finally:
        session.close()


def _body_hash(body):
    """The canonical hash place_order computes for this body — via
    the same schema + helper the service uses, so the test cannot
    drift from the production hash."""
    req = OrderPlacementRequest(**body)
    return _canonical_hash(req.model_dump(by_alias=True, mode="json"))


def _order_count(factory):
    session = factory()
    try:
        return session.execute(
            select(func.count()).select_from(Order)
        ).scalar_one()
    finally:
        session.close()


def _claim_state(factory, key):
    """Current state of the claim row for `key`, if any (fresh
    session; plain values, not detached ORM objects)."""
    session = factory()
    try:
        row = session.execute(
            select(IdempotencyKey).where(IdempotencyKey.key == key)
        ).scalars().first()
        if row is None:
            return None
        return {
            "response_body": row.response_body,
            "order_id": row.order_id,
            "request_hash": row.request_hash,
        }
    finally:
        session.close()


def _legacy_claim(factory, restaurant_id, session_id, key, request_hash,
                  order_id=None, age_seconds=120):
    """Insert a Stage 3.1-style COMMITTED pending claim (response
    NULL) — simulating a claim orphaned by a process crash under the
    old three-transaction design — and commit it."""
    session = factory()
    try:
        now = _now()
        row = IdempotencyKey(
            restaurant_id=restaurant_id,
            table_session_id=session_id,
            endpoint=ORDER_ENDPOINT,
            key=key,
            request_hash=request_hash,
            response_status=None,
            response_body=None,
            order_id=order_id,
            expires_at=now + timedelta(hours=24),
            created_at=now - timedelta(seconds=age_seconds),
        )
        session.add(row)
        session.commit()
        return row.id
    finally:
        session.close()


def _post_one(mk_client, token, body, key):
    with mk_client() as client:
        return client.post(
            "/api/v1/orders",
            json=body,
            headers={**headers(token), "Idempotency-Key": key},
        )


def test_failed_placement_does_not_burn_key(pg_client):
    """Stage 3.2 test 4: a failed placement leaves NO claim row, so
    the same key can be reused — first with the same invalid body
    (422 validation again, not idempotency_key_reused), then with a
    corrected body (201, exactly one order)."""
    key = f"conc-rollback-{uuid.uuid4().hex}"
    token = pg_client["tokens"][5]
    menu = pg_client["menus"][5]
    factory = pg_client["session_factory"]
    invalid = {"items": [{"menuItemId": menu["Hara Bhara Kebab"]["id"], "quantity": 1}]}
    valid = _order_body(menu)

    orders_before = _order_count(factory)
    assert _claim_state(factory, key) is None

    response = _post_one(pg_client["mk_client"], token, invalid, key)
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "validation_failed"
    assert _claim_state(factory, key) is None
    assert _order_count(factory) == orders_before

    # Same invalid body again: still a validation 422, not a reuse
    # 422 — the failed attempt burned nothing.
    response = _post_one(pg_client["mk_client"], token, invalid, key)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_failed"
    assert _claim_state(factory, key) is None

    response = _post_one(pg_client["mk_client"], token, valid, key)
    assert response.status_code == 201, response.text
    assert "Idempotency-Replayed" not in response.headers
    assert _order_count(factory) == orders_before + 1
    claim = _claim_state(factory, key)
    assert claim is not None and claim["response_body"] is not None
    assert str(claim["order_id"]) == response.json()["order"]["id"]


def test_abandoned_claim_without_order_recovers(pg_client):
    """Stage 3.2 test 5a: a legacy pending claim (no order, no
    response, > 60 s old) is reclaimed — the POST succeeds as owner
    with exactly one new order, and the claim is filled in."""
    key = f"conc-abandoned-{uuid.uuid4().hex}"
    token = pg_client["tokens"][5]
    menu = pg_client["menus"][5]
    factory = pg_client["session_factory"]
    body = _order_body(menu)

    restaurant_id, session_ids = _scope_ids(factory)
    _legacy_claim(factory, restaurant_id, session_ids[5], key, _body_hash(body))

    orders_before = _order_count(factory)
    response = _post_one(pg_client["mk_client"], token, body, key)
    assert response.status_code == 201, response.text
    assert "Idempotency-Replayed" not in response.headers
    assert _order_count(factory) == orders_before + 1
    claim = _claim_state(factory, key)
    assert claim is not None and claim["response_body"] is not None
    assert str(claim["order_id"]) == response.json()["order"]["id"]


def test_abandoned_claim_with_order_replays(pg_client):
    """Stage 3.2 test 5b: a legacy pending claim pointing at an
    order that DOES exist (response never filled) replays that order
    verbatim — no second order is created."""
    token = pg_client["tokens"][5]
    menu = pg_client["menus"][5]
    factory = pg_client["session_factory"]
    body = _order_body(menu)

    placed = _post_one(
        pg_client["mk_client"], token, body, f"conc-5b-owner-{uuid.uuid4().hex}"
    )
    assert placed.status_code == 201, placed.text
    order_id = placed.json()["order"]["id"]

    key = f"conc-abandoned-order-{uuid.uuid4().hex}"
    restaurant_id, session_ids = _scope_ids(factory)
    _legacy_claim(
        factory, restaurant_id, session_ids[5], key, _body_hash(body),
        order_id=uuid.UUID(order_id),
    )

    orders_before = _order_count(factory)
    response = _post_one(pg_client["mk_client"], token, body, key)
    assert response.status_code == 201, response.text
    assert response.headers.get("Idempotency-Replayed") == "true"
    assert response.json()["order"]["id"] == order_id
    assert response.json() == placed.json()
    assert _order_count(factory) == orders_before


def test_two_competing_recoveries_create_one_order(pg_client):
    """Stage 3.2 test 6: ONE stale legacy claim, TWO simultaneous
    recovery POSTs — exactly one order, one owner + one replay, the
    same order id in both responses."""
    key = f"conc-recover-{uuid.uuid4().hex}"
    token = pg_client["tokens"][5]
    menu = pg_client["menus"][5]
    factory = pg_client["session_factory"]
    body = _order_body(menu)

    restaurant_id, session_ids = _scope_ids(factory)
    _legacy_claim(factory, restaurant_id, session_ids[5], key, _body_hash(body))

    orders_before = _order_count(factory)
    responses = _post_burst(pg_client["mk_client"], token, body, key, workers=2)
    assert all(r.status_code == 201 for r in responses), [
        (r.status_code, r.text) for r in responses
    ]
    assert _order_count(factory) == orders_before + 1
    order_ids = {r.json()["order"]["id"] for r in responses}
    assert len(order_ids) == 1
    owners = [r for r in responses if "Idempotency-Replayed" not in r.headers]
    replayed = [r for r in responses if r.headers.get("Idempotency-Replayed") == "true"]
    assert len(owners) == 1 and len(replayed) == 1


def test_delayed_owner_blocks_until_rollback(pg_client):
    """Stage 3.2 test 7 (the core PostgreSQL semantics test): a claim
    row held in an UNCOMMITTED transaction — simulating a slow or
    crashed owner — makes a competing POST genuinely block on the
    unique-index speculative insert (no order appears, no response
    returns) until that transaction aborts; the aborted owner's
    crash is then equivalent to a rollback, and the waiter proceeds
    as owner with exactly one order."""
    key = f"conc-delay-{uuid.uuid4().hex}"
    token = pg_client["tokens"][5]
    menu = pg_client["menus"][5]
    factory = pg_client["session_factory"]
    body = _order_body(menu)

    restaurant_id, session_ids = _scope_ids(factory)
    orders_before = _order_count(factory)

    holder = factory()
    try:
        holder.add(IdempotencyKey(
            restaurant_id=restaurant_id,
            table_session_id=session_ids[5],
            endpoint=ORDER_ENDPOINT,
            key=key,
            request_hash=_body_hash(body),
            response_status=None,
            response_body=None,
            order_id=None,
            expires_at=_now() + timedelta(hours=24),
        ))
        holder.flush()  # uncommitted — holds the speculative-insert wait open
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(
                _post_one, pg_client["mk_client"], token, body, key
            )
            time.sleep(2)
            assert not future.done(), (
                "POST returned while the owner transaction was still open — "
                "it should block on PostgreSQL, not race or poll"
            )
            assert _order_count(factory) == orders_before
            holder.rollback()  # simulated process crash: abort the owner
            response = future.result(timeout=15)
        assert response.status_code == 201, response.text
        assert "Idempotency-Replayed" not in response.headers
        assert _order_count(factory) == orders_before + 1
    finally:
        holder.close()


def test_lock_timeout_returns_409(pg_client, monkeypatch):
    """Stage 3.2 test 7b: the SET LOCAL lock_timeout bounds the
    speculative-insert wait — with the timeout patched to 200 ms, a
    POST blocked behind a held claim transaction gets 409
    idempotency_in_progress promptly instead of waiting. If this
    ever fails, the SET LOCAL is unverified and must be removed
    (see order_service._set_lock_timeout)."""
    monkeypatch.setattr(order_service, "IDEMPOTENCY_LOCK_TIMEOUT_SECONDS", 0.2)
    key = f"conc-timeout-{uuid.uuid4().hex}"
    token = pg_client["tokens"][5]
    menu = pg_client["menus"][5]
    factory = pg_client["session_factory"]
    body = _order_body(menu)

    restaurant_id, session_ids = _scope_ids(factory)
    holder = factory()
    try:
        holder.add(IdempotencyKey(
            restaurant_id=restaurant_id,
            table_session_id=session_ids[5],
            endpoint=ORDER_ENDPOINT,
            key=key,
            request_hash=_body_hash(body),
            response_status=None,
            response_body=None,
            order_id=None,
            expires_at=_now() + timedelta(hours=24),
        ))
        holder.flush()  # uncommitted — holds the wait open
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(
                _post_one, pg_client["mk_client"], token, body, key
            )
            response = future.result(timeout=10)
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "idempotency_in_progress"
        assert _claim_state(factory, key) is None  # nothing committed
    finally:
        holder.rollback()
        holder.close()
