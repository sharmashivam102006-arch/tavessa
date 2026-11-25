"""Stage 9 — NO CLIENT-SUPPLIED PRICE (item 9).

Grep map (verified against backend/app/schemas/*.py):
- guest OrderPlacementItemIn / OrderPlacementRequest: NO price,
  total, or amount field at all; extra='forbid' -> any such field
  is a 422 (schemas/guest.py).
- SplitCreateRequest: amounts_paise IS client-chosen, but it is a
  split ALLOCATION of the server total, not a price — the service
  rejects any sum != server total (422 split_sum_mismatch).
- PaymentCreateRequest: amount_paise is the cash amount handed
  over; it can never set the total — the service rejects amount >
  outstanding (422 amount_exceeds_due) and the bill total is
  server-computed.
- RefundCreateRequest: amount_paise is bounded by the refundable
  amount (service 422 when exceeded).
- MenuItemCreateRequest / VariantCreateRequest / AddOnCreateRequest:
  price_paise / price_delta_paise ARE client-supplied — LEGITIMATE:
  only owner/manager (manage_menu) may set menu prices, and the
  values feed future orders only (snapshots protect old bills).
- TaxConfigCreateRequest.rate_bps: owner-only setting, legitimate.
Each guest-facing case below has a live negative test.
"""
import uuid

from app.models import MenuItem
from tests.helpers import headers, make_token, menu_by_name, open_session
from tests.sec9 import cleanup_placed_orders, pay_bill, place_bill, seed, user_headers, zaika_id  # noqa: F401  (cleanup_placed_orders is autouse)


def test_order_total_is_server_computed_not_client_supplied(client, db_session):
    seed(db_session)
    token = make_token(db_session, 5)
    open_session(client, token)
    menu, _ = menu_by_name(client, token)
    item = menu["Butter Naan"]
    unit = item["pricePaise"]
    # Quantity 2, no price anywhere in the request: total must be
    # exactly 2x the server menu price.
    response = client.post(
        "/api/v1/orders",
        json={"items": [{"menuItemId": item["id"], "quantity": 2}]},
        headers={**headers(token), "Idempotency-Key": f"sec9-noprice-{uuid.uuid4().hex}"},
    )
    assert response.status_code == 201, response.text
    order = response.json()["order"]
    assert order["items"][0]["unitPricePaise"] == unit
    assert order["totalPaise"] == 2 * unit
    assert order["subtotalPaise"] == 2 * unit


def test_order_rejects_every_price_field_variant(client, db_session):
    seed(db_session)
    token = make_token(db_session, 5)
    open_session(client, token)
    menu, _ = menu_by_name(client, token)
    item = next(iter(menu.values()))
    for extra in (
        {"pricePaise": 1},
        {"unitPricePaise": 1},
        {"totalPaise": 1},
        {"amountPaise": 1},
        {"price": 1},
    ):
        line = {"menuItemId": item["id"], "quantity": 1, **extra}
        response = client.post(
            "/api/v1/orders",
            json={"items": [line]},
            headers={**headers(token), "Idempotency-Key": f"sec9-np-{uuid.uuid4().hex}"},
        )
        assert response.status_code == 422, (extra, response.text)
    # A top-level total is rejected too.
    response = client.post(
        "/api/v1/orders",
        json={"items": [{"menuItemId": item["id"], "quantity": 1}], "totalPaise": 1},
        headers={**headers(token), "Idempotency-Key": f"sec9-np-{uuid.uuid4().hex}"},
    )
    assert response.status_code == 422, response.text


def test_split_amounts_must_sum_to_server_total(client, db_session):
    seed(db_session)
    bill, _, token = place_bill(client, db_session)
    total = bill["totalPaise"]
    # One paise short / over: 422, and no splits were stored.
    for amounts in ([total - 2, 1], [total + 1], [total - 1], [1, 1, 1]):
        response = client.post(
            "/api/v1/guest/bill/splits",
            json={"mode": "custom", "amountsPaise": amounts},
            headers=headers(token),
        )
        assert response.status_code == 422, (amounts, response.text)
    after = client.get("/api/v1/guest/bill", headers=headers(token)).json()["data"]
    assert after["splits"] == []
    # An exact-sum custom split is accepted — the amounts are an
    # allocation, and the total is still the server's.
    ok = client.post(
        "/api/v1/guest/bill/splits",
        json={"mode": "custom", "amountsPaise": [total - 1, 1]},
        headers=headers(token),
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["data"]["totalPaise"] == total
    assert sum(s["amountPaise"] for s in ok.json()["data"]["splits"]) == total


def test_payment_amount_never_sets_the_total(client, db_session):
    seed(db_session)
    bill, _, _ = place_bill(client, db_session)
    total = bill["totalPaise"]
    # Overpay: 422.
    response = pay_bill(client, db_session, bill, amount_paise=total + 1)
    assert response.status_code == 422, response.text
    # Underpay (partial): accepted, bill total unchanged, only the
    # paid/outstanding figures move.
    ok = pay_bill(client, db_session, bill, amount_paise=total - 1)
    assert ok.status_code == 201, ok.text
    data = ok.json()["data"]
    assert data["bill"]["totalPaise"] == total
    assert data["bill"]["amountPaidPaise"] == total - 1
    assert data["bill"]["outstandingPaise"] == 1
    assert data["payment"]["amountPaise"] == total - 1


def test_refund_amount_bounded_by_refundable(client, db_session):
    seed(db_session)
    h, _ = user_headers(client, db_session, "owner")
    bill, _, _ = place_bill(client, db_session)
    pay = pay_bill(client, db_session, bill)
    assert pay.status_code == 201, pay.text
    payment_id = pay.json()["data"]["payment"]["id"]
    total = bill["totalPaise"]
    # Refund above what was paid: 422.
    response = client.post(
        f"/api/v1/admin/bills/{bill['id']}/refunds",
        json={"paymentId": payment_id, "amountPaise": total + 1, "reason": "x"},
        headers={**h, "Idempotency-Key": f"sec9-npr-{uuid.uuid4().hex}"},
    )
    assert response.status_code == 422, response.text
    # A refund of exactly the paid amount succeeds and never changes
    # the bill total (only its status).
    ok = client.post(
        f"/api/v1/admin/bills/{bill['id']}/refunds",
        json={"paymentId": payment_id, "amountPaise": total, "reason": "full"},
        headers={**h, "Idempotency-Key": f"sec9-npr-{uuid.uuid4().hex}"},
    )
    assert ok.status_code == 201, ok.text
    row = client.get(f"/api/v1/admin/bills/{bill['id']}", headers=h).json()["data"]
    assert row["totalPaise"] == total
    assert row["status"] == "refunded"


def test_admin_set_menu_price_is_the_only_price_input(client, db_session):
    """The legitimate exception: an owner CAN set a menu price — and
    that price then flows into server-side order totals, while a
    guest still cannot. (Old bills keep their snapshot.)"""
    seed(db_session)
    h, _ = user_headers(client, db_session, "owner")
    cats = client.get("/api/v1/admin/menu/categories", headers=h).json()["data"]
    item = client.post(
        "/api/v1/admin/menu/items",
        json={"categoryId": cats[0]["id"], "name": "Sec9 Priced Dish",
              "pricePaise": 12345},
        headers=h,
    )
    assert item.status_code == 201, item.text
    item_id = item.json()["data"]["id"]
    # A waiter cannot set prices at all (403) — the price-setting
    # power is role-gated, not merely schema-gated.
    wh, _ = user_headers(client, db_session, "waiter")
    assert client.post(
        "/api/v1/admin/menu/items",
        json={"categoryId": cats[0]["id"], "name": "Nope", "pricePaise": 1},
        headers=wh,
    ).status_code == 403
    # The new item's server price is what a guest order charges.
    token = make_token(db_session, 5)
    open_session(client, token)
    placed = client.post(
        "/api/v1/orders",
        json={"items": [{"menuItemId": item_id, "quantity": 1}]},
        headers={**headers(token), "Idempotency-Key": f"sec9-np2-{uuid.uuid4().hex}"},
    )
    assert placed.status_code == 201, placed.text
    assert placed.json()["order"]["totalPaise"] == 12345
    # Clean up: the shared suite's seed test asserts exactly 24
    # menu items, so the item created here must not linger.
    db_session.delete(db_session.get(MenuItem, uuid.UUID(item_id)))
    db_session.commit()
