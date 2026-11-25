"""Concurrent cash refunds — Stage 7.

REAL PostgreSQL ONLY (ephemeral pgserver), same fixture approach
as tests/test_billing_concurrency.py: one module-scoped cluster,
per-request sessions, and the slowapi limiter disabled while this
module's fixture is active.

Proves what SQLite cannot: the bill row lock + refundable-amount
guard + claim serialise concurrent refunds, so 8 simultaneous
refunds against one payment never refund more than the payment
amount (the losers get 409/422, the refunded total is exactly the
payment amount). Also proves same-key concurrent refunds create
exactly one refund row (7 replays).
"""
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest

pgserver = pytest.importorskip("pgserver")

from tests.pg_support import pg_cluster_dir, remove_pg_cluster_dir

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import QueuePool

from app.core.rate_limit import limiter
from app.core.security import hash_password
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models import Bill, DiningTable, Payment, Refund, Restaurant, User
from app.seed.zaika import RESTAURANT_SLUG, seed_zaika
from app.services.table_service import create_table_token
from tests.helpers import headers, menu_by_name, open_session

WORKERS = 8
DEV_KEY = "dev-staff-only"
OWNER_EMAIL = "owner@zaika.example"
OWNER_PASSWORD = "OwnerPass123"


def _pg_url(server) -> str:
    uri = server.get_uri()
    assert uri.startswith("postgresql://"), uri
    return uri.replace("postgresql://", "postgresql+psycopg://", 1)


@pytest.fixture(scope="module")
def pg_client():
    cluster_dir = pg_cluster_dir("pg7")
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
            restaurant = setup.execute(
                select(Restaurant).where(Restaurant.slug == RESTAURANT_SLUG)
            ).scalars().first()
            setup.add(User(
                restaurant_id=restaurant.id,
                email=OWNER_EMAIL,
                password_hash=hash_password(OWNER_PASSWORD),
                role="owner",
                is_active=True,
            ))
            tokens = {}
            for number in (5, 6):
                table = setup.execute(
                    select(DiningTable).where(
                        DiningTable.restaurant_id == restaurant.id,
                        DiningTable.number == number,
                    )
                ).scalars().first()
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
            login = client.post(
                "/api/v1/auth/login",
                json={"email": OWNER_EMAIL, "password": OWNER_PASSWORD},
            )
            assert login.status_code == 200, login.text
            owner_headers = {
                "Authorization": f"Bearer {login.json()['data']['accessToken']}"
            }
        yield {
            "mk_client": lambda: TestClient(app),
            "session_factory": factory,
            "tokens": tokens,
            "menus": menus,
            "owner_headers": owner_headers,
        }
    finally:
        app.dependency_overrides.clear()
        limiter.enabled = was_enabled
        engine.dispose()
        server.cleanup()
        remove_pg_cluster_dir(cluster_dir)


def _place_and_pay(pg_client, table_number=5, quantity=4):
    token = pg_client["tokens"][table_number]
    menu = pg_client["menus"][table_number]
    item = menu["Butter Naan"]
    with pg_client["mk_client"]() as client:
        response = client.post(
            "/api/v1/orders",
            json={"items": [{"menuItemId": item["id"], "quantity": quantity}]},
            headers={**headers(token), "Idempotency-Key": f"pg-r-{uuid.uuid4().hex}"},
        )
        assert response.status_code == 201, response.text
        bill = client.post("/api/v1/guest/bill", headers=headers(token))
        assert bill.status_code == 200, bill.text
        bill = bill.json()["data"]
        session = pg_client["session_factory"]()
        try:
            restaurant_id = str(session.execute(
                select(Restaurant).where(Restaurant.slug == RESTAURANT_SLUG)
            ).scalars().first().id)
        finally:
            session.close()
        pay = client.post(
            f"/api/v1/staff/bills/{bill['id']}/payments?restaurant_id={restaurant_id}",
            json={"amountPaise": bill["totalPaise"]},
            headers={"X-Staff-Dev-Key": DEV_KEY, "Idempotency-Key": f"pg-rp-{uuid.uuid4().hex}"},
        )
        assert pay.status_code == 201, pay.text
        return bill, pay.json()["data"]["payment"]


def _refund_burst(mk_client, owner_headers, bill_id, payment_id, amount, keys, workers=WORKERS):
    barrier = threading.Barrier(workers)

    def _one(i):
        with mk_client() as client:
            barrier.wait(timeout=30)
            return client.post(
                f"/api/v1/admin/bills/{bill_id}/refunds",
                json={"paymentId": payment_id, "amountPaise": amount, "reason": "burst"},
                headers={**owner_headers, "Idempotency-Key": keys[i]},
            )

    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(_one, range(workers)))


def _refunds_for_bill(pg_client, bill_id):
    session = pg_client["session_factory"]()
    try:
        return session.execute(
            select(Refund).where(Refund.bill_id == uuid.UUID(bill_id))
        ).scalars().all()
    finally:
        session.close()


def test_concurrent_same_key_refund_creates_exactly_one(pg_client):
    bill, payment = _place_and_pay(pg_client, table_number=5)
    key = f"pg-rf-{uuid.uuid4().hex}"
    responses = _refund_burst(
        pg_client["mk_client"], pg_client["owner_headers"], bill["id"],
        payment["id"], bill["totalPaise"], [key] * WORKERS,
    )
    assert all(r.status_code == 201 for r in responses), [
        (r.status_code, r.text) for r in responses
    ]
    refund_ids = {r.json()["data"]["id"] for r in responses}
    assert len(refund_ids) == 1
    replays = [r for r in responses if r.headers.get("Idempotency-Replayed") == "true"]
    assert len(replays) == WORKERS - 1
    assert len(_refunds_for_bill(pg_client, bill["id"])) == 1


def test_concurrent_refunds_never_exceed_payment(pg_client):
    bill, payment = _place_and_pay(pg_client, table_number=6)
    total = bill["totalPaise"]
    keys = [f"pg-rfx-{uuid.uuid4().hex}" for _ in range(WORKERS)]
    responses = _refund_burst(
        pg_client["mk_client"], pg_client["owner_headers"], bill["id"],
        payment["id"], total, keys,
    )
    successes = [r for r in responses if r.status_code == 201]
    failures = [r for r in responses if r.status_code in (409, 422)]
    assert len(successes) == 1, [(r.status_code, r.text) for r in responses]
    assert len(failures) == WORKERS - 1
    refunds = _refunds_for_bill(pg_client, bill["id"])
    assert len(refunds) == 1
    assert sum(r.amount_paise for r in refunds) == total
    session = pg_client["session_factory"]()
    try:
        assert session.get(Bill, uuid.UUID(bill["id"])).status == "refunded"
        assert session.get(Payment, uuid.UUID(payment["id"])).status == "refunded"
    finally:
        session.close()
