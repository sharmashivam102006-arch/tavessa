"""Orders — placement, pricing, idempotency, cancel (Stage 3)."""
import uuid

import pytest
from sqlalchemy import select

from app.models import Order
from tests.helpers import (
    fresh_table_token,
    headers,
    make_token,
    menu_by_name,
    open_session,
    seed,
)


@pytest.fixture
def setup(client, db_session):
    seed(db_session)
    token = fresh_table_token(db_session)
    open_session(client, token)
    by_name, _body = menu_by_name(client, token)
    return {"token": token, "menu": by_name}


def _place(client, token, items, key):
    return client.post(
        "/api/v1/orders",
        json={"items": items},
        headers={**headers(token), "Idempotency-Key": key},
    )


def test_happy_path_exact_paise_math(client, db_session, setup):
    butter = setup["menu"]["Old Delhi Butter Chicken"]
    tikka = setup["menu"]["Paneer Tikka Angara"]
    half = next(v for v in butter["variants"] if v["name"] == "Half")
    gravy = next(a for a in butter["addOns"] if a["name"] == "Extra gravy")
    medium = next(v for v in tikka["variants"] if v["name"] == "Medium")
    chutney = next(a for a in tikka["addOns"] if a["name"] == "Extra mint chutney")
    response = _place(client, setup["token"], [
        {"menuItemId": butter["id"], "variantId": half["id"],
         "addOnIds": [gravy["id"]], "quantity": 2},
        {"menuItemId": tikka["id"], "variantId": medium["id"],
         "addOnIds": [chutney["id"]], "quantity": 1,
         "specialInstructions": "less spicy"},
    ], key="happy-path-1")
    assert response.status_code == 201, response.text
    order = response.json()["order"]
    # Butter chicken Half: 34900 - 10000 + 5000 (gravy) = 29900 x 2 = 59800
    # Paneer tikka Medium: 24900 + 0 + 2000 (chutney) = 26900 x 1 = 26900
    assert order["orderNumber"].startswith("ZAIKA-")
    assert order["status"] == "placed"
    assert order["subtotalPaise"] == 59800 + 26900
    assert order["taxPaise"] == 0
    assert order["totalPaise"] == 86700
    assert order["tableSessionId"]
    assert order["placedAt"]
    line1, line2 = order["items"]
    assert line1["name"] == "Old Delhi Butter Chicken"
    assert line1["variantName"] == "Half"
    assert line1["addOnNames"] == ["Extra gravy"]
    assert line1["unitPricePaise"] == 29900
    assert line1["totalPaise"] == 59800
    assert line1["status"] == "queued"
    assert line2["specialInstructions"] == "less spicy"
    assert line2["unitPricePaise"] == 26900


def test_order_number_increments(client, db_session, setup):
    naan = setup["menu"]["Butter Naan"]
    first = _place(client, setup["token"],
                   [{"menuItemId": naan["id"], "quantity": 1}], key="num-1")
    second = _place(client, setup["token"],
                    [{"menuItemId": naan["id"], "quantity": 1}], key="num-2")
    assert first.json()["order"]["orderNumber"] != second.json()["order"]["orderNumber"]
    assert first.json()["order"]["orderNumber"].startswith("ZAIKA-")
    assert second.json()["order"]["orderNumber"].startswith("ZAIKA-")


def test_tampered_price_fields_rejected(client, db_session, setup):
    naan = setup["menu"]["Butter Naan"]
    response = client.post(
        "/api/v1/orders",
        json={"items": [{"menuItemId": naan["id"], "quantity": 1,
                         "pricePaise": 100, "unitPricePaise": 100}],
              "totalPaise": 100},
        headers={**headers(setup["token"]), "Idempotency-Key": "tamper-1"},
    )
    assert response.status_code == 422, response.text


def test_invalid_quantities(client, db_session, setup):
    naan = setup["menu"]["Butter Naan"]
    for qty in (0, 21):
        response = _place(client, setup["token"],
                          [{"menuItemId": naan["id"], "quantity": qty}],
                          key=f"qty-{qty}")
        assert response.status_code == 422, response.text
    # 31 lines exceeds the 30-line max.
    response = _place(client, setup["token"],
                      [{"menuItemId": naan["id"], "quantity": 1}] * 31,
                      key="too-many-lines")
    assert response.status_code == 422


def test_invalid_variant_and_add_on(client, db_session, setup):
    naan = setup["menu"]["Butter Naan"]
    butter = setup["menu"]["Old Delhi Butter Chicken"]
    half = next(v for v in butter["variants"] if v["name"] == "Half")
    gravy = next(a for a in butter["addOns"] if a["name"] == "Extra gravy")
    # Variant from another item.
    response = _place(client, setup["token"],
                      [{"menuItemId": naan["id"], "variantId": half["id"], "quantity": 1}],
                      key="variant-wrong-item")
    assert response.status_code == 422
    # Unknown variant id.
    response = _place(client, setup["token"],
                      [{"menuItemId": naan["id"], "variantId": str(uuid.uuid4()), "quantity": 1}],
                      key="variant-unknown")
    assert response.status_code == 422
    # Add-on from another item.
    response = _place(client, setup["token"],
                      [{"menuItemId": naan["id"], "addOnIds": [gravy["id"]], "quantity": 1}],
                      key="addon-wrong-item")
    assert response.status_code == 422
    # Unknown add-on id.
    response = _place(client, setup["token"],
                      [{"menuItemId": naan["id"], "addOnIds": [str(uuid.uuid4())], "quantity": 1}],
                      key="addon-unknown")
    assert response.status_code == 422
    # Unknown menu item.
    response = _place(client, setup["token"],
                      [{"menuItemId": str(uuid.uuid4()), "quantity": 1}],
                      key="item-unknown")
    assert response.status_code == 422


def test_unavailable_item_rejected(client, db_session, setup):
    kebab = setup["menu"]["Hara Bhara Kebab"]
    response = _place(client, setup["token"],
                      [{"menuItemId": kebab["id"], "quantity": 1}],
                      key="unavailable-1")
    assert response.status_code == 422


def test_missing_idempotency_key_422(client, db_session, setup):
    naan = setup["menu"]["Butter Naan"]
    response = client.post(
        "/api/v1/orders",
        json={"items": [{"menuItemId": naan["id"], "quantity": 1}]},
        headers=headers(setup["token"]),
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "idempotency_key_required"


def test_duplicate_submission_same_key_one_order(client, db_session, setup):
    naan = setup["menu"]["Butter Naan"]
    body = {"items": [{"menuItemId": naan["id"], "quantity": 2}]}
    first = client.post("/api/v1/orders", json=body,
                        headers={**headers(setup["token"]), "Idempotency-Key": "dup-1"})
    second = client.post("/api/v1/orders", json=body,
                         headers={**headers(setup["token"]), "Idempotency-Key": "dup-1"})
    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json() == second.json()
    assert second.headers.get("Idempotency-Replayed") == "true"
    assert "Idempotency-Replayed" not in first.headers
    # Exactly one order exists for this session with that order number.
    orders = client.get("/api/v1/guest/orders", headers=headers(setup["token"])).json()
    assert orders["pagination"]["total"] == 1
    assert orders["data"][0]["id"] == first.json()["order"]["id"]


def test_same_key_different_body_422(client, db_session, setup):
    naan = setup["menu"]["Butter Naan"]
    chai = setup["menu"]["Masala Chai"]
    _place(client, setup["token"],
           [{"menuItemId": naan["id"], "quantity": 1}], key="reuse-1")
    response = _place(client, setup["token"],
                      [{"menuItemId": chai["id"], "quantity": 1}], key="reuse-1")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "idempotency_key_reused"


def test_guest_orders_list_and_get(client, db_session, setup):
    naan = setup["menu"]["Butter Naan"]
    placed = _place(client, setup["token"],
                    [{"menuItemId": naan["id"], "quantity": 1}], key="list-1")
    order_id = placed.json()["order"]["id"]
    response = client.get("/api/v1/guest/orders", headers=headers(setup["token"]))
    assert response.status_code == 200
    body = response.json()
    assert body["pagination"] == {"page": 1, "per_page": 20, "total": 1, "total_pages": 1}
    assert len(body["data"]) == 1
    assert body["data"][0]["id"] == order_id
    response = client.get(f"/api/v1/guest/orders/{order_id}", headers=headers(setup["token"]))
    assert response.status_code == 200
    assert response.json()["data"]["id"] == order_id
    response = client.get(f"/api/v1/guest/orders/{uuid.uuid4()}", headers=headers(setup["token"]))
    assert response.status_code == 404


def test_cancel_while_placed(client, db_session, setup):
    naan = setup["menu"]["Butter Naan"]
    placed = _place(client, setup["token"],
                    [{"menuItemId": naan["id"], "quantity": 1}], key="cancel-1")
    order_id = placed.json()["order"]["id"]
    response = client.post(f"/api/v1/orders/{order_id}/cancel",
                           json={"reason": "changed my mind"},
                           headers=headers(setup["token"]))
    assert response.status_code == 200, response.text
    order = response.json()["data"]
    assert order["status"] == "cancelled"
    assert order["cancelledAt"]
    assert all(i["status"] == "cancelled" for i in order["items"])
    # Cancelling again is a conflict.
    response = client.post(f"/api/v1/orders/{order_id}/cancel", json={},
                           headers=headers(setup["token"]))
    assert response.status_code == 409


def test_cancel_after_accepted_409(client, db_session, setup):
    naan = setup["menu"]["Butter Naan"]
    placed = _place(client, setup["token"],
                    [{"menuItemId": naan["id"], "quantity": 1}], key="cancel-2")
    order_id = placed.json()["order"]["id"]
    order = db_session.get(Order, uuid.UUID(order_id))
    order.status = "accepted"  # simulate kitchen acceptance (no endpoint yet)
    db_session.commit()
    response = client.post(f"/api/v1/orders/{order_id}/cancel", json={},
                           headers=headers(setup["token"]))
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "conflict"
