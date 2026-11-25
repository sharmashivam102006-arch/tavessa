"""Stage 8 — split/tax-freeze reconciliation (task A).

SQLite suite with its OWN in-memory engine (like test_admin.py):
reconciliation tests configure tax, which is restaurant-global
state that must not pollute the other suites.

Covers: equal splits re-derived at an exclusive freeze (+ audit
row), custom splits blocking the freeze with 409 and recovering
after the guest re-creates them against the live total, an
inclusive freeze, exact reconciliation (no audit row), the
payment-time split-sum guard, refunds after split payments
(refunds reduce the payment, not the split amount), and a
historical PAID bill staying unchanged when the tax config moves.
"""
import uuid
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.security import hash_password
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models import AuditLog, Bill, Payment, Restaurant, User
from app.seed.zaika import RESTAURANT_SLUG
from tests.helpers import (
    fresh_table_token,
    headers,
    menu_by_name,
    open_session,
    seed,
)

_engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
_Session = sessionmaker(bind=_engine, autoflush=False, autocommit=False)
Base.metadata.create_all(bind=_engine)


import pytest


@pytest.fixture
def db_session():
    session = _Session()
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


@pytest.fixture
def owner(db_session):
    seed(db_session)
    restaurant_id = db_session.execute(
        select(Restaurant).where(Restaurant.slug == RESTAURANT_SLUG)
    ).scalars().first().id
    user = User(
        restaurant_id=restaurant_id,
        branch_id=None,
        email=f"owner-{uuid.uuid4().hex[:8]}@zaika.example",
        password_hash=hash_password("ownerpass"),
        role="owner",
        is_active=True,
        must_change_password=False,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def _auth_headers(client, owner):
    response = client.post(
        "/api/v1/auth/login",
        json={"email": owner.email, "password": "ownerpass"},
    )
    assert response.status_code == 200, response.text
    token = response.json()["data"]["accessToken"]
    return {"Authorization": f"Bearer {token}"}


def _restaurant_id(db_session):
    return db_session.execute(
        select(Restaurant).where(Restaurant.slug == RESTAURANT_SLUG)
    ).scalars().first().id


def _set_tax(client, h, rate_bps, mode, enabled=True):
    now = datetime.now(timezone.utc)
    response = client.post(
        "/api/v1/admin/settings/tax",
        json={
            "enabled": enabled, "rateBps": rate_bps, "mode": mode,
            "label": "GST", "effectiveFrom": now.isoformat(),
        },
        headers=h,
    )
    assert response.status_code == 201, response.text


def _order_bill(client, db_session, quantity=1):
    """Guest order -> bill, on a FRESH table per test (fresh_table_token
    isolates the session/orders from every other test sharing this
    module's DB — make_token(5) would reuse table 5's session).
    Returns (token, bill dict)."""
    token = fresh_table_token(db_session)
    open_session(client, token)
    menu = menu_by_name(client, token)[0]
    item = menu["Butter Naan"]
    response = client.post(
        "/api/v1/orders",
        json={"items": [{"menuItemId": item["id"], "quantity": quantity}]},
        headers={**headers(token), "Idempotency-Key": f"r-{uuid.uuid4().hex}"},
    )
    assert response.status_code == 201, response.text
    bill = client.post("/api/v1/guest/bill", headers=headers(token)).json()["data"]
    return token, bill


def _create_splits(client, token, body):
    return client.post(
        "/api/v1/guest/bill/splits", json=body, headers=headers(token)
    )


def _request_payment(client, token):
    return client.patch(
        "/api/v1/guest/bill/request-payment", headers=headers(token)
    )


def _pay_staff(client, db_session, bill_id, amount, split_id=None):
    body = {"amountPaise": amount}
    if split_id is not None:
        body["billSplitId"] = split_id
    return client.post(
        f"/api/v1/staff/bills/{bill_id}/payments"
        f"?restaurant_id={_restaurant_id(db_session)}",
        json=body,
        headers={
            "X-Staff-Dev-Key": "dev-staff-only",
            "Idempotency-Key": f"p-{uuid.uuid4().hex}",
        },
    )


def _audit_rows(db_session, bill_id, action):
    return db_session.execute(
        select(AuditLog).where(
            AuditLog.entity_id == bill_id, AuditLog.action == action
        )
    ).scalars().all()


def _equal_parts(total, n):
    base = total // n
    remainder = total % n
    return [base + (1 if i < remainder else 0) for i in range(n)]


def test_equal_splits_rederived_at_exclusive_freeze(
    client, db_session, owner
):
    h = _auth_headers(client, owner)
    _set_tax(client, h, 500, "exclusive")  # 5% exclusive
    token, bill = _order_bill(client, db_session)
    subtotal = bill["subtotalPaise"]
    assert subtotal > 0
    # Equal split while 5% is in force: parts sum to the live total
    # (subtotal + 5%).
    live_total = bill["totalPaise"]
    assert live_total == subtotal + subtotal * 500 // 10000
    created = _create_splits(
        client, token, {"mode": "equal", "parts": 2}
    )
    assert created.status_code == 200, created.text
    old_amounts = [s["amountPaise"] for s in created.json()["data"]["splits"]]
    assert old_amounts == _equal_parts(live_total, 2)
    # The tax config changes BEFORE the freeze: 5% -> 10%.
    _set_tax(client, h, 1000, "exclusive")
    # Freeze: request payment. The frozen total grows again, and
    # the equal splits are re-derived from it in the same txn.
    requested = _request_payment(client, token)
    assert requested.status_code == 200, requested.text
    frozen = requested.json()["data"]
    expected_total = subtotal + subtotal * 1000 // 10000
    assert frozen["totalPaise"] == expected_total
    assert frozen["taxPaise"] == expected_total - subtotal
    amounts = [s["amountPaise"] for s in frozen["splits"]]
    assert sum(amounts) == expected_total
    assert amounts == _equal_parts(expected_total, 2)
    assert all(s["mode"] == "equal" for s in frozen["splits"])
    # An audit row records the re-derivation (amounts changed).
    # (The audit scrubber stringifies list values by design —
    # Stage 7 — so the amounts round-trip as their str() form.)
    rows = _audit_rows(db_session, uuid.UUID(frozen["id"]), "bill.splits_reconciled")
    assert len(rows) == 1
    assert rows[0].details["old_amounts_paise"] == str(old_amounts)
    assert rows[0].details["new_amounts_paise"] == str(amounts)


def test_custom_splits_block_freeze_then_recover(
    client, db_session, owner
):
    h = _auth_headers(client, owner)
    # Tax OFF while the custom splits are created, so they sum to
    # the plain subtotal — the live total at creation time.
    _set_tax(client, h, 0, "exclusive", enabled=False)
    token, bill = _order_bill(client, db_session)
    subtotal = bill["subtotalPaise"]
    assert bill["totalPaise"] == subtotal
    half = subtotal // 2
    created = _create_splits(
        client, token,
        {"mode": "custom", "amountsPaise": [half, subtotal - half]},
    )
    assert created.status_code == 200, created.text
    # Tax turns ON (5%) before the freeze: the custom splits now
    # sum to the pre-tax total, so the freeze must BLOCK.
    _set_tax(client, h, 500, "exclusive")
    blocked = _request_payment(client, token)
    assert blocked.status_code == 409, blocked.text
    error = blocked.json()["error"]
    assert error["code"] == "split_reconciliation_required"
    expected_total = subtotal + subtotal * 500 // 10000
    assert error["details"]["expected_total_paise"] == expected_total
    assert error["details"]["splits_sum_paise"] == subtotal
    assert error["details"]["difference_paise"] == expected_total - subtotal
    # The bill is untouched by the blocked freeze: still issued,
    # never frozen (no tax snapshot), splits still summing to the
    # pre-tax total, no audit row. (The live totalPaise on a fresh
    # fetch legitimately recomputes to the 5% total — that is
    # regeneration of an issued bill, not a freeze.)
    again = client.post("/api/v1/guest/bill", headers=headers(token)).json()["data"]
    assert again["status"] == "issued"
    assert again["taxRateBpsSnapshot"] is None
    assert again["taxModeSnapshot"] is None
    assert sum(s["amountPaise"] for s in again["splits"]) == subtotal
    assert _audit_rows(
        db_session, uuid.UUID(bill["id"]), "bill.splits_reconciled"
    ) == []
    # The guest re-creates the splits against the LIVE total
    # (subtotal + 5% tax) and retries — the freeze then succeeds.
    live_total = expected_total
    half_live = live_total // 2
    recreated = _create_splits(
        client, token,
        {"mode": "custom", "amountsPaise": [half_live, live_total - half_live]},
    )
    assert recreated.status_code == 200, recreated.text
    assert recreated.json()["data"]["totalPaise"] == live_total
    requested = _request_payment(client, token)
    assert requested.status_code == 200, requested.text
    assert requested.json()["data"]["totalPaise"] == live_total
    assert sum(
        s["amountPaise"] for s in requested.json()["data"]["splits"]
    ) == live_total


def test_inclusive_freeze_keeps_total_and_splits(client, db_session, owner):
    h = _auth_headers(client, owner)
    _set_tax(client, h, 1000, "inclusive")  # 10% inclusive
    token, bill = _order_bill(client, db_session)
    subtotal = bill["subtotalPaise"]
    created = _create_splits(client, token, {"mode": "equal", "parts": 3})
    assert created.status_code == 200, created.text
    requested = _request_payment(client, token)
    assert requested.status_code == 200, requested.text
    frozen = requested.json()["data"]
    # Inclusive: the total is the subtotal; the tax is inside it.
    assert frozen["totalPaise"] == subtotal
    assert frozen["taxPaise"] > 0
    assert frozen["taxPaise"] + (subtotal - frozen["taxPaise"]) == subtotal
    amounts = [s["amountPaise"] for s in frozen["splits"]]
    assert sum(amounts) == subtotal
    assert amounts == _equal_parts(subtotal, 3)


def test_exact_reconciliation_writes_no_audit_row(client, db_session, owner):
    h = _auth_headers(client, owner)
    _set_tax(client, h, 1000, "inclusive")  # 10% inclusive
    token, bill = _order_bill(client, db_session)
    subtotal = bill["subtotalPaise"]
    half = subtotal // 2
    created = _create_splits(
        client, token,
        {"mode": "custom", "amountsPaise": [half, subtotal - half]},
    )
    assert created.status_code == 200, created.text
    requested = _request_payment(client, token)
    assert requested.status_code == 200, requested.text
    # Splits already summed to the frozen total: no re-derivation,
    # so no audit row.
    assert _audit_rows(
        db_session, uuid.UUID(bill["id"]), "bill.splits_reconciled"
    ) == []


def test_split_payment_guard_rejects_stale_amount(client, db_session, owner):
    h = _auth_headers(client, owner)
    _set_tax(client, h, 500, "exclusive")
    token, bill = _order_bill(client, db_session, quantity=2)
    created = _create_splits(client, token, {"mode": "equal", "parts": 2})
    assert created.status_code == 200, created.text
    requested = _request_payment(client, token)
    assert requested.status_code == 200, requested.text
    frozen = requested.json()["data"]
    split = frozen["splits"][0]
    # Corrupt one split amount directly (simulating a stale split
    # the freeze did not reconcile): the payment must refuse.
    row = db_session.get(Bill, uuid.UUID(bill["id"]))
    target = [s for s in row.splits if s.id == uuid.UUID(split["id"])][0]
    target.amount_paise += 1
    db_session.commit()
    refused = _pay_staff(
        client, db_session, bill["id"], split["amountPaise"], split["id"]
    )
    assert refused.status_code == 409, refused.text
    assert refused.json()["error"]["code"] == "split_reconciliation_required"
    assert db_session.execute(
        select(Payment).where(Payment.bill_id == uuid.UUID(bill["id"]))
    ).scalars().all() == []
    # Restore the amount: the same payment now succeeds.
    target.amount_paise -= 1
    db_session.commit()
    paid = _pay_staff(
        client, db_session, bill["id"], split["amountPaise"], split["id"]
    )
    assert paid.status_code == 201, paid.text


def test_refund_after_split_payment_keeps_split_consistent(
    client, db_session, owner
):
    h = _auth_headers(client, owner)
    _set_tax(client, h, 500, "exclusive")
    token, bill = _order_bill(client, db_session, quantity=2)
    created = _create_splits(client, token, {"mode": "equal", "parts": 2})
    assert created.status_code == 200, created.text
    requested = _request_payment(client, token)
    assert requested.status_code == 200, requested.text
    frozen = requested.json()["data"]
    split = frozen["splits"][0]
    paid = _pay_staff(
        client, db_session, bill["id"], split["amountPaise"], split["id"]
    )
    assert paid.status_code == 201, paid.text
    payment = paid.json()["data"]["payment"]
    assert payment["billSplitId"] == split["id"]
    # Pay the second split too: the bill is now fully paid (a
    # refund needs a paid bill).
    other = frozen["splits"][1]
    paid2 = _pay_staff(
        client, db_session, bill["id"], other["amountPaise"], other["id"]
    )
    assert paid2.status_code == 201, paid2.text
    # Refund half of the first split's payment: the refund reduces
    # the PAYMENT, not the split amount — the split's amount never
    # moves, the split stays 'paid', and the bill becomes
    # partially_refunded (not re-opened for payment).
    refund = client.post(
        f"/api/v1/admin/bills/{bill['id']}/refunds",
        json={
            "paymentId": payment["id"],
            "amountPaise": split["amountPaise"] // 2,
            "reason": "guest changed mind",
        },
        headers={**h, "Idempotency-Key": f"rf-{uuid.uuid4().hex}"},
    )
    assert refund.status_code == 201, refund.text
    after = client.get(
        f"/api/v1/admin/bills/{bill['id']}", headers=h
    ).json()["data"]
    assert [s["amountPaise"] for s in after["splits"]][0] == split["amountPaise"]
    assert [s["status"] for s in after["splits"]][0] == "paid"
    assert after["status"] == "partially_refunded"
    assert [p["status"] for p in after["payments"] if p["id"] == payment["id"]] == [
        "partially_refunded"
    ]


def test_historical_paid_bill_unchanged_by_later_tax(
    client, db_session, owner
):
    h = _auth_headers(client, owner)
    token, bill = _order_bill(client, db_session)
    requested = _request_payment(client, token)
    assert requested.status_code == 200, requested.text
    frozen = requested.json()["data"]
    paid = _pay_staff(
        client, db_session, bill["id"], frozen["totalPaise"]
    )
    assert paid.status_code == 201, paid.text
    # The tax config moves after the bill is paid: the paid bill
    # and its recorded payment must not move.
    _set_tax(client, h, 1800, "exclusive")  # 18% exclusive
    after = client.get(
        f"/api/v1/admin/bills/{bill['id']}", headers=h
    ).json()["data"]
    assert after["status"] == "paid"
    assert after["totalPaise"] == frozen["totalPaise"]
    assert after["taxPaise"] == frozen["taxPaise"]
    assert after["amountPaidPaise"] == frozen["totalPaise"]
    payments = db_session.execute(
        select(Payment).where(Payment.bill_id == uuid.UUID(bill["id"]))
    ).scalars().all()
    assert len(payments) == 1
    assert payments[0].amount_paise == frozen["totalPaise"]
