"""Stage 9 — wire-contract tests (SQLite suite).

Pins the guest/staff/kitchen/admin wire shapes FIELD-FOR-FIELD
(response keys, types, statuses, error envelope). These are the
evidence that justifies the hand-maintained frontend types in
frontend/src/shared/api/types.ts (the OpenAPI-generation TODO
is ruled on by the other Stage 9 workstream — no type
generation is implemented here).

Frontend-type gaps found while writing these tests (NOT fixed
here — recorded for the parent):
1. frontend `Bill` has no `voidedAt`/`voidReason`, which the
   backend sends on every bill.
2. frontend `Receipt` has no `taxRateBpsSnapshot` /
   `taxModeSnapshot` / `taxLabelSnapshot`, which the backend
   sends on every receipt.
3. frontend `StaffBillSummary` is the staff-bills LIST-row shape
   (tableId/tableNumber/tableSessionId/orderCount), but the same
   type is used for the table-detail `openBill`, whose backend
   shape has none of those four fields.
4. frontend `StaffTableDetail` declares `session`, which the
   backend NEVER sends — TableDetail.tsx therefore always
   renders "No active session — the table is free", even for an
   occupied table (the data IS available as
   table.sessionId/sessionOpenedAt). Fixed in the frontend step
   of this stage; see test_staff_table_detail_contract below.
5. frontend `StaffHelpCallListResponse` has no `pagination`,
   which the backend sends.
"""
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.core.security import hash_password
from app.models import Restaurant, User
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


def _find_kitchen_order(client, setup, order_id):
    """Find one order by paging the kitchen list (per_page=100).

    The shared test DB accumulates >100 active orders in a full
    run, so a just-placed order is not on page 1 — same reason as
    test_kitchen._all_kitchen_orders. Returns None if absent.
    """
    page = 1
    while True:
        body = client.get(
            f"/api/v1/kitchen/orders?restaurant_id={setup['restaurant_id']}"
            f"&page={page}&per_page=100",
            headers=_kitchen_headers(),
        ).json()
        for o in body["data"]:
            if o["id"] == order_id:
                return o
        if page >= body["pagination"]["total_pages"]:
            return None
        page += 1


@pytest.fixture
def setup(client, db_session):
    seed(db_session)
    token = fresh_table_token(db_session)
    session_resp = open_session(client, token)
    by_name, _body = menu_by_name(client, token)
    restaurant = (
        db_session.execute(select(Restaurant).where(Restaurant.slug == "zaika"))
        .scalars()
        .first()
    )
    return {
        "token": token,
        "table_id": session_resp["session"]["tableId"],
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


def _assert_keys(obj, expected, where):
    assert set(obj.keys()) == set(expected), (
        f"{where}: got {sorted(obj.keys())}, want {sorted(expected)}")


# ----------------------------------------------------------------------
# Error envelope (every error, same shape)
# ----------------------------------------------------------------------


def test_error_envelope_shape(client, db_session, setup):
    response = client.get("/api/v1/guest/menu", headers=headers("nope"))
    assert response.status_code == 401
    _assert_keys(response.json(), ["error"], "envelope")
    _assert_keys(response.json()["error"], ["code", "message", "details"], "error")
    assert response.json()["error"]["code"] == "invalid_table_token"
    assert isinstance(response.json()["error"]["details"], dict)

    unknown = client.get("/api/v1/definitely-not-a-route")
    assert unknown.status_code == 404
    assert unknown.json()["error"]["code"] == "not_found"


# ----------------------------------------------------------------------
# Guest
# ----------------------------------------------------------------------


def test_guest_session_contract(client, db_session, setup):
    response = client.get(f"/api/v1/guest/t/{setup['token']}")
    assert response.status_code == 200
    _assert_keys(response.json(),
                 ["session", "restaurant", "table", "menuUrl"], "session")
    _assert_keys(response.json()["session"],
                 ["id", "restaurantId", "tableId", "tableNumber",
                  "status", "openedAt"], "session.session")
    _assert_keys(response.json()["restaurant"],
                 ["id", "name", "tagline", "cuisine", "address",
                  "openingHours", "phone", "rating"], "session.restaurant")
    _assert_keys(response.json()["table"], ["number"], "session.table")
    assert isinstance(response.json()["menuUrl"], str)


def test_guest_menu_contract(client, db_session, setup):
    response = client.get("/api/v1/guest/menu", headers=headers(setup["token"]))
    assert response.status_code == 200
    _assert_keys(response.json(), ["categories", "items"], "menu")
    assert len(response.json()["items"]) == 24
    category = response.json()["categories"][0]
    _assert_keys(category,
                 ["id", "name", "description", "sortOrder", "isActive"],
                 "menu.category")
    item = response.json()["items"][0]
    _assert_keys(item,
                 ["id", "categoryId", "name", "description", "pricePaise",
                  "isVeg", "isAvailable", "isPopular", "imageUrl", "station",
                  "variantGroupLabel", "variants", "addOns"], "menu.item")
    assert isinstance(item["pricePaise"], int)
    if item["variants"]:
        _assert_keys(item["variants"][0],
                     ["id", "name", "priceDeltaPaise", "isAvailable"],
                     "menu.variant")
    if item["addOns"]:
        _assert_keys(item["addOns"][0],
                     ["id", "name", "pricePaise", "isAvailable"], "menu.addOn")


def test_guest_order_contract(client, db_session, setup):
    order = _place(client, setup, f"ct-{uuid.uuid4().hex}")
    # Placement response wraps the order bare (not under `data`).
    response = client.post(
        "/api/v1/orders",
        json={"items": [{"menuItemId": setup["menu"]["Butter Naan"]["id"],
                         "quantity": 1}]},
        headers={**headers(setup["token"]),
                 "Idempotency-Key": f"ct-{uuid.uuid4().hex}"},
    )
    assert response.status_code == 201
    _assert_keys(response.json(), ["order"], "order placement")
    placed = response.json()["order"]
    _assert_keys(placed,
                 ["id", "orderNumber", "tableSessionId", "status", "items",
                  "subtotalPaise", "taxPaise", "totalPaise", "placedAt",
                  "acceptedAt", "readyAt", "servedAt", "cancelledAt"],
                 "order")
    assert placed["status"] == "placed"
    assert placed["acceptedAt"] is None
    _assert_keys(placed["items"][0],
                 ["id", "menuItemId", "name", "variantName", "addOnNames",
                  "quantity", "unitPricePaise", "totalPaise",
                  "specialInstructions", "status"], "order item")

    listing = client.get("/api/v1/guest/orders", headers=headers(setup["token"]))
    assert listing.status_code == 200
    _assert_keys(listing.json(), ["data", "pagination"], "order list")
    _assert_keys(listing.json()["pagination"],
                 ["page", "per_page", "total", "total_pages"],
                 "pagination (snake_case by design)")
    assert {o["id"] for o in listing.json()["data"]} == {
        order["id"], placed["id"]}


def test_guest_bill_and_receipt_contract(client, db_session, setup):
    order = _place(client, setup, f"ct-{uuid.uuid4().hex}")
    bill = client.post(
        "/api/v1/guest/bill", headers=headers(setup["token"])).json()["data"]
    _assert_keys(bill,
                 ["id", "billNumber", "tableSessionId", "tableId",
                  "tableNumber", "status", "items", "splits", "payments",
                  "orderNumbers", "subtotalPaise", "taxPaise",
                  "serviceChargePaise", "discountPaise", "totalPaise",
                  "amountPaidPaise", "outstandingPaise", "issuedAt",
                  "paymentRequestedAt", "paidAt", "version", "voidedAt",
                  "voidReason", "taxRateBpsSnapshot", "taxModeSnapshot",
                  "taxLabelSnapshot"], "bill")
    assert bill["status"] == "issued"
    assert bill["orderNumbers"] == [order["orderNumber"]]
    _assert_keys(bill["items"][0],
                 ["id", "orderId", "orderItemId", "name", "variantName",
                  "addOnNames", "quantity", "unitPricePaise", "totalPaise"],
                 "bill item")

    pay = client.post(
        f"/api/v1/staff/bills/{bill['id']}/payments"
        f"?restaurant_id={setup['restaurant_id']}",
        json={"amountPaise": bill["totalPaise"]},
        headers={**_staff_headers(), "Idempotency-Key": f"ct-{uuid.uuid4().hex}"},
    )
    assert pay.status_code == 201, pay.text
    _assert_keys(pay.json(), ["data"], "payment response")
    _assert_keys(pay.json()["data"], ["payment", "bill", "receipt"],
                 "payment data")
    payment = pay.json()["data"]["payment"]
    _assert_keys(payment,
                 ["id", "billId", "billSplitId", "method", "amountPaise",
                  "changeDuePaise", "collectedBy", "collectedAt",
                  "receiptNumber", "status", "note"], "payment")
    assert payment["method"] == "cash"
    assert payment["status"] == "confirmed"

    receipt = client.get(
        "/api/v1/guest/bill/receipt", headers=headers(setup["token"]))
    assert receipt.status_code == 200
    _assert_keys(receipt.json(), ["data"], "receipt response")
    _assert_keys(receipt.json()["data"],
                 ["restaurantName", "branchName", "billNumber",
                  "receiptNumber", "receiptNumbers", "tableNumber",
                  "orderNumbers", "items", "subtotalPaise", "taxPaise",
                  "taxStatus", "taxRateBpsSnapshot", "taxModeSnapshot",
                  "taxLabelSnapshot", "totalPaise", "amountReceivedPaise",
                  "outstandingPaise", "paymentMethod", "paymentTimestamps",
                  "receiptTimestamp", "billId"], "receipt")
    assert receipt.json()["data"]["paymentMethod"] == "Cash"


def test_guest_help_call_contract(client, db_session, setup):
    response = client.post(
        "/api/v1/guest/help-calls",
        json={"type": "waiter", "note": "Water please"},
        headers=headers(setup["token"]),
    )
    assert response.status_code == 201
    _assert_keys(response.json(), ["data"], "help call response")
    _assert_keys(response.json()["data"],
                 ["id", "tableSessionId", "type", "note", "status",
                  "createdAt"], "help call")
    assert response.json()["data"]["status"] == "open"


# ----------------------------------------------------------------------
# Kitchen
# ----------------------------------------------------------------------


def test_kitchen_orders_contract(client, db_session, setup):
    order = _place(client, setup, f"ct-{uuid.uuid4().hex}")
    response = client.get(
        f"/api/v1/kitchen/orders?restaurant_id={setup['restaurant_id']}",
        headers=_kitchen_headers(),
    )
    assert response.status_code == 200
    _assert_keys(response.json(), ["data", "pagination"], "kitchen list")
    # Page through: the shared test DB accumulates >100 active
    # orders in a full run, so a just-placed order is not on
    # page 1 (same reason as test_kitchen._all_kitchen_orders).
    row = _find_kitchen_order(client, setup, order["id"])
    assert row is not None
    _assert_keys(row,
                 ["id", "orderNumber", "tableSessionId", "status", "items",
                  "subtotalPaise", "taxPaise", "totalPaise", "placedAt",
                  "acceptedAt", "readyAt", "servedAt", "cancelledAt",
                  "tableNumber", "elapsedSeconds"], "kitchen order")
    assert isinstance(row["elapsedSeconds"], int)
    assert row["elapsedSeconds"] >= 0


# ----------------------------------------------------------------------
# Staff
# ----------------------------------------------------------------------


def test_staff_floor_and_table_detail_contract(client, db_session, setup):
    order = _place(client, setup, f"ct-{uuid.uuid4().hex}")
    bill = client.post(
        "/api/v1/guest/bill", headers=headers(setup["token"])).json()["data"]

    floor = client.get(
        f"/api/v1/staff/tables?restaurant_id={setup['restaurant_id']}",
        headers=_staff_headers(),
    )
    assert floor.status_code == 200
    _assert_keys(floor.json(), ["data"], "staff floor")
    # This test's fresh table is the one with the live session.
    rows = floor.json()["data"]
    assert len(rows) >= 10  # fresh tables accumulate in the shared DB
    row = next(t for t in rows if t["id"] == setup["table_id"])
    assert row["activeOrderCount"] == 1
    assert row["latestOrderStatus"] == "placed"
    _assert_keys(row,
                 ["id", "number", "status", "sessionId", "sessionOpenedAt",
                  "activeOrderCount", "readyOrderCount",
                  "latestOrderStatus", "hasOpenHelpCall"], "staff table")

    # The detail of the table the order sits on.
    detail = client.get(
        f"/api/v1/staff/tables/{row['id']}?restaurant_id={setup['restaurant_id']}",
        headers=_staff_headers(),
    )
    assert detail.status_code == 200
    _assert_keys(detail.json(), ["data"], "staff table detail response")
    _assert_keys(detail.json()["data"],
                 ["table", "orders", "helpCalls", "openBill"],
                 "staff table detail")
    assert "session" not in detail.json()["data"], (
        "backend does not send `session` on table detail — the "
        "frontend type claims it (see module docstring, gap 4)")
    open_bill = detail.json()["data"]["openBill"]
    assert open_bill is not None
    _assert_keys(open_bill,
                 ["id", "billNumber", "status", "totalPaise",
                  "amountPaidPaise", "outstandingPaise", "issuedAt",
                  "paymentRequestedAt"], "staff open-bill summary")
    assert "orderCount" not in open_bill and "tableNumber" not in open_bill, (
        "open-bill summary has no orderCount/tableNumber — the "
        "frontend StaffBillSummary type claims them (gap 3)")


def test_staff_bills_contract(client, db_session, setup):
    _place(client, setup, f"ct-{uuid.uuid4().hex}")
    bill = client.post(
        "/api/v1/guest/bill", headers=headers(setup["token"])).json()["data"]
    response = client.get(
        f"/api/v1/staff/bills?restaurant_id={setup['restaurant_id']}",
        headers=_staff_headers(),
    )
    assert response.status_code == 200
    _assert_keys(response.json(), ["data", "pagination"], "staff bills")
    row = next(b for b in response.json()["data"] if b["id"] == bill["id"])
    _assert_keys(row,
                 ["id", "billNumber", "tableId", "tableNumber",
                  "tableSessionId", "status", "totalPaise",
                  "amountPaidPaise", "outstandingPaise", "orderCount",
                  "issuedAt", "paymentRequestedAt"], "staff bill row")


# ----------------------------------------------------------------------
# Admin
# ----------------------------------------------------------------------


def test_admin_overview_bills_cash_contract(client, db_session, setup):
    owner_h = _owner_headers(client, db_session, setup)
    # Delta-based: the shared SQLite DB accumulates other tests'
    # bills/payments, so assert the CHANGE this test causes.
    before = client.get(
        "/api/v1/admin/dashboard/overview", headers=owner_h).json()["data"]
    today = datetime.now(timezone.utc).date().isoformat()
    cash_before = client.get(
        f"/api/v1/admin/reports/daily-cash?date={today}",
        headers=owner_h).json()["data"]
    order = _place(client, setup, f"ct-{uuid.uuid4().hex}")
    bill = client.post(
        "/api/v1/guest/bill", headers=headers(setup["token"])).json()["data"]
    pay = client.post(
        f"/api/v1/staff/bills/{bill['id']}/payments"
        f"?restaurant_id={setup['restaurant_id']}",
        json={"amountPaise": bill["totalPaise"]},
        headers={**_staff_headers(), "Idempotency-Key": f"ct-{uuid.uuid4().hex}"},
    )
    assert pay.status_code == 201, pay.text

    overview = client.get("/api/v1/admin/dashboard/overview", headers=owner_h)
    assert overview.status_code == 200, overview.text
    _assert_keys(overview.json(), ["data"], "admin overview response")
    data = overview.json()["data"]
    _assert_keys(data,
                 ["restaurantId", "branchId", "periodStart", "periodEnd",
                  "preset", "timezone", "grossSalesPaise",
                  "cashCollectedPaise", "refundsPaise", "netCashPaise",
                  "outstandingPaise", "billCount", "orderCount",
                  "averageOrderValuePaise", "ordersByStatus",
                  "recentPayments", "recentActivity", "bestSellers"],
                 "admin overview")
    assert data["billCount"] == before["billCount"] + 1
    assert data["orderCount"] == before["orderCount"] + 1
    assert data["cashCollectedPaise"] == before["cashCollectedPaise"] + bill["totalPaise"]
    assert data["grossSalesPaise"] == before["grossSalesPaise"] + bill["totalPaise"]
    if data["recentPayments"]:
        _assert_keys(data["recentPayments"][0],
                     ["id", "billId", "billNumber", "tableNumber",
                      "amountPaise", "receiptNumber", "collectedAt",
                      "collectedByName"], "overview recent payment")
    if data["ordersByStatus"]:
        _assert_keys(data["ordersByStatus"][0], ["status", "count"],
                     "overview orders by status")
    if data["recentActivity"]:
        _assert_keys(data["recentActivity"][0],
                     ["id", "action", "entityType", "entityId", "details",
                      "createdAt"], "overview recent activity")
    if data["bestSellers"]:
        _assert_keys(data["bestSellers"][0],
                     ["menuItemId", "name", "quantity", "revenuePaise"],
                     "overview best seller")

    bills = client.get("/api/v1/admin/bills", headers=owner_h)
    assert bills.status_code == 200
    _assert_keys(bills.json(), ["data", "pagination"], "admin bills")
    row = next(b for b in bills.json()["data"] if b["id"] == bill["id"])
    _assert_keys(row,
                 ["id", "billNumber", "tableNumber", "status", "totalPaise",
                  "amountPaidPaise", "collectedPaise", "outstandingPaise",
                  "refundPaise", "netCollectedPaise", "issuedAt", "paidAt",
                  "voidedAt", "voidReason"], "admin bill row")
    assert row["status"] == "paid"
    assert row["collectedPaise"] == bill["totalPaise"]

    cash = client.get(
        f"/api/v1/admin/reports/daily-cash?date={today}", headers=owner_h)
    assert cash.status_code == 200, cash.text
    _assert_keys(cash.json(), ["data"], "daily cash response")
    _assert_keys(cash.json()["data"],
                 ["date", "timezone", "periodStart", "periodEnd",
                  "grossCashCollectedPaise", "cashRefundsPaise",
                  "netCashPaise", "outstandingPaise", "billCount",
                  "paymentCount", "refundCount"], "daily cash")
    assert cash.json()["data"]["paymentCount"] == cash_before["paymentCount"] + 1
    assert cash.json()["data"]["grossCashCollectedPaise"] == (
        cash_before["grossCashCollectedPaise"] + bill["totalPaise"])
