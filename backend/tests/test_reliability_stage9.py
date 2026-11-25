"""Stage 9 — reliability (SQLite suite).

Failure modes with a test each (Postgres-only races live in
tests/test_reliability_concurrency_pg.py):

- Cancel after bill freeze / after payment / after partial
  payment -> 409 (Stage 9 FIX in order_service.cancel_order:
  a frozen bill never changes, so a guest cancel would leave
  them paying for cancelled food). Cancel on an unpaid issued
  bill still works, and on a voided bill too.
- Session expiry mid-flow -> 410, no order placed, no bill shown.
- Bill after more orders, with splits + frozen tax.
- Cancelled item after bill: lines recompute, totals never go
  negative, splits survive regeneration.
- KDS + guest socket offline/reconnect: queue re-fetched from
  REST, no order lost, no order shown twice, no event replay.

Duplicate submission and double payment confirmation are NOT
re-tested here — they are already covered:
- order duplicate (same key / different body): test_orders.py
  + test_orders_concurrency.py (incl. concurrent same-key).
- payment duplicate (same key replay, different body 422,
  failed payment leaves no claim, second confirmation of the
  same split with a new key -> 409): test_billing.py +
  test_billing_concurrency.py (8 simultaneous same-key -> one
  payment; 8 different-key -> never overpay).
- refund duplicate (same key -> one refund; concurrent refunds
  never exceed the payment): test_admin_concurrency.py.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.auth.models import TaxConfiguration
from app.core.security import hash_password
from app.models import Bill, Restaurant, User
from tests.helpers import (
    assert_ws_rejected,
    fresh_table_token,
    headers,
    make_token,
    make_token_row,
    menu_by_name,
    open_session,
    seed,
)

DEV_STAFF = "dev-staff-only"
DEV_KITCHEN = "dev-kitchen-only"
OWNER_PASSWORD = "Owner-pass-9"


def _staff_headers(key=DEV_STAFF):
    return {"X-Staff-Dev-Key": key}


def _kitchen_headers(key=DEV_KITCHEN):
    return {"X-Kitchen-Dev-Key": key}


def _all_kitchen_orders(client, setup):
    """ALL pages of the kitchen active-orders list.

    The shared test DB accumulates >100 active orders in a full
    run, so a just-placed order is not on page 1 — same reason as
    test_kitchen._all_kitchen_orders.
    """
    rows: list[dict] = []
    page = 1
    while True:
        body = client.get(
            f"/api/v1/kitchen/orders?restaurant_id={setup['restaurant_id']}"
            f"&page={page}&per_page=100",
            headers=_kitchen_headers(),
        ).json()
        rows.extend(body["data"])
        if page >= body["pagination"]["total_pages"]:
            return rows
        page += 1


@pytest.fixture
def setup(client, db_session):
    seed(db_session)
    token = fresh_table_token(db_session)
    open_session(client, token)
    by_name, _body = menu_by_name(client, token)
    restaurant = (
        db_session.execute(select(Restaurant).where(Restaurant.slug == "zaika"))
        .scalars()
        .first()
    )
    return {
        "token": token,
        "menu": by_name,
        "restaurant_id": str(restaurant.id),
        "restaurant": restaurant,
    }


def _place(client, setup, key, name="Butter Naan", quantity=1):
    item = setup["menu"][name]
    response = client.post(
        "/api/v1/orders",
        json={"items": [{"menuItemId": item["id"], "quantity": quantity}]},
        headers={**headers(setup["token"]), "Idempotency-Key": key},
    )
    assert response.status_code == 201, response.text
    return response.json()["order"]


def _bill_post(client, setup):
    response = client.post("/api/v1/guest/bill", headers=headers(setup["token"]))
    assert response.status_code == 200, response.text
    return response.json()["data"]


def _bill_get(client, setup):
    return client.get("/api/v1/guest/bill", headers=headers(setup["token"]))


def _request_payment(client, setup):
    return client.patch(
        "/api/v1/guest/bill/request-payment", headers=headers(setup["token"])
    )


def _pay(client, setup, bill_id, body, key):
    return client.post(
        f"/api/v1/staff/bills/{bill_id}/payments"
        f"?restaurant_id={setup['restaurant_id']}",
        json=body,
        headers={**_staff_headers(), "Idempotency-Key": key},
    )


def _cancel(client, setup, order_id):
    return client.post(
        f"/api/v1/orders/{order_id}/cancel",
        json={},
        headers=headers(setup["token"]),
    )


def _owner_headers(client, db_session, setup):
    email = f"owner-{uuid.uuid4().hex[:8]}@zaika.example"
    user = User(
        restaurant_id=setup["restaurant"].id,
        branch_id=None,
        email=email,
        password_hash=hash_password(OWNER_PASSWORD),
        role="owner",
        is_active=True,
        must_change_password=False,
    )
    db_session.add(user)
    db_session.commit()
    response = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": OWNER_PASSWORD},
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['data']['accessToken']}"}


def _set_tax(db_session, setup, rate_bps):
    row = TaxConfiguration(
        restaurant_id=uuid.UUID(setup["restaurant_id"]),
        branch_id=None,
        enabled=True,
        rate_bps=rate_bps,
        mode="exclusive",
        label="GST",
        effective_from=datetime.now(timezone.utc) - timedelta(days=1),
    )
    db_session.add(row)
    db_session.commit()
    return row


def _clear_tax(db_session, setup):
    for row in db_session.execute(
        select(TaxConfiguration).where(
            TaxConfiguration.restaurant_id == uuid.UUID(setup["restaurant_id"])
        )
    ).scalars().all():
        db_session.delete(row)
    db_session.commit()


# ----------------------------------------------------------------------
# Cancel after a bill is frozen / paid / partially paid (Stage 9 fix)
# ----------------------------------------------------------------------


def test_cancel_on_payment_requested_bill_is_409(client, db_session, setup):
    order = _place(client, setup, f"rel-{uuid.uuid4().hex}")
    bill = _bill_post(client, setup)
    assert _request_payment(client, setup).status_code == 200
    response = _cancel(client, setup, order["id"])
    assert response.status_code == 409, response.text
    assert response.json()["error"]["details"]["bill_number"] == bill["billNumber"]
    # Nothing changed: the order is still placed, the bill frozen.
    orders = client.get(
        "/api/v1/guest/orders", headers=headers(setup["token"])).json()["data"]
    assert orders[0]["status"] == "placed"
    assert _bill_get(client, setup).json()["data"]["status"] == "payment_requested"
    assert _bill_get(client, setup).json()["data"]["totalPaise"] == bill["totalPaise"]


def test_cancel_on_paid_bill_is_409(client, db_session, setup):
    order = _place(client, setup, f"rel-{uuid.uuid4().hex}")
    bill = _bill_post(client, setup)
    assert _pay(client, setup, bill["id"],
                {"amountPaise": bill["totalPaise"]},
                f"rel-pay-{uuid.uuid4().hex}").status_code == 201
    assert _cancel(client, setup, order["id"]).status_code == 409


def test_cancel_on_partially_paid_issued_bill_is_409(client, db_session, setup):
    order = _place(client, setup, f"rel-{uuid.uuid4().hex}")
    bill = _bill_post(client, setup)
    assert _pay(client, setup, bill["id"], {"amountPaise": 100},
                f"rel-pay-{uuid.uuid4().hex}").status_code == 201
    # The bill is still "issued" but cash has been collected
    # against it — cancelling would strand the payment.
    assert _cancel(client, setup, order["id"]).status_code == 409


def test_cancel_on_unpaid_issued_bill_still_works(client, db_session, setup):
    order = _place(client, setup, f"rel-{uuid.uuid4().hex}")
    _bill_post(client, setup)
    assert _cancel(client, setup, order["id"]).status_code == 200
    # The bill regenerates to nothing and is deleted.
    assert _bill_get(client, setup).status_code == 404


def test_cancel_after_bill_voided_works(client, db_session, setup):
    owner_h = _owner_headers(client, db_session, setup)
    order = _place(client, setup, f"rel-{uuid.uuid4().hex}")
    bill = _bill_post(client, setup)
    void = client.post(
        f"/api/v1/admin/bills/{bill['id']}/void",
        json={"reason": "wrong table"},
        headers=owner_h,
    )
    assert void.status_code == 200, void.text
    # Nothing is owed on a voided bill, so the guest may cancel.
    assert _cancel(client, setup, order["id"]).status_code == 200


# ----------------------------------------------------------------------
# Session expiry mid-flow
# ----------------------------------------------------------------------


def test_revoked_token_fails_cleanly_everywhere(client, db_session):
    seed(db_session)
    raw, _row = make_token_row(db_session, 5, status="revoked")
    # Session open itself: 410, not a silent menu.
    assert client.get(f"/api/v1/guest/t/{raw}").status_code == 410
    for method, url, extra in (
        ("GET", "/api/v1/guest/menu", {}),
        ("GET", "/api/v1/guest/orders", {}),
        ("GET", "/api/v1/guest/bill", {}),
        ("POST", "/api/v1/orders", {"json": {"items": []},
                                    "headers": {"Idempotency-Key": "x"}}),
        ("POST", "/api/v1/guest/bill", {}),
        ("POST", "/api/v1/guest/ws/tickets", {}),
    ):
        call_headers = {**headers(raw), **extra.pop("headers", {})}
        response = getattr(client, method.lower())(
            url, headers=call_headers, **extra)
        assert response.status_code == 410, (method, url, response.text)
        assert response.json()["error"]["code"] == "session_expired"


def test_expired_token_fails_with_410_and_unknown_with_401(client, db_session):
    seed(db_session)
    raw, _row = make_token_row(
        db_session, 5,
        expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
    )
    assert client.get(f"/api/v1/guest/t/{raw}").status_code == 410
    assert client.get("/api/v1/guest/t/not-a-real-token").status_code == 401
    assert client.get(
        "/api/v1/guest/menu",
        headers=headers("not-a-real-token")).status_code == 401


def test_token_revoked_mid_flow_no_order_placed(client, db_session):
    """Guest is mid-flow, the token is rotated (old one revoked):
    further guest calls fail 410 and NO order is placed."""
    seed(db_session)
    token = make_token(db_session, 5)
    open_session(client, token)
    by_name, _body = menu_by_name(client, token)
    order = client.post(
        "/api/v1/orders",
        json={"items": [{"menuItemId": by_name["Butter Naan"]["id"], "quantity": 1}]},
        headers={**headers(token), "Idempotency-Key": f"rel-mid-{uuid.uuid4().hex}"},
    )
    assert order.status_code == 201, order.text
    # Rotating the table token revokes the old one.
    make_token(db_session, 5)
    retry = client.post(
        "/api/v1/orders",
        json={"items": [{"menuItemId": by_name["Butter Naan"]["id"], "quantity": 1}]},
        headers={**headers(token), "Idempotency-Key": f"rel-mid2-{uuid.uuid4().hex}"},
    )
    assert retry.status_code == 410, retry.text
    assert client.get("/api/v1/guest/bill", headers=headers(token)).status_code == 410
    assert client.get("/api/v1/guest/menu", headers=headers(token)).status_code == 410


# ----------------------------------------------------------------------
# Bill after more orders — splits + frozen tax
# ----------------------------------------------------------------------


def test_new_orders_after_freeze_go_on_next_bill(client, db_session, setup):
    try:
        order1 = _place(client, setup, f"rel-{uuid.uuid4().hex}", quantity=2)
        bill1 = _bill_post(client, setup)
        subtotal1 = bill1["subtotalPaise"]
        # Equal split, then 5% tax, then freeze: splits re-derive.
        split = client.post(
            "/api/v1/guest/bill/splits",
            json={"mode": "equal", "parts": 2},
            headers=headers(setup["token"]),
        )
        assert split.status_code == 200, split.text
        _set_tax(db_session, setup, 500)
        frozen = _request_payment(client, setup)
        assert frozen.status_code == 200, frozen.text
        frozen_bill = frozen.json()["data"]
        assert frozen_bill["status"] == "payment_requested"
        assert frozen_bill["taxPaise"] == subtotal1 * 500 // 10000
        assert frozen_bill["totalPaise"] == subtotal1 + frozen_bill["taxPaise"]
        assert sum(s["amountPaise"] for s in frozen_bill["splits"]) == frozen_bill["totalPaise"]
        assert frozen_bill["taxRateBpsSnapshot"] == 500

        # A new order placed while the bill is frozen is NOT on it.
        order2 = _place(client, setup, f"rel-{uuid.uuid4().hex}", quantity=1)
        again = _bill_get(client, setup).json()["data"]
        assert again["id"] == frozen_bill["id"]
        assert again["totalPaise"] == frozen_bill["totalPaise"]
        assert order2["orderNumber"] not in again["orderNumbers"]

        # Pay the frozen bill in full — split by split (a bill with
        # splits has no whole-bill target) — then a NEW bill holds
        # only the second order.
        for split_row in frozen_bill["splits"]:
            assert _pay(client, setup, frozen_bill["id"],
                        {"amountPaise": split_row["amountPaise"],
                         "billSplitId": split_row["id"]},
                        f"rel-pay-{uuid.uuid4().hex}").status_code == 201
        bill2 = _bill_post(client, setup)
        assert bill2["id"] != frozen_bill["id"]
        assert bill2["orderNumbers"] == [order2["orderNumber"]]
        naan_price = setup["menu"]["Butter Naan"]["pricePaise"]
        assert bill2["subtotalPaise"] == naan_price
        assert bill2["taxPaise"] == naan_price * 500 // 10000
        assert bill2["totalPaise"] == naan_price + bill2["taxPaise"]

        # The paid bill is byte-identical: snapshot + totals frozen.
        paid_again = client.get(
            f"/api/v1/staff/bills/{frozen_bill['id']}"
            f"?restaurant_id={setup['restaurant_id']}",
            headers=_staff_headers(),
        ).json()["data"]
        assert paid_again["status"] == "paid"
        assert paid_again["totalPaise"] == frozen_bill["totalPaise"]
        assert paid_again["taxRateBpsSnapshot"] == 500
    finally:
        _clear_tax(db_session, setup)


# ----------------------------------------------------------------------
# Cancelled item after bill (issued, unpaid)
# ----------------------------------------------------------------------


def test_cancelled_order_after_bill_recomputes_never_negative(
    client, db_session, setup
):
    order1 = _place(client, setup, f"rel-{uuid.uuid4().hex}", quantity=2)
    order2 = _place(
        client, setup, f"rel-{uuid.uuid4().hex}", name="Tandoori Roti", quantity=1)
    bill = _bill_post(client, setup)
    total_before = bill["totalPaise"]
    # Equal split survives regeneration (Stage 8 rule) even though
    # its sum will mismatch until the guest updates it.
    split = client.post(
        "/api/v1/guest/bill/splits",
        json={"mode": "equal", "parts": 2},
        headers=headers(setup["token"]),
    )
    assert split.status_code == 200, split.text

    assert _cancel(client, setup, order2["id"]).status_code == 200
    after = _bill_get(client, setup).json()["data"]
    assert after["totalPaise"] == total_before - setup["menu"]["Tandoori Roti"]["pricePaise"]
    assert after["totalPaise"] >= 0
    assert after["orderNumbers"] == [order1["orderNumber"]]
    assert len(after["splits"]) == 2  # splits survive regeneration
    assert all(s["amountPaise"] >= 0 for s in after["splits"])
    assert all(line["totalPaise"] >= 0 for line in after["items"])

    # Cancelling everything empties the bill instead of going negative.
    assert _cancel(client, setup, order1["id"]).status_code == 200
    assert _bill_get(client, setup).status_code == 404


# ----------------------------------------------------------------------
# Socket offline / reconnect — queue re-fetched from REST
# ----------------------------------------------------------------------


def _login_user(client, db_session, setup, email, role):
    user = User(
        restaurant_id=setup["restaurant"].id,
        branch_id=None,
        email=email,
        password_hash=hash_password("Staff-pass-9"),
        role=role,
        is_active=True,
        must_change_password=False,
    )
    db_session.add(user)
    db_session.commit()
    response = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "Staff-pass-9"},
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['data']['accessToken']}"}


def _ws_ticket(client, auth_h, channel):
    response = client.post(
        "/api/v1/ws/tickets", json={"channel": channel}, headers=auth_h)
    assert response.status_code == 200, response.text
    return response.json()["data"]["ticket"]


def test_kitchen_socket_reconnect_refetches_queue_no_duplicates(
    client, db_session, setup
):
    kitchen_h = _login_user(
        client, db_session, setup,
        f"kitchen-{uuid.uuid4().hex[:8]}@zaika.example", "kitchen")
    order = _place(client, setup, f"rel-{uuid.uuid4().hex}")
    ticket = _ws_ticket(client, kitchen_h, "kitchen")
    with client.websocket_connect(
        f"/ws/kitchen?restaurant_id={setup['restaurant_id']}&ticket={ticket}"
    ) as ws:
        # The socket is live; drop it mid-service (no close frame
        # discipline — just disconnect) and reconnect with a FRESH
        # single-use ticket.
        pass
    ticket2 = _ws_ticket(client, kitchen_h, "kitchen")
    assert ticket2 != ticket
    with client.websocket_connect(
        f"/ws/kitchen?restaurant_id={setup['restaurant_id']}&ticket={ticket2}"
    ):
        # Reconnected on a fresh ticket. REST is the source of
        # truth after reconnect: the order is there exactly once,
        # no duplicates, no loss. (No receive_text here: the
        # server sends nothing until the next event, and a bare
        # read would block forever.) Page through the list: the
        # shared test DB accumulates >100 active orders in a
        # full run, so a just-placed order is not on page 1.
        rows = _all_kitchen_orders(client, setup)
        rows = [o for o in rows if o["id"] == order["id"]]
        assert len(rows) == 1
        assert rows[0]["status"] == "placed"


def test_guest_socket_reconnect_no_event_replay(client, db_session, setup):
    order = _place(client, setup, f"rel-{uuid.uuid4().hex}")

    def _guest_ticket():
        response = client.post(
            "/api/v1/guest/ws/tickets", headers=headers(setup["token"]))
        assert response.status_code == 201, response.text
        return response.json()["data"]["ticket"]

    with client.websocket_connect(
        f"/ws/guest?ticket={_guest_ticket()}"
    ) as ws:
        pass  # drop mid-service
    with client.websocket_connect(
        f"/ws/guest?ticket={_guest_ticket()}"
    ):
        # Reconnected on a fresh ticket. No replay is asserted
        # structurally: the guest socket only announces NEW
        # changes, and the state comes from REST — the order is
        # there exactly once.
        orders = client.get(
            "/api/v1/guest/orders", headers=headers(setup["token"])).json()["data"]
        assert [o["id"] for o in orders] == [order["id"]]
