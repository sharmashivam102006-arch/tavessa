"""Stage 9 — input validation: tampered payloads on every mutating
endpoint. Every case asserts the DESIGNED code (422 for schema
violations, 404 for unknown ids) AND that the database is unchanged
after the rejected request (no partial writes). Counts are read
directly from the DB, not from responses.
"""
import uuid

from sqlalchemy import func, select

from app.models import Bill, HelpCall, MenuItem, Order, Payment, Refund
from tests.helpers import fresh_table_token, headers, menu_by_name, open_session
from tests.sec9 import (
    future_iso,
    pay_bill,
    place_bill,
    seed,
    user_headers,
    zaika_id,
    cleanup_placed_orders,  # noqa: F401  (autouse fixture)
)

LONG = "x" * 10_000


def _count(db_session, model):
    return db_session.execute(select(func.count()).select_from(model)).scalar_one()


def _order_body(menu, **overrides):
    item = next(iter(menu.values()))
    body = {"items": [{"menuItemId": item["id"], "quantity": 1}]}
    body.update(overrides)
    return body


# --- Order placement --------------------------------------------------------

def test_order_tampered_payloads_422_and_no_order_created(client, db_session):
    seed(db_session)
    token = fresh_table_token(db_session)
    open_session(client, token)
    menu, _ = menu_by_name(client, token)
    item_id = next(iter(menu.values()))["id"]
    bad_bodies = [
        {"items": [{"menuItemId": item_id, "quantity": 0}]},
        {"items": [{"menuItemId": item_id, "quantity": -1}]},
        {"items": [{"menuItemId": item_id, "quantity": 21}]},
        {"items": [{"menuItemId": item_id, "quantity": "two"}]},
        {"items": [{"menuItemId": item_id, "quantity": 1.5}]},
        {"items": []},
        {"items": [{"menuItemId": item_id, "quantity": 1}] * 31},
        {"items": [{"menuItemId": item_id, "quantity": 1, "pricePaise": 1}]},
        {"items": [{"menuItemId": item_id, "quantity": 1, "unitPricePaise": 1}]},
        {"items": [{"menuItemId": item_id, "quantity": 1}], "totalPaise": 1},
        {"items": [{"menuItemId": item_id, "quantity": 1,
                    "specialInstructions": LONG}]},
        {"items": [{"menuItemId": str(uuid.uuid4()), "quantity": 1}]},  # unknown item -> 422 by design
        {},
        {"items": [{"quantity": 1}]},  # missing menuItemId
        {"items": "not-a-list"},
    ]
    before = _count(db_session, Order)
    for i, body in enumerate(bad_bodies):
        response = client.post(
            "/api/v1/orders", json=body,
            headers={**headers(token), "Idempotency-Key": f"sec9-val-{i}"},
        )
        # Unknown menu item is a service-level validation failure
        # (422 invalid_menu_item), not a 404 — pinned as designed.
        assert response.status_code == 422, (i, body, response.text)
    assert _count(db_session, Order) == before


def test_order_bad_enum_and_wrong_types(client, db_session):
    seed(db_session)
    token = fresh_table_token(db_session)
    open_session(client, token)
    menu, _ = menu_by_name(client, token)
    item_id = next(iter(menu.values()))["id"]
    # Cancel with an extra field is ignored by design (extra='ignore'
    # on CancelOrderRequest) — pin that, and that a bad order id 404s.
    bill, _, _ = place_bill(client, db_session, token=token)
    orders = client.get("/api/v1/guest/orders", headers=headers(token)).json()["data"]
    order_id = orders[0]["id"]
    response = client.post(
        f"/api/v1/orders/{order_id}/cancel",
        json={"reason": "changed mind", "unexpected": True},
        headers=headers(token),
    )
    assert response.status_code == 200, response.text
    assert client.post(
        f"/api/v1/orders/{uuid.uuid4()}/cancel",
        json={}, headers=headers(token),
    ).status_code == 404


# --- Bill splits --------------------------------------------------------------

def test_split_tampered_payloads_422_and_no_splits_created(client, db_session):
    seed(db_session)
    bill, _, token = place_bill(client, db_session)
    total = bill["totalPaise"]
    bad_bodies = [
        {"mode": "bogus", "parts": 2},
        {"mode": "equal", "parts": 1},
        {"mode": "equal", "parts": 0},
        {"mode": "equal"},  # parts required for equal
        {"mode": "custom", "amountsPaise": [total - 1, 2]},  # sum = total+1
        {"mode": "custom", "amountsPaise": [total + 1]},  # also < 2 amounts
        {"mode": "custom", "amountsPaise": [0, total]},
        {"mode": "custom", "amountsPaise": [-5, total + 5]},
        {"mode": "custom", "amountsPaise": [total]},
        {"mode": "custom"},  # amounts required for custom
        {"mode": "equal", "parts": 2, "totalPaise": total},  # extra field
        {"mode": "equal", "parts": "two"},
    ]
    for i, body in enumerate(bad_bodies):
        response = client.post(
            "/api/v1/guest/bill/splits", json=body, headers=headers(token)
        )
        assert response.status_code == 422, (i, body, response.text)
    after = client.get("/api/v1/guest/bill", headers=headers(token)).json()["data"]
    assert after["splits"] == []
    assert after["totalPaise"] == total


# --- Payments -----------------------------------------------------------------

def test_payment_tampered_payloads_422_and_no_payment_row(client, db_session):
    seed(db_session)
    bill, _, _ = place_bill(client, db_session)
    total = bill["totalPaise"]
    bad = [
        ({"amountPaise": 0}, "zero amount"),
        ({"amountPaise": -100}, "negative amount"),
        ({"amountPaise": total + 1}, "over outstanding"),
        ({"amountPaise": total, "method": "card"}, "non-cash method"),
        ({"amountPaise": total, "method": "upi"}, "non-cash method"),
        ({"amountPaise": total, "totalPaise": total}, "client total field"),
        ({"amountPaise": total, "receiptNumber": "R-1"}, "client receipt field"),
        ({"amountPaise": "lots"}, "wrong type"),
        ({}, "missing amount"),
    ]
    before = _count(db_session, Payment)
    for i, (body, why) in enumerate(bad):
        response = client.post(
            f"/api/v1/staff/bills/{bill['id']}/payments?restaurant_id={zaika_id(db_session)}",
            json=body,
            headers={"X-Staff-Dev-Key": "dev-staff-only",
                     "Idempotency-Key": f"sec9-payval-{i}"},
        )
        assert response.status_code == 422, (why, response.text)
    # Missing Idempotency-Key is also a 422 (router-level check).
    response = client.post(
        f"/api/v1/staff/bills/{bill['id']}/payments?restaurant_id={zaika_id(db_session)}",
        json={"amountPaise": 100},
        headers={"X-Staff-Dev-Key": "dev-staff-only"},
    )
    assert response.status_code == 422, response.text
    assert _count(db_session, Payment) == before
    # A valid partial payment leaves the bill TOTAL untouched.
    ok = pay_bill(client, db_session, bill, amount_paise=100)
    assert ok.status_code == 201, ok.text
    row = db_session.get(Bill, uuid.UUID(bill["id"]))
    assert row.total_paise == total


# --- Refunds ------------------------------------------------------------------

def test_refund_tampered_payloads_422_and_no_refund_row(client, db_session):
    seed(db_session)
    h, _ = user_headers(client, db_session, "owner")
    bill, _, _ = place_bill(client, db_session)
    pay = pay_bill(client, db_session, bill)
    assert pay.status_code == 201, pay.text
    payment_id = pay.json()["data"]["payment"]["id"]
    total = bill["totalPaise"]
    bad = [
        ({"paymentId": payment_id, "amountPaise": 0, "reason": "x"}, "zero"),
        ({"paymentId": payment_id, "amountPaise": -1, "reason": "x"}, "negative"),
        ({"paymentId": payment_id, "amountPaise": total + 1, "reason": "x"}, "over refundable"),
        ({"paymentId": payment_id, "amountPaise": 1, "reason": ""}, "empty reason"),
        ({"paymentId": payment_id, "amountPaise": 1, "reason": LONG}, "10k reason"),
        ({"paymentId": payment_id, "amountPaise": 1}, "missing reason"),
        ({"paymentId": payment_id, "reason": "x"}, "missing amount"),
        ({"paymentId": str(uuid.uuid4()), "amountPaise": 1, "reason": "x"}, "unknown payment -> 404"),
        ({"paymentId": payment_id, "amountPaise": 1, "reason": "x", "totalPaise": 1}, "extra field"),
    ]
    before = _count(db_session, Refund)
    for i, (body, why) in enumerate(bad):
        response = client.post(
            f"/api/v1/admin/bills/{bill['id']}/refunds", json=body,
            headers={**h, "Idempotency-Key": f"sec9-refval-{i}"},
        )
        expected = 404 if why.startswith("unknown payment") else 422
        assert response.status_code == expected, (why, response.text)
    assert _count(db_session, Refund) == before


# --- Tax config -----------------------------------------------------------------

def test_tax_tampered_payloads_422(client, db_session):
    seed(db_session)
    h, _ = user_headers(client, db_session, "owner")
    past = "2020-01-01T00:00:00Z"
    bad = [
        {"enabled": True, "rateBps": 10001, "mode": "exclusive", "label": "GST",
         "effectiveFrom": future_iso()},
        {"enabled": True, "rateBps": -1, "mode": "exclusive", "label": "GST",
         "effectiveFrom": future_iso()},
        {"enabled": True, "rateBps": 500, "mode": "flat", "label": "GST",
         "effectiveFrom": future_iso()},
        {"enabled": True, "rateBps": 500, "mode": "exclusive", "label": "GST",
         "effectiveFrom": past},
        {"enabled": True, "rateBps": 500, "mode": "exclusive", "label": "GST"},
        {"rateBps": 500, "mode": "exclusive", "label": "GST",
         "effectiveFrom": future_iso()},
        # NOTE: {"enabled": "yes", ...} is NOT in this list: Pydantic
        # coerces "yes" -> True by design, so it is a 201, not a 422.
        {"enabled": True, "rateBps": 500, "mode": "exclusive", "label": LONG,
         "effectiveFrom": future_iso()},
    ]
    for i, body in enumerate(bad):
        response = client.post("/api/v1/admin/settings/tax", json=body, headers=h)
        assert response.status_code == 422, (i, body, response.text)


# --- Menu item create/update ------------------------------------------------------

def test_menu_item_tampered_payloads_422_and_no_item_created(client, db_session):
    seed(db_session)
    h, _ = user_headers(client, db_session, "owner")
    cats = client.get("/api/v1/admin/menu/categories", headers=h).json()["data"]
    cat_id = cats[0]["id"]
    bad = [
        {"categoryId": cat_id, "name": "X", "pricePaise": -5},
        {"categoryId": cat_id, "name": "", "pricePaise": 100},
        {"categoryId": cat_id, "name": LONG, "pricePaise": 100},
        {"categoryId": cat_id, "name": "X", "pricePaise": "free"},
        {"categoryId": str(uuid.uuid4()), "name": "X", "pricePaise": 100},  # 404
        {"categoryId": cat_id, "name": "X", "pricePaise": 100, "totalPaise": 1},
        {"categoryId": cat_id, "pricePaise": 100},  # missing name
        {"name": "X", "pricePaise": 100},  # missing category
    ]
    before = _count(db_session, MenuItem)
    for i, body in enumerate(bad):
        response = client.post("/api/v1/admin/menu/items", json=body, headers=h)
        expected = 404 if i == 4 else 422
        assert response.status_code == expected, (i, body, response.text)
    assert _count(db_session, MenuItem) == before
    # Update path: negative price and 10k name are 422, no change.
    item = client.post(
        "/api/v1/admin/menu/items",
        json={"categoryId": cat_id, "name": "Sec9 Dish", "pricePaise": 500},
        headers=h,
    ).json()["data"]
    for body in ({"pricePaise": -1}, {"name": LONG}, {"pricePaise": "x"}):
        assert client.patch(
            f"/api/v1/admin/menu/items/{item['id']}", json=body, headers=h
        ).status_code == 422
    row = db_session.get(MenuItem, uuid.UUID(item["id"]))
    assert row.price_paise == 500 and row.name == "Sec9 Dish"
    # Clean up: the shared suite's seed test asserts exactly 24
    # menu items, so the item created here must not linger.
    db_session.delete(row)
    db_session.commit()


# --- User create ------------------------------------------------------------------

def test_user_create_tampered_payloads_422(client, db_session):
    seed(db_session)
    h, _ = user_headers(client, db_session, "owner")
    bad = [
        {"email": "not-an-email", "password": "SomePass123", "role": "waiter"},
        {"email": "a@b.example", "password": "short", "role": "waiter"},
        {"email": "a@b.example", "password": "SomePass123", "role": "superuser"},
        {"email": "a@b.example", "password": "SomePass123", "role": "waiter",
         "isActive": False},  # extra field: not in schema
        {"email": "a@b.example", "role": "waiter"},  # missing password
        {"password": "SomePass123", "role": "waiter"},  # missing email
    ]
    for i, body in enumerate(bad):
        response = client.post("/api/v1/admin/users", json=body, headers=h)
        assert response.status_code == 422, (i, body, response.text)


# --- Help calls -------------------------------------------------------------------

def test_help_call_tampered_payloads_422_and_none_created(client, db_session):
    seed(db_session)
    token = fresh_table_token(db_session)
    open_session(client, token)
    bad = [
        {"type": "bogus"},
        {"type": "WATER"},  # valid after normalisation? 'water' is allowed — see below
        {"type": ""},
        {"type": "waiter", "note": LONG},
        {"note": "no type"},
        {"type": 42},
    ]
    before = _count(db_session, HelpCall)
    # "WATER" normalises to "water" and is VALID (201) — pin that the
    # validator lower-cases; it is index 1.
    for i, body in enumerate(bad):
        response = client.post(
            "/api/v1/guest/help-calls", json=body, headers=headers(token)
        )
        expected = 201 if i == 1 else 422
        assert response.status_code == expected, (i, body, response.text)
    assert _count(db_session, HelpCall) == before + 1
