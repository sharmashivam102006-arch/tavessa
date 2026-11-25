"""Concurrent split payments + reconciliation — Stage 8 (task A).

REAL PostgreSQL ONLY (ephemeral pgserver), same fixture approach
as tests/test_billing_concurrency.py: one module-scoped cluster,
per-request sessions, delta assertions, slowapi disabled only
while this module's fixture is active.

Proves what SQLite cannot: the bill row lock serialises
- 4 simultaneous payments of 4 different splits (all succeed,
  bill ends exactly paid, never over-collected),
- 2 simultaneous freeze requests (request-payment) over splits
  created under an older tax config (the freeze reconciles the
  equal splits exactly ONCE; the second request is a no-op),
- a freeze racing a split payment (exactly one reconciliation;
  final splits sum to the frozen total, totals never exceeded).

Tax configs are inserted directly as TaxConfiguration rows (this
module has no owner user for the admin API); a new row is a new
config, exactly as the admin endpoint creates them.
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

from app.auth.models import AuditLog, TaxConfiguration
from app.core.rate_limit import limiter
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models import Bill, DiningTable, Payment, Restaurant
from app.seed.zaika import RESTAURANT_SLUG, seed_zaika
from app.services.table_service import create_table_token
from tests.helpers import headers, menu_by_name, open_session

DEV_KEY = "dev-staff-only"


def _pg_url(server) -> str:
    uri = server.get_uri()
    assert uri.startswith("postgresql://"), uri
    return uri.replace("postgresql://", "postgresql+psycopg://", 1)


@pytest.fixture(scope="module")
def pg_client():
    cluster_dir = pg_cluster_dir("pg-split")
    server = pgserver.get_server(str(cluster_dir))
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
        remove_pg_cluster_dir(cluster_dir)


def _restaurant_id(pg_client):
    session = pg_client["session_factory"]()
    try:
        return session.execute(
            select(Restaurant).where(Restaurant.slug == RESTAURANT_SLUG)
        ).scalars().first().id
    finally:
        session.close()


def _set_tax(pg_client, rate_bps, mode="exclusive", enabled=True):
    session = pg_client["session_factory"]()
    try:
        session.add(TaxConfiguration(
            restaurant_id=_restaurant_id(pg_client),
            branch_id=None,
            enabled=enabled,
            rate_bps=rate_bps,
            mode=mode,
            label="GST",
            effective_from=datetime.now(timezone.utc),
        ))
        session.commit()
    finally:
        session.close()


def _place_and_bill(pg_client, table_number=5, quantity=1):
    token = pg_client["tokens"][table_number]
    menu = pg_client["menus"][table_number]
    item = menu["Butter Naan"]
    with pg_client["mk_client"]() as client:
        response = client.post(
            "/api/v1/orders",
            json={"items": [{"menuItemId": item["id"], "quantity": quantity}]},
            headers={**headers(token), "Idempotency-Key": f"pg-s-{uuid.uuid4().hex}"},
        )
        assert response.status_code == 201, response.text
        bill = client.post("/api/v1/guest/bill", headers=headers(token))
        assert bill.status_code == 200, bill.text
        return token, bill.json()["data"]


def _create_splits(pg_client, token, body):
    with pg_client["mk_client"]() as client:
        response = client.post(
            "/api/v1/guest/bill/splits", json=body, headers=headers(token)
        )
        assert response.status_code == 200, response.text
        return response.json()["data"]


def _request_payment(pg_client, token):
    with pg_client["mk_client"]() as client:
        return client.patch(
            "/api/v1/guest/bill/request-payment", headers=headers(token)
        )


def _pay_split(pg_client, bill_id, amount, split_id):
    with pg_client["mk_client"]() as client:
        return client.post(
            f"/api/v1/staff/bills/{bill_id}/payments"
            f"?restaurant_id={_restaurant_id(pg_client)}",
            json={"amountPaise": amount, "billSplitId": split_id},
            headers={
                "X-Staff-Dev-Key": DEV_KEY,
                "Idempotency-Key": f"pg-sp-{uuid.uuid4().hex}",
            },
        )


def _bill_row(pg_client, bill_id):
    from sqlalchemy.orm import selectinload

    session = pg_client["session_factory"]()
    try:
        return session.execute(
            select(Bill)
            .options(selectinload(Bill.splits))
            .where(Bill.id == uuid.UUID(bill_id))
        ).scalars().first()
    finally:
        session.close()


def _audit_count(pg_client, bill_id, action):
    session = pg_client["session_factory"]()
    try:
        return session.execute(
            select(func.count()).select_from(AuditLog).where(
                AuditLog.entity_id == uuid.UUID(bill_id),
                AuditLog.action == action,
            )
        ).scalar_one()
    finally:
        session.close()


def test_concurrent_split_payments_serialise(pg_client):
    _set_tax(pg_client, 500, "exclusive")  # 5%
    token, bill = _place_and_bill(pg_client, table_number=5, quantity=4)
    frozen_bill = _create_splits(
        pg_client, token, {"mode": "equal", "parts": 4}
    )
    requested = _request_payment(pg_client, token)
    assert requested.status_code == 200, requested.text
    frozen = requested.json()["data"]
    total = frozen["totalPaise"]
    splits = frozen["splits"]
    assert len(splits) == 4
    assert sum(s["amountPaise"] for s in splits) == total

    barrier = threading.Barrier(4)

    def _one(split):
        barrier.wait(timeout=30)
        return _pay_split(
            pg_client, bill["id"], split["amountPaise"], split["id"]
        )

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(_one, splits))
    assert all(r.status_code == 201 for r in results), [
        (r.status_code, r.text) for r in results
    ]
    # Exactly 4 payments, summing to the frozen total — never over.
    session = pg_client["session_factory"]()
    try:
        payments = session.execute(
            select(Payment).where(Payment.bill_id == uuid.UUID(bill["id"]))
        ).scalars().all()
        assert len(payments) == 4
        assert sum(p.amount_paise for p in payments) == total
        bill_row = session.get(Bill, uuid.UUID(bill["id"]))
        assert bill_row.status == "paid"
        assert bill_row.amount_paid_paise == total
        assert all(s.status == "paid" for s in bill_row.splits)
    finally:
        session.close()


def test_concurrent_freeze_requests_reconcile_once(pg_client):
    _set_tax(pg_client, 500, "exclusive")  # 5%
    token, bill = _place_and_bill(pg_client, table_number=6, quantity=2)
    _create_splits(pg_client, token, {"mode": "equal", "parts": 2})
    # The tax config changes before the freeze: 5% -> 10%.
    _set_tax(pg_client, 1000, "exclusive")
    barrier = threading.Barrier(2)

    def _one(_i):
        barrier.wait(timeout=30)
        return _request_payment(pg_client, token)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(_one, range(2)))
    assert all(r.status_code == 200 for r in results), [
        (r.status_code, r.text) for r in results
    ]
    # Both callers see the same frozen bill; the reconciliation
    # (equal re-derivation) happened exactly once.
    bill_id = bill["id"]
    assert _audit_count(pg_client, bill_id, "bill.splits_reconciled") == 1
    row = _bill_row(pg_client, bill_id)
    assert row.status == "payment_requested"
    assert sum(s.amount_paise for s in row.splits) == row.total_paise
    assert row.tax_paise == row.subtotal_paise * 1000 // 10000


def test_freeze_racing_split_payment_reconciles_once(pg_client):
    _set_tax(pg_client, 500, "exclusive")  # 5%
    token, bill = _place_and_bill(pg_client, table_number=5, quantity=2)
    _create_splits(pg_client, token, {"mode": "equal", "parts": 2})
    # The tax config changes before the race: 5% -> 10%.
    _set_tax(pg_client, 1000, "exclusive")
    with pg_client["mk_client"]() as client:
        before = client.post(
            "/api/v1/guest/bill", headers=headers(token)
        ).json()["data"]
    split = before["splits"][0]
    barrier = threading.Barrier(2)
    results = {}

    def _freeze(_i):
        barrier.wait(timeout=30)
        results["freeze"] = _request_payment(pg_client, token)

    def _pay(_i):
        barrier.wait(timeout=30)
        results["pay"] = _pay_split(
            pg_client, bill["id"], split["amountPaise"], split["id"]
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda f: f(0), [_freeze, _pay]))
    # The split payment carries the amount computed against the
    # 5% live total, but the freeze re-derives the equal splits to
    # the 10% total first — whichever operation wins the bill row
    # lock, the loser re-reads the reconciled split. The payment
    # succeeds against the reconciled split as a partial payment
    # of it (its amount is below the new split amount, never above
    # what is due), and the bill ends consistent either way.
    assert results["freeze"].status_code == 200, results["freeze"].text
    assert results["pay"].status_code == 201, results["pay"].text
    row = _bill_row(pg_client, bill["id"])
    assert row.status == "payment_requested"
    assert sum(s.amount_paise for s in row.splits) == row.total_paise
    assert _audit_count(pg_client, bill["id"], "bill.splits_reconciled") == 1
    session = pg_client["session_factory"]()
    try:
        payments = session.execute(
            select(Payment).where(Payment.bill_id == uuid.UUID(bill["id"]))
        ).scalars().all()
        # At most one payment, never more than the frozen total.
        assert sum(p.amount_paise for p in payments) <= row.total_paise
    finally:
        session.close()
