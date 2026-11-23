"""Waiter / Staff dashboard — Stage 5.

SQLite suite (shared in-memory fixture). Covers: the dev-only staff
gate (401/403/503, guest token and kitchen key grant nothing), the
floor grid (live operational state per table), table detail, active
orders, serve (ready -> served only, with served_at + version, session
NOT closed, table status unchanged), served orders leaving the
kitchen active queue, help-call list / acknowledge / resolve with
their 409 rules, cross-restaurant isolation, and WebSocket events
(order.created / order.status_changed on kitchen transition AND on
serve / help_call.opened / acknowledged / resolved) with NO event
after a failed transaction or a replayed placement.

Postgres-only behaviour (row-lock serialisation of concurrent
serves) lives in tests/test_staff_concurrency.py.
"""
import uuid
from datetime import datetime, timezone

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

DEV_KEY = "dev-staff-only"
KITCHEN_KEY = "dev-kitchen-only"


def staff_headers(key=DEV_KEY):
    return {"X-Staff-Dev-Key": key}


def kitchen_headers(key=KITCHEN_KEY):
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
    # This test's own table number (the shared test DB accumulates
    # fresh tables from earlier tests, so tests must select by this
    # number, never by "number >= 900").
    session_body = client.get("/api/v1/guest/session", headers=headers(token)).json()
    return {
        "token": token,
        "menu": by_name,
        "restaurant_id": str(restaurant.id),
        "table_number": session_body["table"]["number"],
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


def _ready(client, setup, order_id):
    """Kitchen: placed -> preparing -> ready."""
    for status in ("preparing", "ready"):
        response = client.patch(
            f"/api/v1/kitchen/orders/{order_id}/status",
            json={"status": status},
            headers=kitchen_headers(),
        )
        assert response.status_code == 200, response.text


def _help_call(client, setup, type_="water", note="Still water, please"):
    response = client.post(
        "/api/v1/guest/help-calls",
        json={"type": type_, "note": note},
        headers=headers(setup["token"]),
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]


def _staff_tables(client, setup, params=""):
    return client.get(
        f"/api/v1/staff/tables{params}", headers=staff_headers()
    )


def _staff_orders(client, setup, params=""):
    # per_page=100: the shared test DB accumulates orders from
    # earlier tests, and the list is oldest-first — the default
    # first page may not contain this test's orders.
    sep = "&" if params.startswith("?") else "?"
    params = f"{params}{sep}per_page=100" if params else "?per_page=100"
    return client.get(
        f"/api/v1/staff/orders{params}", headers=staff_headers()
    )


def _serve(client, setup, order_id):
    return client.patch(
        f"/api/v1/staff/orders/{order_id}/serve",
        headers=staff_headers(),
    )


# ----------------------------------------------------------------------
# Dev gate
# ----------------------------------------------------------------------


def test_staff_requires_dev_key(client, setup):
    assert client.get("/api/v1/staff/tables").status_code == 401
    assert (
        client.get("/api/v1/staff/tables", headers=staff_headers("wrong"))
        .status_code
        == 403
    )
    assert _staff_tables(client, setup).status_code == 200


def test_guest_token_does_not_grant_staff_access(client, setup):
    response = client.get(
        "/api/v1/staff/tables", headers=headers(setup["token"])
    )
    assert response.status_code == 401
    response = client.get(
        "/api/v1/staff/tables",
        headers={"X-Staff-Dev-Key": setup["token"]},
    )
    assert response.status_code == 403


def test_kitchen_key_does_not_grant_staff_access(client, setup):
    # The kitchen dev key is a different key: presenting it AS the
    # staff key must not open the staff routes (and vice versa).
    response = client.get(
        "/api/v1/staff/tables",
        headers={"X-Staff-Dev-Key": KITCHEN_KEY},
    )
    assert response.status_code == 403
    response = client.get(
        "/api/v1/kitchen/orders",
        headers={"X-Kitchen-Dev-Key": DEV_KEY},
    )
    assert response.status_code == 403


def test_staff_production_503(client, setup):
    previous = settings.environment
    settings.environment = "production"
    try:
        response = _staff_tables(client, setup)
        assert response.status_code == 503, response.text
        assert response.json()["error"]["code"] == "staff_auth_not_configured"
        # The WS handshake refuses in production too (4403).
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect(
                f"/ws/staff?restaurant_id={setup['restaurant_id']}&dev_key={DEV_KEY}"
            ):
                pass
        assert exc.value.code == 4403
    finally:
        settings.environment = previous


# ----------------------------------------------------------------------
# Floor overview
# ----------------------------------------------------------------------


def test_floor_grid_shape_and_live_state(client, db_session, setup):
    order = _place_one(client, setup, "s-floor-1")
    _help_call(client, setup)
    body = _staff_tables(client, setup).json()
    rows = {row["number"]: row for row in body["data"]}
    # This test's table has the session + order + help call.
    row = rows[setup["table_number"]]
    assert row["status"] == "occupied"
    assert row["sessionId"]
    assert row["sessionOpenedAt"]
    assert row["activeOrderCount"] == 1
    assert row["readyOrderCount"] == 0
    assert row["latestOrderStatus"] == "placed"
    assert row["hasOpenHelpCall"] is True
    # Seeded tables with no session are available with zero counts.
    assert rows[1]["status"] == "available"
    assert rows[1]["activeOrderCount"] == 0
    assert rows[1]["readyOrderCount"] == 0
    assert rows[1]["latestOrderStatus"] is None
    assert rows[1]["hasOpenHelpCall"] is False
    assert rows[1]["sessionId"] is None
    # Grid order is by table number.
    assert [r["number"] for r in body["data"]] == sorted(
        r["number"] for r in body["data"]
    )
    # Ready flag flips once the kitchen marks the order ready.
    _ready(client, setup, order["id"])
    body = _staff_tables(client, setup).json()
    row = next(r for r in body["data"] if r["number"] == setup["table_number"])
    assert row["readyOrderCount"] == 1
    assert row["latestOrderStatus"] == "ready"


def test_table_detail(client, db_session, setup):
    order = _place_one(client, setup, "s-detail-1")
    call = _help_call(client, setup)
    body = _staff_tables(client, setup).json()
    table_id = next(
        r["id"] for r in body["data"] if r["number"] == setup["table_number"]
    )
    response = client.get(
        f"/api/v1/staff/tables/{table_id}", headers=staff_headers()
    )
    assert response.status_code == 200, response.text
    detail = response.json()["data"]
    assert detail["table"]["number"] >= 900
    assert detail["table"]["activeOrderCount"] == 1
    assert [o["orderNumber"] for o in detail["orders"]] == [
        order["orderNumber"]
    ]
    assert detail["orders"][0]["items"][0]["name"] == "Butter Naan"
    assert [c["id"] for c in detail["helpCalls"]] == [call["id"]]
    assert detail["helpCalls"][0]["type"] == "water"
    # Unknown table -> 404.
    response = client.get(
        f"/api/v1/staff/tables/{uuid.uuid4()}", headers=staff_headers()
    )
    assert response.status_code == 404


# ----------------------------------------------------------------------
# Active orders
# ----------------------------------------------------------------------


def test_staff_orders_list_shape_and_filter(client, setup):
    first = _place_one(client, setup, "s-orders-1", name="Butter Naan")
    second = _place_one(client, setup, "s-orders-2", name="Masala Chai")
    _ready(client, setup, first["id"])
    body = _staff_orders(client, setup).json()
    mine = [o for o in body["data"] if o["id"] in (first["id"], second["id"])]
    assert [o["id"] for o in mine] == [first["id"], second["id"]]  # oldest first
    assert mine[0]["status"] == "ready"
    assert mine[0]["tableNumber"] >= 900
    assert mine[0]["elapsedSeconds"] >= 0
    assert mine[0]["items"][0]["name"] == "Butter Naan"
    # Status filter.
    body = _staff_orders(client, setup, "?status=ready").json()
    assert first["id"] in [o["id"] for o in body["data"]]
    assert second["id"] not in [o["id"] for o in body["data"]]
    body = _staff_orders(client, setup, "?status=placed").json()
    assert second["id"] in [o["id"] for o in body["data"]]
    assert first["id"] not in [o["id"] for o in body["data"]]
    # Unknown filter -> 422.
    response = _staff_orders(client, setup, "?status=served")
    assert response.status_code == 422


def test_new_guest_order_appears_in_staff_list(client, setup):
    placed = _place_one(client, setup, "s-orders-3")
    body = _staff_orders(client, setup).json()
    assert placed["id"] in [o["id"] for o in body["data"]]


# ----------------------------------------------------------------------
# Serving
# ----------------------------------------------------------------------


def test_serve_ready_order(client, db_session, setup):
    order = _place_one(client, setup, "s-serve-1")
    _ready(client, setup, order["id"])
    row_before = db_session.get(Order, uuid.UUID(order["id"]))
    session_id = row_before.table_session_id
    response = _serve(client, setup, order["id"])
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["status"] == "served"
    assert data["servedAt"]
    db_session.expire_all()
    row = db_session.get(Order, uuid.UUID(order["id"]))
    assert row.status == "served"
    assert row.served_at is not None
    assert row.version == 4  # placed(1) -> preparing(2) -> ready(3) -> served(4)
    assert [i.status for i in row.items] == ["served"]
    # Serving does NOT close the session and does NOT change the table.
    from app.models import TableSession

    session = db_session.get(TableSession, session_id)
    assert session.status == "active"
    table = db_session.get(DiningTable, row.table_id)
    assert table.status == "occupied"


def test_serve_invalid_transitions_409_state_unchanged(client, db_session, setup):
    order = _place_one(client, setup, "s-serve-2")

    def _state():
        row = db_session.get(Order, uuid.UUID(order["id"]))
        db_session.refresh(row)
        return row.status, row.version, [i.status for i in row.items]

    # placed -> served is illegal.
    response = _serve(client, setup, order["id"])
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "conflict"
    assert response.json()["error"]["details"]["current_status"] == "placed"
    assert _state() == ("placed", 1, ["queued"])
    # preparing -> served is illegal.
    client.patch(
        f"/api/v1/kitchen/orders/{order['id']}/status",
        json={"status": "preparing"},
        headers=kitchen_headers(),
    )
    response = _serve(client, setup, order["id"])
    assert response.status_code == 409
    assert _state() == ("preparing", 2, ["preparing"])
    # Ready -> served works, then served -> served is illegal.
    response = client.patch(
        f"/api/v1/kitchen/orders/{order['id']}/status",
        json={"status": "ready"},
        headers=kitchen_headers(),
    )
    assert response.status_code == 200, response.text
    assert _serve(client, setup, order["id"]).status_code == 200
    response = _serve(client, setup, order["id"])
    assert response.status_code == 409
    assert _state() == ("served", 4, ["served"])
    # A guest client cannot serve: no dev key -> 401 (the guest
    # token is not staff auth, and there is no guest serve endpoint).
    response = client.patch(
        f"/api/v1/staff/orders/{order['id']}/serve",
        headers=headers(setup["token"]),
    )
    assert response.status_code == 401


def test_serve_unknown_order_404(client, setup):
    response = _serve(client, setup, str(uuid.uuid4()))
    assert response.status_code == 404


def test_served_order_leaves_kitchen_queue(client, setup):
    order = _place_one(client, setup, "s-serve-3")
    _ready(client, setup, order["id"])
    # per_page=100: the shared test DB accumulates orders, and the
    # kitchen list is oldest-first — the default page may not
    # contain this test's order.
    kitchen_before = client.get(
        "/api/v1/kitchen/orders?per_page=100", headers=kitchen_headers()
    ).json()["data"]
    assert order["id"] in [o["id"] for o in kitchen_before]
    assert _serve(client, setup, order["id"]).status_code == 200
    kitchen_after = client.get(
        "/api/v1/kitchen/orders?per_page=100", headers=kitchen_headers()
    ).json()["data"]
    assert order["id"] not in [o["id"] for o in kitchen_after]
    # ... and the staff active list too.
    staff_after = _staff_orders(client, setup).json()["data"]
    assert order["id"] not in [o["id"] for o in staff_after]


def test_failed_serve_leaves_everything_untouched(client, db_session, setup):
    order = _place_one(client, setup, "s-serve-4")
    response = _serve(client, setup, order["id"])
    assert response.status_code == 409
    row = db_session.get(Order, uuid.UUID(order["id"]))
    assert (row.status, row.version) == ("placed", 1)
    assert row.served_at is None
    assert [i.status for i in row.items] == ["queued"]


# ----------------------------------------------------------------------
# Help calls
# ----------------------------------------------------------------------


def test_help_call_list_ack_resolve(client, db_session, setup):
    call = _help_call(client, setup, type_="waiter", note="Need the bill")
    body = client.get("/api/v1/staff/help-calls", headers=staff_headers()).json()
    mine = [c for c in body["data"] if c["id"] == call["id"]]
    assert len(mine) == 1
    assert mine[0]["tableNumber"] >= 900
    assert mine[0]["type"] == "waiter"
    assert mine[0]["note"] == "Need the bill"
    assert mine[0]["status"] == "open"
    assert mine[0]["acknowledgedAt"] is None
    assert mine[0]["resolvedAt"] is None

    # Resolve from open -> 409 (must acknowledge first).
    response = client.patch(
        f"/api/v1/staff/help-calls/{call['id']}/resolve",
        headers=staff_headers(),
    )
    assert response.status_code == 409
    # Acknowledge.
    response = client.patch(
        f"/api/v1/staff/help-calls/{call['id']}/acknowledge",
        headers=staff_headers(),
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["status"] == "acknowledged"
    assert response.json()["data"]["acknowledgedAt"]
    # Duplicate acknowledge -> 409.
    response = client.patch(
        f"/api/v1/staff/help-calls/{call['id']}/acknowledge",
        headers=staff_headers(),
    )
    assert response.status_code == 409
    # Resolve.
    response = client.patch(
        f"/api/v1/staff/help-calls/{call['id']}/resolve",
        headers=staff_headers(),
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["status"] == "resolved"
    assert response.json()["data"]["resolvedAt"]
    # Duplicate resolve -> 409.
    response = client.patch(
        f"/api/v1/staff/help-calls/{call['id']}/resolve",
        headers=staff_headers(),
    )
    assert response.status_code == 409
    # Resolved calls leave the default (unresolved) list...
    body = client.get("/api/v1/staff/help-calls", headers=staff_headers()).json()
    assert call["id"] not in [c["id"] for c in body["data"]]
    # ... but appear under status=resolved.
    body = client.get(
        "/api/v1/staff/help-calls?status=resolved", headers=staff_headers()
    ).json()
    assert call["id"] in [c["id"] for c in body["data"]]
    # Unknown status filter -> 422; unknown call -> 404.
    assert (
        client.get("/api/v1/staff/help-calls?status=bogus", headers=staff_headers())
        .status_code
        == 422
    )
    assert (
        client.patch(
            f"/api/v1/staff/help-calls/{uuid.uuid4()}/acknowledge",
            headers=staff_headers(),
        ).status_code
        == 404
    )


def test_help_call_unknown_restaurant_404(client, setup):
    call = _help_call(client, setup)
    response = client.patch(
        f"/api/v1/staff/help-calls/{call['id']}/acknowledge"
        f"?restaurant_id={uuid.uuid4()}",
        headers=staff_headers(),
    )
    assert response.status_code == 404


# ----------------------------------------------------------------------
# Cross-restaurant isolation + restaurant_id rule
# ----------------------------------------------------------------------


def _make_second_restaurant_order(db_session):
    """A second restaurant (Bistro) with one placed order, built
    directly — mirrors tests/test_kitchen.py."""
    from app.models import OrderItem, TableSession

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
    _place_one(client, setup, "s-iso-1")
    other, other_order = _make_second_restaurant_order(db_session)
    zaika_id = setup["restaurant_id"]
    try:
        # A's tables never include B's table; A's orders never show B's.
        tables = _staff_tables(
            client, setup, f"?restaurant_id={zaika_id}"
        ).json()["data"]
        assert other_order.table_id not in [t["id"] for t in tables]
        orders = _staff_orders(
            client, setup, f"?restaurant_id={zaika_id}"
        ).json()["data"]
        assert all(o["orderNumber"].startswith("ZAIKA-") for o in orders)
        # B's orders list shows only B's order.
        orders = _staff_orders(
            client, setup, f"?restaurant_id={other.id}"
        ).json()["data"]
        assert [o["orderNumber"] for o in orders] == ["BISTRO-0001"]
        # Serve on B's order via A's restaurant_id -> 404, unchanged.
        response = client.patch(
            f"/api/v1/staff/orders/{other_order.id}/serve?restaurant_id={zaika_id}",
            headers=staff_headers(),
        )
        assert response.status_code == 404
        db_session.refresh(other_order)
        assert other_order.status == "placed"
        # Table detail across restaurants -> 404.
        response = client.get(
            f"/api/v1/staff/tables/{other_order.table_id}?restaurant_id={zaika_id}",
            headers=staff_headers(),
        )
        assert response.status_code == 404
        # Omitted restaurant_id with 2 restaurants -> 422.
        assert _staff_tables(client, setup).status_code == 422
        assert _staff_orders(client, setup).status_code == 422
    finally:
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
        with client.websocket_connect(f"/ws/staff?restaurant_id={rid}"):
            pass
    assert exc.value.code == 4401
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(
            f"/ws/staff?restaurant_id={rid}&dev_key=wrong"
        ):
            pass
    assert exc.value.code == 4401
    # The KITCHEN key does not open the staff socket.
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(
            f"/ws/staff?restaurant_id={rid}&dev_key={KITCHEN_KEY}"
        ):
            pass
    assert exc.value.code == 4401
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(
            f"/ws/staff?restaurant_id={uuid.uuid4()}&dev_key={DEV_KEY}"
        ):
            pass
    assert exc.value.code == 4404


def test_ws_events_for_staff(client, setup):
    rid = setup["restaurant_id"]
    with client.websocket_connect(
        f"/ws/staff?restaurant_id={rid}&dev_key={DEV_KEY}"
    ) as ws:
        # New guest order -> order.created on the staff channel.
        placed = _place_one(client, setup, "s-ws-1")
        event = ws.receive_json()
        assert event["type"] == "order.created"
        assert event["restaurant_id"] == rid
        assert event["data"]["order_id"] == placed["id"]
        assert event["data"]["table_number"] >= 900

        # Kitchen transition -> order.status_changed on staff too.
        response = client.patch(
            f"/api/v1/kitchen/orders/{placed['id']}/status",
            json={"status": "preparing"},
            headers=kitchen_headers(),
        )
        assert response.status_code == 200
        event = ws.receive_json()
        assert event["type"] == "order.status_changed"
        assert event["data"]["status"] == "preparing"

        # Guest help call -> help_call.opened.
        call = _help_call(client, setup, type_="water")
        event = ws.receive_json()
        assert event["type"] == "help_call.opened"
        assert event["data"]["help_call_id"] == call["id"]
        assert event["data"]["type"] == "water"
        assert event["data"]["status"] == "open"

        # Acknowledge + resolve -> their events, in order.
        response = client.patch(
            f"/api/v1/staff/help-calls/{call['id']}/acknowledge",
            headers=staff_headers(),
        )
        assert response.status_code == 200
        event = ws.receive_json()
        assert event["type"] == "help_call.acknowledged"
        assert event["data"]["help_call_id"] == call["id"]
        assert event["data"]["status"] == "acknowledged"
        response = client.patch(
            f"/api/v1/staff/help-calls/{call['id']}/resolve",
            headers=staff_headers(),
        )
        assert response.status_code == 200
        event = ws.receive_json()
        assert event["type"] == "help_call.resolved"
        assert event["data"]["help_call_id"] == call["id"]

        # Serve -> order.status_changed with status served.
        client.patch(
            f"/api/v1/kitchen/orders/{placed['id']}/status",
            json={"status": "ready"},
            headers=kitchen_headers(),
        )
        assert ws.receive_json()["type"] == "order.status_changed"
        assert _serve(client, setup, placed["id"]).status_code == 200
        event = ws.receive_json()
        assert event["type"] == "order.status_changed"
        assert event["data"]["status"] == "served"


def test_ws_no_event_after_failed_serve_or_replay(client, setup):
    """A failed/rolled-back serve broadcasts nothing, and a replayed
    placement broadcasts nothing: the next event received after each
    must be from the following successful action."""
    rid = setup["restaurant_id"]
    with client.websocket_connect(
        f"/ws/staff?restaurant_id={rid}&dev_key={DEV_KEY}"
    ) as ws:
        placed = _place_one(client, setup, "s-ws-2")
        assert ws.receive_json()["type"] == "order.created"

        # Invalid serve (placed -> served) -> 409, no event.
        assert _serve(client, setup, placed["id"]).status_code == 409
        # The next event must be from the kitchen transition below.
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
            "s-ws-2",
        )
        assert replay.status_code == 201
        assert replay.headers.get("Idempotency-Replayed") == "true"
        second = _place_one(client, setup, "s-ws-3", name="Masala Chai")
        event = ws.receive_json()
        assert event["type"] == "order.created"
        assert event["data"]["order_id"] == second["id"]
