"""Kitchen Display System — Stage 4.

SQLite suite (shared in-memory fixture). Covers: the dev-only gate
(401/403/503, guest token grants nothing), list shape + oldest-first
+ status filter + pagination, order-level transitions (order + items
move together, timestamps, version), illegal transitions -> 409 with
state unchanged, unknown/wrong-restaurant -> 404, cross-restaurant
isolation, guest orders appearing in the queue, and WebSocket event
delivery after commit (order.created / order.status_changed) with NO
event after a failed transaction or a replayed placement.

Postgres-only behaviour (row-lock serialisation of concurrent
PATCHes) lives in tests/test_kitchen_concurrency.py.
"""
import uuid

import pytest
from starlette.websockets import WebSocketDisconnect
from sqlalchemy import select

from app.core.config import settings
from app.models import Branch, DiningTable, Order, Restaurant
from tests.helpers import (
    fresh_table_token,
    headers,
    make_token,
    menu_by_name,
    open_session,
    seed,
)

DEV_KEY = "dev-kitchen-only"


def kitchen_headers(key=DEV_KEY):
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
    }


def _place(client, token, items, key):
    return client.post(
        "/api/v1/orders",
        json={"items": items},
        headers={**headers(token), "Idempotency-Key": key},
    )


def _place_one(client, setup, key, name="Butter Naan"):
    item = setup["menu"][name]
    response = _place(
        client, setup["token"], [{"menuItemId": item["id"], "quantity": 1}], key
    )
    assert response.status_code == 201, response.text
    return response.json()["order"]


def _kitchen_list(client, setup, params=""):
    return client.get(
        f"/api/v1/kitchen/orders{params}", headers=kitchen_headers()
    )


# ----------------------------------------------------------------------
# Dev gate
# ----------------------------------------------------------------------


def test_kitchen_requires_dev_key(client, setup):
    assert client.get("/api/v1/kitchen/orders").status_code == 401
    assert (
        client.get("/api/v1/kitchen/orders", headers=kitchen_headers("wrong"))
        .status_code
        == 403
    )
    assert _kitchen_list(client, setup).status_code == 200


def test_guest_token_does_not_grant_kitchen_access(client, setup):
    # A valid guest token in the guest header is not kitchen auth.
    response = client.get("/api/v1/kitchen/orders", headers=headers(setup["token"]))
    assert response.status_code == 401
    # Nor is it a valid dev key.
    response = client.get(
        "/api/v1/kitchen/orders",
        headers={"X-Kitchen-Dev-Key": setup["token"]},
    )
    assert response.status_code == 403


def test_kitchen_production_503(client, setup):
    previous = settings.environment
    settings.environment = "production"
    try:
        response = _kitchen_list(client, setup)
        assert response.status_code == 503, response.text
        assert response.json()["error"]["code"] == "kitchen_auth_not_configured"
        # The WS handshake refuses in production too (4403).
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect(
                f"/ws/kitchen?restaurant_id={setup['restaurant_id']}&dev_key={DEV_KEY}"
            ):
                pass
        assert exc.value.code == 4403
    finally:
        settings.environment = previous


def test_kitchen_restaurants(client, setup):
    assert client.get("/api/v1/kitchen/restaurants").status_code == 401
    response = client.get(
        "/api/v1/kitchen/restaurants", headers=kitchen_headers()
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert {"id": setup["restaurant_id"], "name": "Zaika"} in data


# ----------------------------------------------------------------------
# List / detail
# ----------------------------------------------------------------------


def test_list_shape_oldest_first_and_active_only(client, db_session, setup):
    # Delta, not absolute totals: the shared test DB accumulates
    # orders from other tests in this module.
    before = _kitchen_list(client, setup).json()["pagination"]["total"]
    first = _place_one(client, setup, "k-list-1")
    second = _place_one(client, setup, "k-list-2", name="Masala Chai")
    response = _kitchen_list(client, setup)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["pagination"]["total"] == before + 2
    # Oldest first: the two new orders appear in placement order.
    numbers = [o["orderNumber"] for o in body["data"]]
    assert numbers.index(first["orderNumber"]) < numbers.index(
        second["orderNumber"]
    )
    order = next(o for o in body["data"] if o["id"] == first["id"])
    assert order["id"] == first["id"]
    assert order["status"] == "placed"
    assert order["tableNumber"] > 0
    assert isinstance(order["elapsedSeconds"], int) and order["elapsedSeconds"] >= 0
    assert len(order["items"]) == 1
    assert order["items"][0]["status"] == "queued"
    assert order["items"][0]["name"] == "Butter Naan"


def test_list_status_filter_and_unknown_filter_422(client, setup):
    placed = _place_one(client, setup, "k-filter-1")
    second = _place_one(client, setup, "k-filter-2", name="Masala Chai")
    second_id = second["id"]
    response = client.patch(
        f"/api/v1/kitchen/orders/{second_id}/status",
        json={"status": "preparing"},
        headers=kitchen_headers(),
    )
    assert response.status_code == 200, response.text

    queued = _kitchen_list(client, setup, "?status=queued").json()
    queued_ids = {o["id"] for o in queued["data"]}
    assert placed["id"] in queued_ids
    assert second_id not in queued_ids
    preparing = _kitchen_list(client, setup, "?status=preparing").json()
    preparing_ids = {o["id"] for o in preparing["data"]}
    assert second_id in preparing_ids
    assert placed["id"] not in preparing_ids
    ready = _kitchen_list(client, setup, "?status=ready").json()
    assert placed["id"] not in {o["id"] for o in ready["data"]}
    assert second_id not in {o["id"] for o in ready["data"]}
    assert _kitchen_list(client, setup, "?status=bogus").status_code == 422


def test_list_pagination(client, setup):
    before = _kitchen_list(client, setup).json()["pagination"]["total"]
    for i in range(3):
        _place_one(client, setup, f"k-page-{i}")
    total = before + 3
    page1 = _kitchen_list(client, setup, "?per_page=2").json()
    assert page1["pagination"]["total"] == total
    assert page1["pagination"]["total_pages"] == (total + 1) // 2
    assert len(page1["data"]) == 2
    last_page = _kitchen_list(
        client, setup, f"?per_page=2&page={(total + 1) // 2}"
    ).json()
    assert len(last_page["data"]) == total - 2 * ((total + 1) // 2 - 1)
    assert {o["id"] for o in page1["data"]}.isdisjoint(
        {o["id"] for o in last_page["data"]}
    )


def test_get_kitchen_order_and_unknown_404(client, setup):
    placed = _place_one(client, setup, "k-get-1")
    response = client.get(
        f"/api/v1/kitchen/orders/{placed['id']}", headers=kitchen_headers()
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["id"] == placed["id"]
    assert response.json()["data"]["items"][0]["name"] == "Butter Naan"
    response = client.get(
        f"/api/v1/kitchen/orders/{uuid.uuid4()}", headers=kitchen_headers()
    )
    assert response.status_code == 404


def test_new_guest_order_appears_in_kitchen_list(client, setup):
    before = _kitchen_list(client, setup).json()["pagination"]["total"]
    placed = _place_one(client, setup, "k-appears-1")
    body = _kitchen_list(client, setup).json()
    assert body["pagination"]["total"] == before + 1
    assert placed["id"] in {o["id"] for o in body["data"]}


# ----------------------------------------------------------------------
# Transitions (order-level)
# ----------------------------------------------------------------------


def test_transition_placed_to_preparing_moves_items(client, db_session, setup):
    placed = _place_one(client, setup, "k-trans-1")
    response = client.patch(
        f"/api/v1/kitchen/orders/{placed['id']}/status",
        json={"status": "preparing"},
        headers=kitchen_headers(),
    )
    assert response.status_code == 200, response.text
    order = response.json()["data"]
    assert order["status"] == "preparing"
    assert order["acceptedAt"] is not None
    assert order["readyAt"] is None
    assert all(i["status"] == "preparing" for i in order["items"])
    row = db_session.get(Order, uuid.UUID(placed["id"]))
    assert row.version == 2


def test_transition_preparing_to_ready(client, db_session, setup):
    placed = _place_one(client, setup, "k-trans-2")
    client.patch(
        f"/api/v1/kitchen/orders/{placed['id']}/status",
        json={"status": "preparing"},
        headers=kitchen_headers(),
    )
    response = client.patch(
        f"/api/v1/kitchen/orders/{placed['id']}/status",
        json={"status": "ready"},
        headers=kitchen_headers(),
    )
    assert response.status_code == 200, response.text
    order = response.json()["data"]
    assert order["status"] == "ready"
    assert order["readyAt"] is not None
    assert all(i["status"] == "ready" for i in order["items"])
    row = db_session.get(Order, uuid.UUID(placed["id"]))
    assert row.version == 3


def test_transition_accepted_to_preparing_allowed(client, db_session, setup):
    placed = _place_one(client, setup, "k-trans-3")
    row = db_session.get(Order, uuid.UUID(placed["id"]))
    row.status = "accepted"
    db_session.commit()
    response = client.patch(
        f"/api/v1/kitchen/orders/{placed['id']}/status",
        json={"status": "preparing"},
        headers=kitchen_headers(),
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["status"] == "preparing"


def test_invalid_transitions_409_state_unchanged(client, db_session, setup):
    placed = _place_one(client, setup, "k-trans-4")
    order_id = placed["id"]

    def _state():
        row = db_session.get(Order, uuid.UUID(order_id))
        db_session.refresh(row)
        return row.status, row.version, [i.status for i in row.items]

    # placed -> ready skips preparing.
    response = client.patch(
        f"/api/v1/kitchen/orders/{order_id}/status",
        json={"status": "ready"},
        headers=kitchen_headers(),
    )
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "conflict"
    assert response.json()["error"]["details"]["current_status"] == "placed"
    assert response.json()["error"]["details"]["requested_status"] == "ready"
    assert _state() == ("placed", 1, ["queued"])

    # Move to ready, then ready -> preparing is backwards.
    client.patch(
        f"/api/v1/kitchen/orders/{order_id}/status",
        json={"status": "preparing"},
        headers=kitchen_headers(),
    )
    client.patch(
        f"/api/v1/kitchen/orders/{order_id}/status",
        json={"status": "ready"},
        headers=kitchen_headers(),
    )
    response = client.patch(
        f"/api/v1/kitchen/orders/{order_id}/status",
        json={"status": "preparing"},
        headers=kitchen_headers(),
    )
    assert response.status_code == 409
    assert _state() == ("ready", 3, ["ready"])

    # "served" is not a kitchen body value at all -> 422, state untouched.
    response = client.patch(
        f"/api/v1/kitchen/orders/{order_id}/status",
        json={"status": "served"},
        headers=kitchen_headers(),
    )
    assert response.status_code == 422
    assert _state() == ("ready", 3, ["ready"])


def test_cancelled_order_cannot_transition(client, db_session, setup):
    placed = _place_one(client, setup, "k-trans-5")
    cancel = client.post(
        f"/api/v1/orders/{placed['id']}/cancel",
        json={"reason": "guest left"},
        headers=headers(setup["token"]),
    )
    assert cancel.status_code == 200, cancel.text
    response = client.patch(
        f"/api/v1/kitchen/orders/{placed['id']}/status",
        json={"status": "preparing"},
        headers=kitchen_headers(),
    )
    assert response.status_code == 409
    row = db_session.get(Order, uuid.UUID(placed["id"]))
    assert row.status == "cancelled"


def test_transition_unknown_order_404(client, setup):
    response = client.patch(
        f"/api/v1/kitchen/orders/{uuid.uuid4()}/status",
        json={"status": "preparing"},
        headers=kitchen_headers(),
    )
    assert response.status_code == 404


def test_failed_patch_leaves_everything_untouched(client, db_session, setup):
    placed = _place_one(client, setup, "k-trans-6")
    response = client.patch(
        f"/api/v1/kitchen/orders/{placed['id']}/status",
        json={"status": "not-a-status"},
        headers=kitchen_headers(),
    )
    assert response.status_code == 422
    row = db_session.get(Order, uuid.UUID(placed["id"]))
    assert (row.status, row.version) == ("placed", 1)
    assert [i.status for i in row.items] == ["queued"]


# ----------------------------------------------------------------------
# Cross-restaurant isolation + restaurant_id rule
# ----------------------------------------------------------------------


def _make_second_restaurant_order(db_session):
    """A second restaurant (Bistro) with one placed order, built
    directly — the Zaika seed only covers Zaika, and the kitchen
    layer under test does not need Bistro to have a menu."""
    from datetime import datetime, timezone

    from app.models import OrderItem

    zaika = (
        db_session.execute(select(Restaurant).where(Restaurant.slug == "zaika"))
        .scalars()
        .first()
    )
    branch = db_session.execute(
        select(Branch).where(Branch.restaurant_id == zaika.id)
    ).scalars().first()
    other = Restaurant(name="Bistro", slug="bistro")
    db_session.add(other)
    db_session.flush()
    other_branch = Branch(restaurant_id=other.id, name="Bistro — Main", slug="main")
    db_session.add(other_branch)
    db_session.flush()
    table = DiningTable(
        restaurant_id=other.id, branch_id=other_branch.id, number=1
    )
    db_session.add(table)
    db_session.flush()
    from app.models import TableSession

    session = TableSession(
        restaurant_id=other.id,
        branch_id=other_branch.id,
        table_id=table.id,
        status="active",
        opened_at=datetime.now(timezone.utc),
    )
    db_session.add(session)
    db_session.flush()
    menu_item_id = (
        db_session.execute(select(OrderItem.menu_item_id).limit(1))
        .scalars()
        .first()
    )
    # Any menu item id works as a snapshot reference for this test.
    order = Order(
        restaurant_id=other.id,
        branch_id=other_branch.id,
        table_session_id=session.id,
        table_id=table.id,
        order_number="BISTRO-0001",
        status="placed",
        subtotal_paise=1000,
        tax_paise=0,
        total_paise=1000,
        placed_at=datetime.now(timezone.utc),
    )
    db_session.add(order)
    db_session.flush()
    db_session.add(
        OrderItem(
            order_id=order.id,
            menu_item_id=menu_item_id,
            name_snapshot="Bistro Soup",
            unit_price_paise=1000,
            quantity=1,
            total_paise=1000,
            status="queued",
        )
    )
    db_session.commit()
    return other, order


def test_cross_restaurant_isolation(client, db_session, setup):
    _place_one(client, setup, "k-iso-1")
    other, other_order = _make_second_restaurant_order(db_session)
    zaika_id = setup["restaurant_id"]
    try:
        # A's list never shows B's order.
        body = client.get(
            f"/api/v1/kitchen/orders?restaurant_id={zaika_id}",
            headers=kitchen_headers(),
        ).json()
        assert all(o["orderNumber"].startswith("ZAIKA-") for o in body["data"])
        # B's list shows only B's order.
        body = client.get(
            f"/api/v1/kitchen/orders?restaurant_id={other.id}",
            headers=kitchen_headers(),
        ).json()
        assert [o["orderNumber"] for o in body["data"]] == ["BISTRO-0001"]
        # PATCH on B's order via A's restaurant_id -> 404, state unchanged.
        response = client.patch(
            f"/api/v1/kitchen/orders/{other_order.id}/status?restaurant_id={zaika_id}",
            json={"status": "preparing"},
            headers=kitchen_headers(),
        )
        assert response.status_code == 404
        db_session.refresh(other_order)
        assert other_order.status == "placed"
        # GET detail across restaurants -> 404.
        response = client.get(
            f"/api/v1/kitchen/orders/{other_order.id}?restaurant_id={zaika_id}",
            headers=kitchen_headers(),
        )
        assert response.status_code == 404
        # Unknown restaurant -> 404.
        response = client.get(
            f"/api/v1/kitchen/orders?restaurant_id={uuid.uuid4()}",
            headers=kitchen_headers(),
        )
        assert response.status_code == 404
        # Omitted restaurant_id with 2 restaurants -> 422 requiring it.
        response = _kitchen_list(client, setup)
        assert response.status_code == 422, response.text
        assert response.json()["error"]["details"]["field"] == "restaurant_id"
    finally:
        # Remove the second restaurant so later tests (which rely on
        # the single-restaurant omission rule) are not polluted.
        from app.models import TableSession

        for item in list(other_order.items):
            db_session.delete(item)
        db_session.delete(other_order)
        session_row = db_session.get(TableSession, other_order.table_session_id)
        table_row = db_session.get(DiningTable, other_order.table_id)
        branch_row = db_session.get(Branch, other_order.branch_id)
        if session_row is not None:
            db_session.delete(session_row)
        if table_row is not None:
            db_session.delete(table_row)
        if branch_row is not None:
            db_session.delete(branch_row)
        db_session.delete(other)
        db_session.commit()


# ----------------------------------------------------------------------
# WebSocket
# ----------------------------------------------------------------------


def test_ws_auth_rejected(client, setup):
    rid = setup["restaurant_id"]
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(f"/ws/kitchen?restaurant_id={rid}"):
            pass
    assert exc.value.code == 4401
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(
            f"/ws/kitchen?restaurant_id={rid}&dev_key=wrong"
        ):
            pass
    assert exc.value.code == 4401
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(
            f"/ws/kitchen?restaurant_id={uuid.uuid4()}&dev_key={DEV_KEY}"
        ):
            pass
    assert exc.value.code == 4404


def test_ws_order_created_and_status_changed(client, setup):
    rid = setup["restaurant_id"]
    with client.websocket_connect(
        f"/ws/kitchen?restaurant_id={rid}&dev_key={DEV_KEY}"
    ) as ws:
        placed = _place_one(client, setup, "k-ws-1")
        listed = client.get(
            f"/api/v1/kitchen/orders?restaurant_id={rid}",
            headers=kitchen_headers(),
        ).json()["data"]
        listed = next(o for o in listed if o["id"] == placed["id"])
        event = ws.receive_json()
        assert event["type"] == "order.created"
        assert event["restaurant_id"] == rid
        assert event["table_id"]
        assert event["data"] == {
            "order_id": placed["id"],
            "order_number": placed["orderNumber"],
            "status": "placed",
            "table_number": listed["tableNumber"],
        }
        assert event["version"] >= 1
        assert event["occurred_at"]

        response = client.patch(
            f"/api/v1/kitchen/orders/{placed['id']}/status",
            json={"status": "preparing"},
            headers=kitchen_headers(),
        )
        assert response.status_code == 200, response.text
        event = ws.receive_json()
        assert event["type"] == "order.status_changed"
        assert event["data"]["order_id"] == placed["id"]
        assert event["data"]["status"] == "preparing"
        assert event["data"]["order_number"] == placed["orderNumber"]
        assert event["version"] > 1


def test_ws_no_event_after_failed_patch_or_replay(client, setup):
    """A failed/rolled-back PATCH broadcasts nothing, and a replayed
    placement broadcasts nothing: the next event received after each
    must be the one from the following successful action — if the
    failed action had broadcast, it would arrive first."""
    rid = setup["restaurant_id"]
    with client.websocket_connect(
        f"/ws/kitchen?restaurant_id={rid}&dev_key={DEV_KEY}"
    ) as ws:
        placed = _place_one(client, setup, "k-ws-2")
        assert ws.receive_json()["type"] == "order.created"

        # Invalid transition (placed -> ready) -> 409, no event.
        response = client.patch(
            f"/api/v1/kitchen/orders/{placed['id']}/status",
            json={"status": "ready"},
            headers=kitchen_headers(),
        )
        assert response.status_code == 409
        # The next event must be from the valid PATCH below, not the failed one.
        response = client.patch(
            f"/api/v1/kitchen/orders/{placed['id']}/status",
            json={"status": "preparing"},
            headers=kitchen_headers(),
        )
        assert response.status_code == 200
        event = ws.receive_json()
        assert event["type"] == "order.status_changed"
        assert event["data"]["status"] == "preparing"

        # Replay the same placement -> 201 with replay header, no event.
        replay = _place(
            client,
            setup["token"],
            [{"menuItemId": setup["menu"]["Butter Naan"]["id"], "quantity": 1}],
            "k-ws-2",
        )
        assert replay.status_code == 201
        assert replay.headers.get("Idempotency-Replayed") == "true"
        # The next event must be the NEW order's created event.
        second = _place_one(client, setup, "k-ws-3", name="Masala Chai")
        event = ws.receive_json()
        assert event["type"] == "order.created"
        assert event["data"]["order_id"] == second["id"]
