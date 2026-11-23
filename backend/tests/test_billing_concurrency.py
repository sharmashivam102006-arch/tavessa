"""Concurrent cash payments — Stage 6.

REAL PostgreSQL ONLY (ephemeral pgserver), same fixture approach
as tests/test_orders_concurrency.py: one module-scoped cluster
(each initdb is expensive on the 512M /tmp tmpfs), per-request
sessions, delta assertions, and the slowapi limiter disabled only
while this module's fixture is active.

Proves what SQLite cannot: the row lock + status guard + claim
serialise concurrent confirmations, so
- 8 simultaneous same-key confirmations create exactly ONE
  payment (7 replays), and
- 8 simultaneous different-key confirmations that together
  overpay the bill never push the total collected past the bill
  total (the losers get 409/422, the bill ends exactly paid).
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
from app.models import Bill, DiningTable, Payment, Restaurant
from app.seed.zaika import RESTAURANT_SLUG, seed_zaika
from app.services.table_service import create_table_token
from tests.helpers import headers, menu_by_name, open_session

WORKERS = 8
DEV_KEY = "dev-staff-only"


def _pg_url(server) -> str:
    uri = server.get_uri()
    assert uri.startswith("postgresql://"), uri
    return uri.replace("postgresql://", "postgresql+psycopg://", 1)


@pytest.fixture(scope="module")
def pg_client(tmp_path_factory):
    server = pgserver.get_server(str(tmp_path_factory.mktemp("pg6") / "pg"))
    url = _pg_url(server)
    engine = create_engine(
        url, poolclass=QueuePool, pool_size=10, max_overflow=5
    )
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
        with TestClient(app) as client:
            menus = {}
            for number, token in tokens.items():
                open_session(client, token)
                menus[number] = menu_by_name(client, token)[0]
        yield {
            "mk_client": lambda: TestClient(app),
            "session_factory": factory,
            "tokens": tokens,
            "menus": menus,
        }
    finally:
        app.dependency_overrides.clear()
        limiter.enabled = was_enabled
        engine.dispose()
        server.cleanup()


def _place_and_bill(pg_client, table_number=5, quantity=1):
    """Place one Butter Naan order (quantity N) on a table and bill
    it. Returns (token, bill dict). Uses a fresh idempotency key so
    each call is independent."""
    token = pg_client["tokens"][table_number]
    menu = pg_client["menus"][table_number]
    item = menu["Butter Naan"]
    with pg_client["mk_client"]() as client:
        response = client.post(
            "/api/v1/orders",
            json={"items": [{"menuItemId": item["id"], "quantity": quantity}]},
            headers={**headers(token), "Idempotency-Key": f"pg-b-{uuid.uuid4().hex}"},
        )
        assert response.status_code == 201, response.text
        bill = client.post("/api/v1/guest/bill", headers=headers(token))
        assert bill.status_code == 200, bill.text
        return token, bill.json()["data"]


def _restaurant_id(pg_client):
    session = pg_client["session_factory"]()
    try:
        return str(
            session.execute(
                select(Restaurant).where(Restaurant.slug == RESTAURANT_SLUG)
            )
            .scalars()
            .first()
            .id
        )
    finally:
        session.close()


def _pay_burst(mk_client, token, restaurant_id, bill_id, body, keys, workers=WORKERS):
    """Fire `workers` simultaneous POST payments. `keys` is a list
    of Idempotency-Key values (same key for all, or one per worker)."""
    barrier = threading.Barrier(workers)
    results = []

    def _one(i):
        with mk_client() as client:
            barrier.wait(timeout=30)
            return client.post(
                f"/api/v1/staff/bills/{bill_id}/payments?restaurant_id={restaurant_id}",
                json=body,
                headers={
                    "X-Staff-Dev-Key": DEV_KEY,
                    "Idempotency-Key": keys[i],
                },
            )

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(_one, range(workers)))
    return results


def _payments_for_bill(pg_client, bill_id):
    session = pg_client["session_factory"]()
    try:
        return session.execute(
            select(Payment).where(Payment.bill_id == uuid.UUID(bill_id))
        ).scalars().all()
    finally:
        session.close()


def test_concurrent_same_key_creates_exactly_one_payment(pg_client):
    token, bill = _place_and_bill(pg_client, table_number=5)
    key = f"pg-conc-{uuid.uuid4().hex}"
    body = {"amountPaise": bill["totalPaise"]}
    responses = _pay_burst(
        pg_client["mk_client"], token, _restaurant_id(pg_client),
        bill["id"], body, [key] * WORKERS,
    )
    assert all(r.status_code == 201 for r in responses), [
        (r.status_code, r.text) for r in responses
    ]
    payment_ids = {r.json()["data"]["payment"]["id"] for r in responses}
    assert len(payment_ids) == 1
    replays = [r for r in responses if r.headers.get("Idempotency-Replayed") == "true"]
    assert len(replays) == WORKERS - 1
    assert len(_payments_for_bill(pg_client, bill["id"])) == 1
    session = pg_client["session_factory"]()
    try:
        row = session.get(Bill, uuid.UUID(bill["id"]))
        assert row.status == "paid"
        assert row.amount_paid_paise == bill["totalPaise"]
    finally:
        session.close()


def test_concurrent_different_keys_never_overpay(pg_client):
    # A bigger bill (4 naans) so several full-amount attempts can
    # race: only the first can win; the rest must fail, and the
    # collected total must never exceed the bill total.
    _token, bill = _place_and_bill(pg_client, table_number=6, quantity=4)
    total = bill["totalPaise"]
    keys = [f"pg-over-{uuid.uuid4().hex}" for _ in range(WORKERS)]
    body = {"amountPaise": total}
    responses = _pay_burst(
        pg_client["mk_client"], _token, _restaurant_id(pg_client),
        bill["id"], body, keys,
    )
    successes = [r for r in responses if r.status_code == 201]
    failures = [r for r in responses if r.status_code in (409, 422)]
    assert len(successes) == 1, [(r.status_code, r.text) for r in responses]
    assert len(failures) == WORKERS - 1
    payments = _payments_for_bill(pg_client, bill["id"])
    assert len(payments) == 1
    assert sum(p.amount_paise for p in payments) == total
    session = pg_client["session_factory"]()
    try:
        row = session.get(Bill, uuid.UUID(bill["id"]))
        assert row.status == "paid"
        assert row.amount_paid_paise == total
    finally:
        session.close()


def test_concurrent_partial_payments_sum_to_total(pg_client):
    # 8 workers each pay 1/8 of a large bill with different keys:
    # all can succeed, and the collected sum must be exactly the
    # total (never more, never a lost update on amount_paid).
    _token, bill = _place_and_bill(pg_client, table_number=5, quantity=8)
    total = bill["totalPaise"]
    assert total % WORKERS == 0, "test bill must divide evenly"
    part = total // WORKERS
    keys = [f"pg-part-{uuid.uuid4().hex}" for _ in range(WORKERS)]
    body = {"amountPaise": part}
    responses = _pay_burst(
        pg_client["mk_client"], _token, _restaurant_id(pg_client),
        bill["id"], body, keys,
    )
    assert all(r.status_code == 201 for r in responses), [
        (r.status_code, r.text) for r in responses
    ]
    payments = _payments_for_bill(pg_client, bill["id"])
    assert len(payments) == WORKERS
    assert sum(p.amount_paise for p in payments) == total
    session = pg_client["session_factory"]()
    try:
        row = session.get(Bill, uuid.UUID(bill["id"]))
        assert row.status == "paid"
        assert row.amount_paid_paise == total
    finally:
        session.close()
