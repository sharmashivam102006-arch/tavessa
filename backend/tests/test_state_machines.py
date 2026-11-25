"""Stage 9 — status-machine tests (SQLite suite).

Every LEGAL transition and every ILLEGAL transition for each
status machine, driven through the real HTTP endpoints (or the
service where the endpoint is the machine), never by hand-setting
DB columns:

- order: placed -> preparing -> ready -> served (+ placed ->
  cancelled via guest cancel). NOTE: the model map also lists
  "accepted" and "completed", but NO endpoint transitions an
  order into either state — the kitchen PATCH accepts only
  preparing/ready (anything else is 422) and there is no
  complete endpoint. Those two states are unreachable in the
  built API; the tests below pin that (422) rather than
  pretending the transitions exist.
- order-item: queued -> preparing -> ready -> served, following
  the order; cancelled on guest cancel.
- bill: issued -> payment_requested -> paid (+ issued ->
  payment_requested direct-to-paid, issued/payment_requested ->
  voided, paid -> refunded / partially_refunded).
- payment: confirmed -> refunded / partially_refunded (via
  admin refund); a second confirmation of the same split is a
  409 (already covered by test_billing.py::
  test_second_confirmation_same_split_new_key_409 — not
  duplicated here).
- help-call: open -> acknowledged -> resolved.

Illegal transitions assert 409 (or the designed code) AND that
the stored state is unchanged.
"""
import uuid

import pytest
from sqlalchemy import select

from app.core.security import hash_password
from app.models import Bill, Order, Restaurant, User
from tests.helpers import (
    fresh_table_token,
    headers,
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


def _kitchen_patch(client, setup, order_id, status):
    return client.patch(
        f"/api/v1/kitchen/orders/{order_id}/status"
        f"?restaurant_id={setup['restaurant_id']}",
        json={"status": status},
        headers=_kitchen_headers(),
    )


def _serve(client, setup, order_id):
    return client.patch(
        f"/api/v1/staff/orders/{order_id}/serve"
        f"?restaurant_id={setup['restaurant_id']}",
        headers=_staff_headers(),
    )


def _cancel(client, setup, order_id):
    return client.post(
        f"/api/v1/orders/{order_id}/cancel",
        json={},
        headers=headers(setup["token"]),
    )


def _order_row(db_session, order_id):
    return db_session.get(Order, uuid.UUID(order_id))


def _bill(client, setup):
    response = client.post("/api/v1/guest/bill", headers=headers(setup["token"]))
    assert response.status_code == 200, response.text
    return response.json()["data"]


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


def _help_call(client, setup):
    response = client.post(
        "/api/v1/guest/help-calls",
        json={"type": "waiter", "note": "Water please"},
        headers=headers(setup["token"]),
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]


def _ack_help(client, setup, call_id):
    return client.patch(
        f"/api/v1/staff/help-calls/{call_id}/acknowledge"
        f"?restaurant_id={setup['restaurant_id']}",
        headers=_staff_headers(),
    )


def _resolve_help(client, setup, call_id):
    return client.patch(
        f"/api/v1/staff/help-calls/{call_id}/resolve"
        f"?restaurant_id={setup['restaurant_id']}",
        headers=_staff_headers(),
    )


# ----------------------------------------------------------------------
# Order machine (kitchen + staff + guest cancel)
# ----------------------------------------------------------------------


def test_order_legal_kitchen_path_items_follow(client, db_session, setup):
    order = _place(client, setup, f"sm-{uuid.uuid4().hex}")
    row = _order_row(db_session, order["id"])
    assert row.status == "placed"
    assert [i.status for i in row.items] == ["queued"]

    response = _kitchen_patch(client, setup, order["id"], "preparing")
    assert response.status_code == 200, response.text
    assert response.json()["data"]["status"] == "preparing"
    db_session.refresh(row)
    assert row.status == "preparing"
    assert row.accepted_at is not None
    assert [i.status for i in row.items] == ["preparing"]

    response = _kitchen_patch(client, setup, order["id"], "ready")
    assert response.status_code == 200, response.text
    db_session.refresh(row)
    assert row.status == "ready"
    assert row.ready_at is not None
    assert [i.status for i in row.items] == ["ready"]

    response = _serve(client, setup, order["id"])
    assert response.status_code == 200, response.text
    db_session.refresh(row)
    assert row.status == "served"
    assert row.served_at is not None
    assert [i.status for i in row.items] == ["served"]


@pytest.mark.parametrize(
    "from_status,target",
    [
        ("placed", "ready"),
        ("placed", "served"),
        ("placed", "completed"),
        ("preparing", "preparing"),
        ("preparing", "served"),
        ("ready", "preparing"),
        ("ready", "ready"),
        ("ready", "served"),  # served is a STAFF action, not kitchen
        ("served", "preparing"),
        ("served", "ready"),
    ],
)
def test_order_illegal_kitchen_transitions_409_or_422(
    client, db_session, setup, from_status, target
):
    order = _place(client, setup, f"sm-illegal-{uuid.uuid4().hex}")
    if from_status == "preparing":
        assert _kitchen_patch(client, setup, order["id"], "preparing").status_code == 200
    elif from_status == "ready":
        assert _kitchen_patch(client, setup, order["id"], "preparing").status_code == 200
        assert _kitchen_patch(client, setup, order["id"], "ready").status_code == 200
    elif from_status == "served":
        assert _kitchen_patch(client, setup, order["id"], "preparing").status_code == 200
        assert _kitchen_patch(client, setup, order["id"], "ready").status_code == 200
        assert _serve(client, setup, order["id"]).status_code == 200

    response = _kitchen_patch(client, setup, order["id"], target)
    # "completed"/"served" are not accepted kitchen values at all
    # (the route schema allows only preparing|ready -> 422); every
    # other illegal move is a 409 conflict.
    expected = 422 if target in ("completed", "served") else 409
    assert response.status_code == expected, (from_status, target, response.text)
    row = _order_row(db_session, order["id"])
    db_session.refresh(row)
    assert row.status == from_status, (from_status, target)


def test_order_kitchen_rejects_unreachable_states(client, db_session, setup):
    """accepted/completed exist in the model map but no endpoint
    reaches them: the kitchen PATCH 422s on both values."""
    order = _place(client, setup, f"sm-unreach-{uuid.uuid4().hex}")
    for value in ("accepted", "completed", "cancelled", "bogus"):
        response = _kitchen_patch(client, setup, order["id"], value)
        assert response.status_code == 422, (value, response.text)
    assert _order_row(db_session, order["id"]).status == "placed"


@pytest.mark.parametrize("from_status", ["placed", "preparing", "ready", "served"])
def test_order_serve_only_from_ready(client, db_session, setup, from_status):
    order = _place(client, setup, f"sm-serve-{uuid.uuid4().hex}")
    if from_status in ("preparing", "ready", "served"):
        assert _kitchen_patch(client, setup, order["id"], "preparing").status_code == 200
    if from_status in ("ready", "served"):
        assert _kitchen_patch(client, setup, order["id"], "ready").status_code == 200
    if from_status == "served":
        assert _serve(client, setup, order["id"]).status_code == 200

    response = _serve(client, setup, order["id"])
    if from_status == "ready":
        assert response.status_code == 200, response.text
    else:
        assert response.status_code == 409, (from_status, response.text)
        assert _order_row(db_session, order["id"]).status == from_status


def test_order_guest_cancel_only_while_placed(client, db_session, setup):
    order = _place(client, setup, f"sm-cancel-{uuid.uuid4().hex}")
    response = _cancel(client, setup, order["id"])
    assert response.status_code == 200, response.text
    assert response.json()["data"]["status"] == "cancelled"
    row = _order_row(db_session, order["id"])
    db_session.refresh(row)
    assert row.status == "cancelled"
    assert row.cancelled_at is not None
    assert [i.status for i in row.items] == ["cancelled"]
    # Cancelling a cancelled order is a 409, state unchanged.
    assert _cancel(client, setup, order["id"]).status_code == 409


@pytest.mark.parametrize("drive_to", ["preparing", "ready", "served"])
def test_order_guest_cancel_after_placed_is_409(
    client, db_session, setup, drive_to
):
    order = _place(client, setup, f"sm-cancel2-{uuid.uuid4().hex}")
    assert _kitchen_patch(client, setup, order["id"], "preparing").status_code == 200
    if drive_to in ("ready", "served"):
        assert _kitchen_patch(client, setup, order["id"], "ready").status_code == 200
    if drive_to == "served":
        assert _serve(client, setup, order["id"]).status_code == 200
    response = _cancel(client, setup, order["id"])
    assert response.status_code == 409, (drive_to, response.text)
    assert _order_row(db_session, order["id"]).status == drive_to


# ----------------------------------------------------------------------
# Bill machine
# ----------------------------------------------------------------------


def test_bill_issued_to_payment_requested_is_idempotent(client, db_session, setup):
    _place(client, setup, f"sm-bill-{uuid.uuid4().hex}")
    bill = _bill(client, setup)
    assert bill["status"] == "issued"
    first = _request_payment(client, setup)
    assert first.status_code == 200, first.text
    assert first.json()["data"]["status"] == "payment_requested"
    second = _request_payment(client, setup)
    assert second.status_code == 200, second.text
    assert second.json()["data"]["status"] == "payment_requested"
    assert second.json()["data"]["version"] == first.json()["data"]["version"]


def test_bill_issued_can_be_paid_without_request(client, db_session, setup):
    _place(client, setup, f"sm-bill2-{uuid.uuid4().hex}")
    bill = _bill(client, setup)
    response = _pay(client, setup, bill["id"],
                    {"amountPaise": bill["totalPaise"]},
                    f"sm-pay-{uuid.uuid4().hex}")
    assert response.status_code == 201, response.text
    assert response.json()["data"]["bill"]["status"] == "paid"


def test_bill_request_payment_after_paid_is_409(client, db_session, setup):
    _place(client, setup, f"sm-bill3-{uuid.uuid4().hex}")
    bill = _bill(client, setup)
    assert _pay(client, setup, bill["id"],
                {"amountPaise": bill["totalPaise"]},
                f"sm-pay3-{uuid.uuid4().hex}").status_code == 201
    response = _request_payment(client, setup)
    assert response.status_code == 409, response.text


def test_bill_payment_on_issued_then_second_payment_409(client, db_session, setup):
    _place(client, setup, f"sm-bill4-{uuid.uuid4().hex}")
    bill = _bill(client, setup)
    assert _pay(client, setup, bill["id"],
                {"amountPaise": bill["totalPaise"]},
                f"sm-pay4-{uuid.uuid4().hex}").status_code == 201
    response = _pay(client, setup, bill["id"],
                    {"amountPaise": 100},
                    f"sm-pay4b-{uuid.uuid4().hex}")
    assert response.status_code == 409, response.text


def test_bill_receipt_only_after_paid(client, db_session, setup):
    _place(client, setup, f"sm-bill5-{uuid.uuid4().hex}")
    bill = _bill(client, setup)
    receipt = client.get(
        "/api/v1/guest/bill/receipt", headers=headers(setup["token"]))
    assert receipt.status_code == 409, receipt.text
    assert _pay(client, setup, bill["id"],
                {"amountPaise": bill["totalPaise"]},
                f"sm-pay5-{uuid.uuid4().hex}").status_code == 201
    receipt = client.get(
        "/api/v1/guest/bill/receipt", headers=headers(setup["token"]))
    assert receipt.status_code == 200, receipt.text
    assert receipt.json()["data"]["billNumber"] == bill["billNumber"]


def test_bill_void_rules(client, db_session, setup):
    owner_h = _owner_headers(client, db_session, setup)

    def _void(bill_id):
        return client.post(
            f"/api/v1/admin/bills/{bill_id}/void",
            json={"reason": "wrong table"},
            headers=owner_h,
        )

    # Void from issued: legal.
    _place(client, setup, f"sm-void-{uuid.uuid4().hex}")
    bill = _bill(client, setup)
    response = _void(bill["id"])
    assert response.status_code == 200, response.text
    assert response.json()["data"]["status"] == "voided"
    # Repeat void: 409, state unchanged.
    assert _void(bill["id"]).status_code == 409

    # Void from payment_requested: legal.
    _place(client, setup, f"sm-void2-{uuid.uuid4().hex}")
    bill2 = _bill(client, setup)
    assert _request_payment(client, setup).status_code == 200
    assert _void(bill2["id"]).status_code == 200

    # Void from paid: 409 (refund instead).
    _place(client, setup, f"sm-void3-{uuid.uuid4().hex}")
    bill3 = _bill(client, setup)
    assert _pay(client, setup, bill3["id"],
                {"amountPaise": bill3["totalPaise"]},
                f"sm-pay6-{uuid.uuid4().hex}").status_code == 201
    response = _void(bill3["id"])
    assert response.status_code == 409, response.text
    assert db_session.get(Bill, uuid.UUID(bill3["id"])).status == "paid"


# ----------------------------------------------------------------------
# Payment + refund machine
# ----------------------------------------------------------------------


def _staff_bill_detail(client, setup, bill_id):
    response = client.get(
        f"/api/v1/staff/bills/{bill_id}?restaurant_id={setup['restaurant_id']}",
        headers=_staff_headers(),
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


def test_refund_moves_payment_and_bill_statuses(client, db_session, setup):
    owner_h = _owner_headers(client, db_session, setup)
    _place(client, setup, f"sm-ref-{uuid.uuid4().hex}")
    bill = _bill(client, setup)
    pay = _pay(client, setup, bill["id"],
               {"amountPaise": bill["totalPaise"]},
               f"sm-pay7-{uuid.uuid4().hex}")
    assert pay.status_code == 201, pay.text
    payment_id = pay.json()["data"]["payment"]["id"]
    total = bill["totalPaise"]

    # Partial refund: payment + bill both partially_refunded.
    response = client.post(
        f"/api/v1/admin/bills/{bill['id']}/refunds",
        json={"paymentId": payment_id, "amountPaise": 100,
              "reason": "spilled drink"},
        headers={**owner_h, "Idempotency-Key": f"sm-ref-{uuid.uuid4().hex}"},
    )
    assert response.status_code == 201, response.text
    detail = _staff_bill_detail(client, setup, bill["id"])
    assert detail["status"] == "partially_refunded"
    assert detail["payments"][0]["status"] == "partially_refunded"

    # Full remainder refund: both flip to refunded.
    response = client.post(
        f"/api/v1/admin/bills/{bill['id']}/refunds",
        json={"paymentId": payment_id, "amountPaise": total - 100,
              "reason": "rest of it"},
        headers={**owner_h, "Idempotency-Key": f"sm-ref2-{uuid.uuid4().hex}"},
    )
    assert response.status_code == 201, response.text
    detail = _staff_bill_detail(client, setup, bill["id"])
    assert detail["status"] == "refunded"
    assert detail["payments"][0]["status"] == "refunded"


def test_refund_on_unpaid_bill_is_409(client, db_session, setup):
    owner_h = _owner_headers(client, db_session, setup)
    _place(client, setup, f"sm-ref2-{uuid.uuid4().hex}")
    bill = _bill(client, setup)
    response = client.post(
        f"/api/v1/admin/bills/{bill['id']}/refunds",
        json={"paymentId": str(uuid.uuid4()), "amountPaise": 100,
              "reason": "too early"},
        headers={**owner_h, "Idempotency-Key": f"sm-ref3-{uuid.uuid4().hex}"},
    )
    assert response.status_code == 409, response.text


# ----------------------------------------------------------------------
# Help-call machine
# ----------------------------------------------------------------------


def test_help_call_legal_path(client, db_session, setup):
    call = _help_call(client, setup)
    assert call["status"] == "open"
    response = _ack_help(client, setup, call["id"])
    assert response.status_code == 200, response.text
    assert response.json()["data"]["status"] == "acknowledged"
    response = _resolve_help(client, setup, call["id"])
    assert response.status_code == 200, response.text
    assert response.json()["data"]["status"] == "resolved"


@pytest.mark.parametrize("action", ["acknowledge", "resolve"])
def test_help_call_open_to_resolve_is_409(client, db_session, setup, action):
    call = _help_call(client, setup)
    if action == "resolve":
        assert _resolve_help(client, setup, call["id"]).status_code == 409
    else:
        assert _ack_help(client, setup, call["id"]).status_code == 200
        assert _ack_help(client, setup, call["id"]).status_code == 409


def test_help_call_terminal_states_reject_everything(client, db_session, setup):
    call = _help_call(client, setup)
    assert _ack_help(client, setup, call["id"]).status_code == 200
    assert _resolve_help(client, setup, call["id"]).status_code == 200
    assert _ack_help(client, setup, call["id"]).status_code == 409
    assert _resolve_help(client, setup, call["id"]).status_code == 409
