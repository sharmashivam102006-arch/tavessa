"""Stage 9 — reliability races on REAL PostgreSQL (pgserver).

SQLite cannot prove serialisation; these tests need row locks.
One module-scoped cluster, per-request sessions, limiter disabled
only while this module's fixture is active (same pattern as
tests/test_billing_concurrency.py).

- Two staff acknowledging the same help call -> exactly one
  wins, the rest 409, final state acknowledged.
- Two owners voiding the same bill -> exactly one wins, the
  rest 409, final state voided.
- Eight simultaneous bill-freeze (request-payment) calls on a
  bill whose equal splits must be re-derived at the new tax
  rate -> all 200 (freeze is idempotent), splits sum exactly to
  the frozen total, and exactly ONE bill.splits_reconciled audit
  row (Stage 8 lock claim).

Already covered elsewhere (NOT duplicated here):
- two cooks, same order: test_kitchen_concurrency.py.
- two staff serving the same ready order:
  test_staff_concurrency.py.
- double payment confirmation (8 same-key -> one payment; 8
  different-key -> never overpay): test_billing_concurrency.py.
- concurrent order / refund / split races:
  test_orders_concurrency.py, test_admin_concurrency.py,
  test_split_concurrency.py.
"""
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest

pgserver = pytest.importorskip("pgserver")

from tests.pg_support import pg_cluster_dir, remove_pg_cluster_dir

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import QueuePool

from app.auth.models import AuditLog
from app.core.rate_limit import limiter
from app.core.security import hash_password
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models import Bill, DiningTable, HelpCall, Restaurant, User
from app.seed.zaika import RESTAURANT_SLUG, seed_zaika
from app.services.table_service import create_table_token
from tests.helpers import headers, menu_by_name, open_session

WORKERS = 8
DEV_STAFF = "dev-staff-only"
OWNER_EMAIL = "owner@zaika.example"
OWNER_PASSWORD = "Owner-pass-9"


def _pg_url(server) -> str:
    uri = server.get_uri()
    assert uri.startswith("postgresql://"), uri
    return uri.replace("postgresql://", "postgresql+psycopg://", 1)


@pytest.fixture(scope="module")
def pg_client():
    cluster_dir = pg_cluster_dir("pg9rel")
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
            owner = User(
                restaurant_id=restaurant.id,
                branch_id=None,
                email=OWNER_EMAIL,
                password_hash=hash_password(OWNER_PASSWORD),
                role="owner",
                is_active=True,
                must_change_password=False,
            )
            setup.add(owner)
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
            restaurant_id = str(restaurant.id)
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
            owner_h = {"Authorization": f"Bearer {login.json()['data']['accessToken']}"}
        yield {
            "mk_client": lambda: TestClient(app),
            "session_factory": factory,
            "tokens": tokens,
            "menus": menus,
            "restaurant_id": restaurant_id,
            "owner_h": owner_h,
        }
    finally:
        app.dependency_overrides.clear()
        limiter.enabled = was_enabled
        engine.dispose()
        server.cleanup()
        remove_pg_cluster_dir(cluster_dir)


def _place(pg_client, table_number, quantity=1, name="Butter Naan"):
    token = pg_client["tokens"][table_number]
    item = pg_client["menus"][table_number][name]
    with pg_client["mk_client"]() as client:
        response = client.post(
            "/api/v1/orders",
            json={"items": [{"menuItemId": item["id"], "quantity": quantity}]},
            headers={**headers(token), "Idempotency-Key": f"pg9-{uuid.uuid4().hex}"},
        )
    assert response.status_code == 201, response.text
    return response.json()["order"]


def _bill(pg_client, table_number):
    token = pg_client["tokens"][table_number]
    with pg_client["mk_client"]() as client:
        response = client.post("/api/v1/guest/bill", headers=headers(token))
    assert response.status_code == 200, response.text
    return response.json()["data"]


def _burst(mk_client, fn, workers=WORKERS):
    barrier = threading.Barrier(workers)

    def _one(i):
        with mk_client() as client:
            barrier.wait(timeout=30)
            return fn(client, i)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(_one, range(workers)))


def test_concurrent_acknowledge_same_help_call_serialises(pg_client):
    token = pg_client["tokens"][5]
    with pg_client["mk_client"]() as client:
        response = client.post(
            "/api/v1/guest/help-calls",
            json={"type": "waiter", "note": "race test"},
            headers=headers(token),
        )
    assert response.status_code == 201, response.text
    call_id = response.json()["data"]["id"]
    rid = pg_client["restaurant_id"]

    def _ack(client, _i):
        return client.patch(
            f"/api/v1/staff/help-calls/{call_id}/acknowledge?restaurant_id={rid}",
            headers={"X-Staff-Dev-Key": DEV_STAFF},
        )

    responses = _burst(pg_client["mk_client"], _ack)
    ok = [r for r in responses if r.status_code == 200]
    conflicts = [r for r in responses if r.status_code == 409]
    assert len(ok) == 1, [(r.status_code, r.text) for r in responses]
    assert len(conflicts) == WORKERS - 1
    assert all(r.json()["error"]["code"] == "conflict" for r in conflicts)
    session = pg_client["session_factory"]()
    try:
        row = session.get(HelpCall, uuid.UUID(call_id))
        assert row.status == "acknowledged"
        assert row.acknowledged_at is not None
    finally:
        session.close()


def test_concurrent_void_same_bill_serialises(pg_client):
    _place(pg_client, 6)
    bill = _bill(pg_client, 6)
    owner_h = pg_client["owner_h"]

    def _void(client, _i):
        return client.post(
            f"/api/v1/admin/bills/{bill['id']}/void",
            json={"reason": "race test"},
            headers=owner_h,
        )

    responses = _burst(pg_client["mk_client"], _void)
    ok = [r for r in responses if r.status_code == 200]
    conflicts = [r for r in responses if r.status_code == 409]
    assert len(ok) == 1, [(r.status_code, r.text) for r in responses]
    assert len(conflicts) == WORKERS - 1
    session = pg_client["session_factory"]()
    try:
        row = session.get(Bill, uuid.UUID(bill["id"]))
        assert row.status == "voided"
        assert row.void_reason == "race test"
    finally:
        session.close()


def test_concurrent_freeze_reconciles_splits_once(pg_client):
    # Fresh table-5 session state: a new order + bill on table 5
    # (table 6's bill is voided above; table 5 is still clean).
    _place(pg_client, 5, quantity=2)
    bill = _bill(pg_client, 5)
    token = pg_client["tokens"][5]
    with pg_client["mk_client"]() as client:
        split = client.post(
            "/api/v1/guest/bill/splits",
            json={"mode": "equal", "parts": 2},
            headers=headers(token),
        )
    assert split.status_code == 200, split.text
    # 5% tax configured AFTER the splits: the freeze must
    # re-derive them to the new total, exactly once.
    with pg_client["mk_client"]() as client:
        tax = client.post(
            "/api/v1/admin/settings/tax",
            json={"enabled": True, "rateBps": 500, "mode": "exclusive",
                  "label": "GST",
                  "effectiveFrom": datetime.now(timezone.utc).isoformat()},
            headers=pg_client["owner_h"],
        )
    assert tax.status_code == 201, tax.text

    def _freeze(client, _i):
        return client.patch(
            "/api/v1/guest/bill/request-payment", headers=headers(token))

    responses = _burst(pg_client["mk_client"], _freeze)
    assert all(r.status_code == 200 for r in responses), [
        (r.status_code, r.text) for r in responses]
    totals = {r.json()["data"]["totalPaise"] for r in responses}
    assert len(totals) == 1
    session = pg_client["session_factory"]()
    try:
        row = session.get(Bill, uuid.UUID(bill["id"]))
        assert row.status == "payment_requested"
        assert row.tax_rate_bps_snapshot == 500
        assert sum(s.amount_paise for s in row.splits) == row.total_paise
        assert row.total_paise == row.subtotal_paise + row.tax_paise
        audit_count = session.execute(
            select(func.count()).select_from(AuditLog).where(
                AuditLog.action == "bill.splits_reconciled",
                AuditLog.entity_id == row.id,
            )
        ).scalar_one()
        assert audit_count == 1, f"expected exactly 1 reconciliation audit row, got {audit_count}"
    finally:
        session.close()
