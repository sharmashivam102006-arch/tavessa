"""Stage 9 — golden path on REAL PostgreSQL (pgserver).

Guest -> kitchen -> staff -> bill -> cash -> receipt, with a
real tax freeze + Stage 8 split reconciliation exercised
end-to-end through the HTTP API. One test, full path, asserting
the bill/receipt numbers arithmetically from the menu prices
(nothing hardcoded except the 5% rate).

Fixture pattern copied from tests/test_billing_concurrency.py:
one module-scoped cluster, per-request sessions, limiter
disabled only while this module's fixture is active.
"""
import uuid
from datetime import datetime, timezone

import pytest

pgserver = pytest.importorskip("pgserver")

from tests.pg_support import pg_cluster_dir, remove_pg_cluster_dir

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import QueuePool

from app.core.rate_limit import limiter
from app.core.security import hash_password
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models import DiningTable, Restaurant, User
from app.seed.zaika import RESTAURANT_SLUG, seed_zaika
from app.services.table_service import create_table_token
from tests.helpers import headers, menu_by_name, open_session

DEV_STAFF = "dev-staff-only"
DEV_KITCHEN = "dev-kitchen-only"
OWNER_EMAIL = "owner@zaika.example"
OWNER_PASSWORD = "Owner-pass-9"


def _pg_url(server) -> str:
    uri = server.get_uri()
    assert uri.startswith("postgresql://"), uri
    return uri.replace("postgresql://", "postgresql+psycopg://", 1)


@pytest.fixture(scope="module")
def pg_client():
    cluster_dir = pg_cluster_dir("pg9gold")
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
            setup.add(User(
                restaurant_id=restaurant.id,
                branch_id=None,
                email=OWNER_EMAIL,
                password_hash=hash_password(OWNER_PASSWORD),
                role="owner",
                is_active=True,
                must_change_password=False,
            ))
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
            restaurant_id = str(restaurant.id)
        finally:
            setup.close()
        with TestClient(app) as client:
            open_session(client, raw)
            menu = menu_by_name(client, raw)[0]
            login = client.post(
                "/api/v1/auth/login",
                json={"email": OWNER_EMAIL, "password": OWNER_PASSWORD},
            )
            assert login.status_code == 200, login.text
            owner_h = {"Authorization": f"Bearer {login.json()['data']['accessToken']}"}
        yield {
            "mk_client": lambda: TestClient(app),
            "token": raw,
            "menu": menu,
            "restaurant_id": restaurant_id,
            "owner_h": owner_h,
        }
    finally:
        app.dependency_overrides.clear()
        limiter.enabled = was_enabled
        engine.dispose()
        server.cleanup()
        remove_pg_cluster_dir(cluster_dir)


def test_golden_path_pg_with_tax_freeze_and_splits(pg_client):
    token = pg_client["token"]
    rid = pg_client["restaurant_id"]
    guest_h = headers(token)
    naan = pg_client["menu"]["Butter Naan"]
    roti = pg_client["menu"]["Tandoori Roti"]
    unit = naan["pricePaise"]
    roti_price = roti["pricePaise"]
    staff_h = {"X-Staff-Dev-Key": DEV_STAFF}
    kitchen_h = {"X-Kitchen-Dev-Key": DEV_KITCHEN}

    with pg_client["mk_client"]() as client:
        # 1. Guest orders: 2x Butter Naan + 1x Tandoori Roti.
        order = client.post(
            "/api/v1/orders",
            json={"items": [
                {"menuItemId": naan["id"], "quantity": 2},
                {"menuItemId": roti["id"], "quantity": 1},
            ]},
            headers={**guest_h, "Idempotency-Key": f"pg9-gold-{uuid.uuid4().hex}"},
        )
        assert order.status_code == 201, order.text
        order = order.json()["order"]
        subtotal = 2 * unit + roti_price
        assert order["subtotalPaise"] == subtotal
        assert order["totalPaise"] == subtotal

        # 2-4. Kitchen: preparing -> ready; staff serves.
        for status in ("preparing", "ready"):
            r = client.patch(
                f"/api/v1/kitchen/orders/{order['id']}/status?restaurant_id={rid}",
                json={"status": status}, headers=kitchen_h)
            assert r.status_code == 200, r.text
        r = client.patch(
            f"/api/v1/staff/orders/{order['id']}/serve?restaurant_id={rid}",
            headers=staff_h)
        assert r.status_code == 200, r.text
        assert r.json()["data"]["status"] == "served"

        # 5. Bill (no tax yet) + equal split in 2.
        bill = client.post("/api/v1/guest/bill", headers=guest_h).json()["data"]
        assert bill["subtotalPaise"] == subtotal
        assert bill["taxPaise"] == 0
        assert bill["totalPaise"] == subtotal
        split = client.post(
            "/api/v1/guest/bill/splits",
            json={"mode": "equal", "parts": 2}, headers=guest_h)
        assert split.status_code == 200, split.text

        # 6. Owner configures 5% exclusive GST; guest requests
        # payment -> freeze re-derives the splits to the frozen
        # total (Stage 8 rule).
        tax = client.post(
            "/api/v1/admin/settings/tax",
            json={"enabled": True, "rateBps": 500, "mode": "exclusive",
                  "label": "GST",
                  "effectiveFrom": datetime.now(timezone.utc).isoformat()},
            headers=pg_client["owner_h"],
        )
        assert tax.status_code == 201, tax.text
        frozen = client.patch(
            "/api/v1/guest/bill/request-payment", headers=guest_h)
        assert frozen.status_code == 200, frozen.text
        frozen = frozen.json()["data"]
        expected_tax = subtotal * 500 // 10000
        expected_total = subtotal + expected_tax
        assert frozen["status"] == "payment_requested"
        assert frozen["taxPaise"] == expected_tax
        assert frozen["totalPaise"] == expected_total
        assert frozen["taxRateBpsSnapshot"] == 500
        assert frozen["taxModeSnapshot"] == "exclusive"
        assert frozen["taxLabelSnapshot"] == "GST"
        amounts = [s["amountPaise"] for s in frozen["splits"]]
        assert sum(amounts) == expected_total
        base, rem = divmod(expected_total, 2)
        assert amounts == [base + 1, base] if rem else [base, base]

        # 7. Staff collects the cash split by split; the bill is
        # paid only after the second split lands.
        for i, part in enumerate(frozen["splits"]):
            pay = client.post(
                f"/api/v1/staff/bills/{frozen['id']}/payments?restaurant_id={rid}",
                json={"amountPaise": part["amountPaise"],
                      "billSplitId": part["id"]},
                headers={**staff_h,
                         "Idempotency-Key": f"pg9-pay-{i}-{uuid.uuid4().hex}"},
            )
            assert pay.status_code == 201, pay.text
            if i == 0:
                assert pay.json()["data"]["bill"]["status"] == "payment_requested"
                assert pay.json()["data"]["bill"]["amountPaidPaise"] == amounts[0]
            else:
                assert pay.json()["data"]["bill"]["status"] == "paid"
                receipt_number = pay.json()["data"]["receipt"]["receiptNumber"]
                assert receipt_number.startswith("R-")

        # 8. Guest bill + receipt agree with the frozen numbers.
        final_bill = client.get("/api/v1/guest/bill", headers=guest_h).json()["data"]
        assert final_bill["status"] == "paid"
        assert final_bill["totalPaise"] == expected_total
        assert final_bill["amountPaidPaise"] == expected_total
        assert final_bill["outstandingPaise"] == 0
        assert final_bill["taxRateBpsSnapshot"] == 500
        receipt = client.get(
            "/api/v1/guest/bill/receipt", headers=guest_h).json()["data"]
        assert receipt["receiptNumber"] == receipt_number
        assert receipt["billNumber"] == frozen["billNumber"]
        assert receipt["totalPaise"] == expected_total
        assert receipt["amountReceivedPaise"] == expected_total
        assert receipt["outstandingPaise"] == 0
        assert receipt["taxRateBpsSnapshot"] == 500
        assert receipt["taxPaise"] == expected_tax
        assert receipt["subtotalPaise"] == subtotal
