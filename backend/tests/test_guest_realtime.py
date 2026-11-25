"""Guest real-time experience — Stage 8 (tasks B, C, E).

SQLite suite (shared in-memory fixture, like the kitchen/staff
suites). Covers: guest ticket issuance (valid/invalid token),
/ws/guest auth (4401 bad/expired/used/missing ticket, 4403 wrong
channel both ways, 4404 revoked session), session-scoped delivery
of all ten guest event types with cross-table isolation, no event
on a failed action or an idempotent replay, the full guest ->
kitchen -> waiter -> billing golden path across guest + kitchen +
staff sockets, GET /guest/help-calls, and bill void broadcasting
bill.updated. A payment for another session never reaches this
session's sockets.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.auth.models import User, WsTicket
from app.auth.tokens import hash_ws_ticket
from app.core.config import settings
from app.core.security import hash_password
from app.models import Restaurant, TableSession, TableToken
from app.realtime.manager import manager
from tests.helpers import (
    assert_ws_rejected,
    fresh_table_token,
    headers,
    menu_by_name,
    open_session,
    seed,
)

KITCHEN_KEY = "dev-kitchen-only"
STAFF_KEY = "dev-staff-only"


def kitchen_headers():
    return {"X-Kitchen-Dev-Key": KITCHEN_KEY}


def staff_headers():
    return {"X-Staff-Dev-Key": STAFF_KEY}


@pytest.fixture
def setup(client, db_session):
    seed(db_session)
    restaurant = (
        db_session.execute(
            select(Restaurant).where(Restaurant.slug == "zaika")
        )
        .scalars()
        .first()
    )
    return {"restaurant_id": str(restaurant.id)}


def _new_session(client, db_session):
    """A fresh table + token + open session + menu. Returns
    (token, session_id, table_number, menu_by_name)."""
    token = fresh_table_token(db_session)
    body = open_session(client, token)
    menu, _ = menu_by_name(client, token)
    return token, body["session"]["id"], body["table"]["number"], menu


def _issue_ticket(client, token):
    response = client.post("/api/v1/guest/ws/tickets", headers=headers(token))
    assert response.status_code == 201, response.text
    return response.json()["data"]["ticket"]


def _place(client, token, menu, key, name="Butter Naan", quantity=1):
    response = client.post(
        "/api/v1/orders",
        json={"items": [{"menuItemId": menu[name]["id"], "quantity": quantity}]},
        headers={**headers(token), "Idempotency-Key": key},
    )
    assert response.status_code == 201, response.text
    return response.json()["order"]


def _make_owner(client, db_session):
    restaurant = (
        db_session.execute(
            select(Restaurant).where(Restaurant.slug == "zaika")
        )
        .scalars()
        .first()
    )
    user = User(
        restaurant_id=restaurant.id,
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
    response = client.post(
        "/api/v1/auth/login",
        json={"email": user.email, "password": "ownerpass"},
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['data']['accessToken']}"}


def _staff_ticket(client, db_session):
    """A real staff-channel ticket (waiter user, serve_orders)."""
    restaurant = (
        db_session.execute(
            select(Restaurant).where(Restaurant.slug == "zaika")
        )
        .scalars()
        .first()
    )
    user = User(
        restaurant_id=restaurant.id,
        branch_id=None,
        email=f"waiter-{uuid.uuid4().hex[:8]}@zaika.example",
        password_hash=hash_password("waiterpass"),
        role="waiter",
        is_active=True,
        must_change_password=False,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    login = client.post(
        "/api/v1/auth/login",
        json={"email": user.email, "password": "waiterpass"},
    )
    assert login.status_code == 200, login.text
    token = login.json()["data"]["accessToken"]
    issued = client.post(
        "/api/v1/ws/tickets",
        json={"channel": "staff"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert issued.status_code == 200, issued.text
    return issued.json()["data"]["ticket"]


def test_guest_ticket_issued_for_valid_token(client, db_session, setup):
    token, session_id, _number, _menu = _new_session(client, db_session)
    response = client.post("/api/v1/guest/ws/tickets", headers=headers(token))
    assert response.status_code == 201, response.text
    data = response.json()["data"]
    assert data["channel"] == "guest"
    assert data["ticket"]
    assert data["expiresAt"]
    # The client never sees the session id; the binding is server-side.
    assert "session" not in str(data).lower()
    assert "tableSession" not in str(data)
    row = db_session.execute(
        select(WsTicket).where(
            WsTicket.ticket_hash == hash_ws_ticket(data["ticket"])
        )
    ).scalars().first()
    assert row is not None
    assert row.user_id is None
    assert row.channel == "guest"
    assert str(row.table_session_id) == session_id
    assert row.used_at is None


def test_guest_ticket_invalid_token(client, db_session, setup):
    assert client.post(
        "/api/v1/guest/ws/tickets", headers=headers("no-such-token")
    ).status_code == 401
    assert client.post("/api/v1/guest/ws/tickets").status_code == 401


def test_guest_ws_connect_and_counts(client, db_session, setup):
    token, session_id, _number, _menu = _new_session(client, db_session)
    raw = _issue_ticket(client, token)
    with client.websocket_connect(f"/ws/guest?ticket={raw}") as ws:
        assert manager.guest_connection_count(uuid.UUID(session_id)) == 1
        # The socket stays open until the client disconnects.
        ws.close()
    assert manager.guest_connection_count(uuid.UUID(session_id)) == 0


def test_guest_ticket_reuse_and_expiry_rejected(client, db_session, setup):
    token, _session_id, _number, _menu = _new_session(client, db_session)
    raw = _issue_ticket(client, token)
    with client.websocket_connect(f"/ws/guest?ticket={raw}"):
        pass
    # Single-use: the same ticket is now dead.
    assert_ws_rejected(client, f"/ws/guest?ticket={raw}", 4401)
    # Expired: mint another and age it past its TTL in the DB.
    raw2 = _issue_ticket(client, token)
    row = db_session.execute(
        select(WsTicket).where(
            WsTicket.ticket_hash == hash_ws_ticket(raw2)
        )
    ).scalars().first()
    row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db_session.commit()
    assert_ws_rejected(client, f"/ws/guest?ticket={raw2}", 4401)
    # Unknown ticket and missing ticket are 4401 too.
    assert_ws_rejected(client, "/ws/guest?ticket=nope", 4401)
    assert_ws_rejected(client, "/ws/guest", 4401)


def test_guest_ticket_wrong_channel_4403(client, db_session, setup):
    token, _session_id, _number, _menu = _new_session(client, db_session)
    guest_ticket = _issue_ticket(client, token)
    # A guest ticket can never open the kitchen channel.
    assert_ws_rejected(
        client, f"/ws/kitchen?ticket={guest_ticket}", 4403
    )
    assert_ws_rejected(
        client, f"/ws/staff?ticket={guest_ticket}", 4403
    )
    # A staff ticket can never open the guest channel.
    staff_ticket = _staff_ticket(client, db_session)
    assert_ws_rejected(client, f"/ws/guest?ticket={staff_ticket}", 4403)


def test_guest_ws_revoked_session_4404(client, db_session, setup):
    token, _session_id, _number, _menu = _new_session(client, db_session)
    raw = _issue_ticket(client, token)
    # Revoke the QR token the session was opened with: the session
    # is no longer active, so the ticket must not connect.
    session = db_session.execute(
        select(TableSession).where(
            TableSession.id == uuid.UUID(_session_id)
        )
    ).scalars().first()
    token_row = db_session.get(TableToken, session.token_id)
    token_row.status = "revoked"
    db_session.commit()
    assert_ws_rejected(client, f"/ws/guest?ticket={raw}", 4404)


def test_guest_order_events_are_session_scoped(client, db_session, setup):
    token_a, _sid_a, _n_a, menu_a = _new_session(client, db_session)
    token_b, _sid_b, _n_b, menu_b = _new_session(client, db_session)
    raw_a = _issue_ticket(client, token_a)
    raw_b = _issue_ticket(client, token_b)
    with client.websocket_connect(f"/ws/guest?ticket={raw_a}") as ws_a:
        with client.websocket_connect(f"/ws/guest?ticket={raw_b}") as ws_b:
            order_a1 = _place(client, token_a, menu_a, "g-scope-a1")
            event = ws_a.receive_json()
            assert event["type"] == "order.created"
            assert event["data"]["order_id"] == order_a1["id"]
            # B's order must not reach A: the next event A receives
            # is A's own second order, not B's.
            order_b1 = _place(client, token_b, menu_b, "g-scope-b1")
            event = ws_b.receive_json()
            assert event["type"] == "order.created"
            assert event["data"]["order_id"] == order_b1["id"]
            order_a2 = _place(client, token_a, menu_a, "g-scope-a2")
            event = ws_a.receive_json()
            assert event["type"] == "order.created"
            assert event["data"]["order_id"] == order_a2["id"]
            order_b2 = _place(client, token_b, menu_b, "g-scope-b2")
            event = ws_b.receive_json()
            assert event["type"] == "order.created"
            assert event["data"]["order_id"] == order_b2["id"]


def test_guest_no_event_on_failed_action_or_replay(client, db_session, setup):
    token, _sid, _n, menu = _new_session(client, db_session)
    raw = _issue_ticket(client, token)
    with client.websocket_connect(f"/ws/guest?ticket={raw}") as ws:
        order = _place(client, token, menu, "g-noevt-1")
        assert ws.receive_json()["type"] == "order.created"
        # Invalid placement (unknown item) -> 422, no event.
        bad = client.post(
            "/api/v1/orders",
            json={"items": [{"menuItemId": str(uuid.uuid4()), "quantity": 1}]},
            headers={**headers(token), "Idempotency-Key": "g-noevt-bad"},
        )
        assert bad.status_code == 422
        # Replay the same placement -> 201 with replay header, no event.
        replay = client.post(
            "/api/v1/orders",
            json={"items": [{"menuItemId": menu["Butter Naan"]["id"], "quantity": 1}]},
            headers={**headers(token), "Idempotency-Key": "g-noevt-1"},
        )
        assert replay.status_code == 201
        assert replay.headers.get("Idempotency-Replayed") == "true"
        # The next event must be the NEW order's created event.
        order2 = _place(client, token, menu, "g-noevt-2", name="Masala Chai")
        event = ws.receive_json()
        assert event["type"] == "order.created"
        assert event["data"]["order_id"] == order2["id"]


def test_guest_cancel_broadcasts(client, db_session, setup):
    token, _sid, _n, menu = _new_session(client, db_session)
    raw = _issue_ticket(client, token)
    rid = setup["restaurant_id"]
    with client.websocket_connect(f"/ws/guest?ticket={raw}") as ws_g:
        with client.websocket_connect(
            f"/ws/kitchen?restaurant_id={rid}&dev_key={KITCHEN_KEY}"
        ) as ws_k:
            order = _place(client, token, menu, "g-cancel-1")
            assert ws_g.receive_json()["type"] == "order.created"
            assert ws_k.receive_json()["type"] == "order.created"
            response = client.post(
                f"/api/v1/orders/{order['id']}/cancel",
                json={},
                headers=headers(token),
            )
            assert response.status_code == 200, response.text
            event = ws_g.receive_json()
            assert event["type"] == "order.cancelled"
            assert event["data"]["order_id"] == order["id"]
            assert event["data"]["status"] == "cancelled"
            event = ws_k.receive_json()
            assert event["type"] == "order.cancelled"
            assert event["data"]["order_id"] == order["id"]


def test_guest_help_calls_rest_and_live(client, db_session, setup):
    token, _sid, _n, _menu = _new_session(client, db_session)
    token2, _sid2, _n2, _menu2 = _new_session(client, db_session)
    # No calls yet: empty list (not a 404).
    assert client.get(
        "/api/v1/guest/help-calls", headers=headers(token)
    ).json()["data"] == []
    raw = _issue_ticket(client, token)
    rid = setup["restaurant_id"]
    with client.websocket_connect(f"/ws/guest?ticket={raw}") as ws_g:
        with client.websocket_connect(
            f"/ws/staff?restaurant_id={rid}&dev_key={STAFF_KEY}"
        ) as ws_s:
            created = client.post(
                "/api/v1/guest/help-calls",
                json={"type": "waiter", "note": "Extra napkins please"},
                headers=headers(token),
            )
            assert created.status_code == 201, created.text
            call = created.json()["data"]
            event = ws_g.receive_json()
            assert event["type"] == "help_call.opened"
            assert event["data"]["help_call_id"] == call["id"]
            event = ws_s.receive_json()
            assert event["type"] == "help_call.opened"
            # The REST list is the refetch source of truth.
            listed = client.get(
                "/api/v1/guest/help-calls", headers=headers(token)
            ).json()["data"]
            assert [c["id"] for c in listed] == [call["id"]]
            assert listed[0]["status"] == "open"
            # Another session sees none of this table's calls.
            assert client.get(
                "/api/v1/guest/help-calls", headers=headers(token2)
            ).json()["data"] == []
            # Acknowledge -> guest sees it live.
            ack = client.patch(
                f"/api/v1/staff/help-calls/{call['id']}/acknowledge"
                f"?restaurant_id={rid}",
                headers=staff_headers(),
            )
            assert ack.status_code == 200, ack.text
            event = ws_g.receive_json()
            assert event["type"] == "help_call.acknowledged"
            assert event["data"]["status"] == "acknowledged"
            assert ws_s.receive_json()["type"] == "help_call.acknowledged"
            # Resolve -> guest sees it live.
            resolved = client.patch(
                f"/api/v1/staff/help-calls/{call['id']}/resolve"
                f"?restaurant_id={rid}",
                headers=staff_headers(),
            )
            assert resolved.status_code == 200, resolved.text
            event = ws_g.receive_json()
            assert event["type"] == "help_call.resolved"
            assert event["data"]["status"] == "resolved"
            listed = client.get(
                "/api/v1/guest/help-calls", headers=headers(token)
            ).json()["data"]
            assert listed[0]["status"] == "resolved"


def test_guest_full_live_flow(client, db_session, setup):
    """Golden path across three sockets at once: guest places an
    order, kitchen prepares + readies it, the waiter serves it, the
    guest asks for help, staff ack/resolve, the guest requests the
    bill, splits it, and staff collect the cash — every step
    reaches the guest socket live, and the bill ends paid with a
    receipt available."""
    token, _sid, _n, menu = _new_session(client, db_session)
    raw = _issue_ticket(client, token)
    rid = setup["restaurant_id"]
    with client.websocket_connect(f"/ws/guest?ticket={raw}") as ws_g:
        with client.websocket_connect(
            f"/ws/kitchen?restaurant_id={rid}&dev_key={KITCHEN_KEY}"
        ) as ws_k:
            with client.websocket_connect(
                f"/ws/staff?restaurant_id={rid}&dev_key={STAFF_KEY}"
            ) as ws_s:
                order = _place(client, token, menu, "g-flow-1")
                assert ws_g.receive_json()["data"]["order_id"] == order["id"]
                assert ws_k.receive_json()["data"]["order_id"] == order["id"]
                assert ws_s.receive_json()["data"]["order_id"] == order["id"]

                for status in ("preparing", "ready"):
                    response = client.patch(
                        f"/api/v1/kitchen/orders/{order['id']}/status",
                        json={"status": status},
                        headers=kitchen_headers(),
                    )
                    assert response.status_code == 200, response.text
                    event = ws_g.receive_json()
                    assert event["type"] == "order.status_changed"
                    assert event["data"]["status"] == status
                    event = ws_s.receive_json()
                    assert event["type"] == "order.status_changed"
                    assert event["data"]["status"] == status

                served = client.patch(
                    f"/api/v1/staff/orders/{order['id']}/serve"
                    f"?restaurant_id={rid}",
                    headers=staff_headers(),
                )
                assert served.status_code == 200, served.text
                event = ws_g.receive_json()
                assert event["type"] == "order.status_changed"
                assert event["data"]["status"] == "served"
                event = ws_s.receive_json()
                assert event["type"] == "order.status_changed"
                assert event["data"]["status"] == "served"

                created = client.post(
                    "/api/v1/guest/help-calls",
                    json={"type": "water"},
                    headers=headers(token),
                )
                assert created.status_code == 201, created.text
                call = created.json()["data"]
                assert ws_g.receive_json()["type"] == "help_call.opened"
                assert ws_s.receive_json()["type"] == "help_call.opened"
                assert client.patch(
                    f"/api/v1/staff/help-calls/{call['id']}/acknowledge"
                    f"?restaurant_id={rid}",
                    headers=staff_headers(),
                ).status_code == 200
                assert ws_g.receive_json()["type"] == "help_call.acknowledged"
                assert ws_s.receive_json()["type"] == "help_call.acknowledged"
                assert client.patch(
                    f"/api/v1/staff/help-calls/{call['id']}/resolve"
                    f"?restaurant_id={rid}",
                    headers=staff_headers(),
                ).status_code == 200
                assert ws_g.receive_json()["type"] == "help_call.resolved"
                assert ws_s.receive_json()["type"] == "help_call.resolved"

                bill = client.post(
                    "/api/v1/guest/bill", headers=headers(token)
                ).json()["data"]
                assert ws_g.receive_json()["type"] == "bill.created"
                assert ws_s.receive_json()["type"] == "bill.created"

                splits = client.post(
                    "/api/v1/guest/bill/splits",
                    json={"mode": "equal", "parts": 2},
                    headers=headers(token),
                )
                assert splits.status_code == 200, splits.text
                event = ws_g.receive_json()
                assert event["type"] == "bill.updated"
                assert event["data"]["bill_id"] == bill["id"]

                requested = client.patch(
                    "/api/v1/guest/bill/request-payment", headers=headers(token)
                )
                assert requested.status_code == 200, requested.text
                event = ws_g.receive_json()
                assert event["type"] == "bill.updated"
                assert event["data"]["status"] == "payment_requested"
                assert ws_s.receive_json()["type"] == "bill.requested"

                frozen = requested.json()["data"]
                for split in frozen["splits"]:
                    paid = client.post(
                        f"/api/v1/staff/bills/{bill['id']}/payments"
                        f"?restaurant_id={rid}",
                        json={
                            "amountPaise": split["amountPaise"],
                            "billSplitId": split["id"],
                        },
                        headers={
                            **staff_headers(),
                            "Idempotency-Key": f"g-flow-p-{uuid.uuid4().hex}",
                        },
                    )
                    assert paid.status_code == 201, paid.text
                    event = ws_g.receive_json()
                    assert event["type"] == "payment.recorded"
                    assert event["data"]["bill_id"] == bill["id"]
                    assert ws_s.receive_json()["type"] == "payment.recorded"
                # The second (final) split payment also pays the bill.
                event = ws_g.receive_json()
                assert event["type"] == "bill.paid"
                assert event["data"]["bill_id"] == bill["id"]
                assert ws_s.receive_json()["type"] == "bill.paid"

                receipt = client.get(
                    "/api/v1/guest/bill/receipt", headers=headers(token)
                )
                assert receipt.status_code == 200, receipt.text


def test_guest_void_broadcasts_bill_updated(client, db_session, setup):
    token, _sid, _n, menu = _new_session(client, db_session)
    raw = _issue_ticket(client, token)
    auth = _make_owner(client, db_session)
    with client.websocket_connect(f"/ws/guest?ticket={raw}") as ws_g:
        order = _place(client, token, menu, "g-void-1")
        assert ws_g.receive_json()["type"] == "order.created"
        bill = client.post(
            "/api/v1/guest/bill", headers=headers(token)
        ).json()["data"]
        assert ws_g.receive_json()["type"] == "bill.created"
        voided = client.post(
            f"/api/v1/admin/bills/{bill['id']}/void",
            json={"reason": "guest left"},
            headers=auth,
        )
        assert voided.status_code == 200, voided.text
        event = ws_g.receive_json()
        assert event["type"] == "bill.updated"
        assert event["data"]["bill_id"] == bill["id"]
        assert event["data"]["status"] == "voided"


def test_payment_for_another_session_never_reaches_guest(
    client, db_session, setup
):
    token_a, _sid_a, _n_a, menu_a = _new_session(client, db_session)
    token_b, _sid_b, _n_b, menu_b = _new_session(client, db_session)
    raw_b = _issue_ticket(client, token_b)
    rid = setup["restaurant_id"]
    # A orders, bills, and pays in full — B's socket must see none
    # of it. B then orders: the first event B receives is its own
    # order, proving A's payment never arrived.
    with client.websocket_connect(f"/ws/guest?ticket={raw_b}") as ws_b:
        _place(client, token_a, menu_a, "g-other-a1")
        bill_a = client.post(
            "/api/v1/guest/bill", headers=headers(token_a)
        ).json()["data"]
        client.patch(
            "/api/v1/guest/bill/request-payment", headers=headers(token_a)
        )
        paid = client.post(
            f"/api/v1/staff/bills/{bill_a['id']}/payments?restaurant_id={rid}",
            json={"amountPaise": bill_a["totalPaise"]},
            headers={
                **staff_headers(),
                "Idempotency-Key": f"g-other-p-{uuid.uuid4().hex}",
            },
        )
        assert paid.status_code == 201, paid.text
        order_b = _place(client, token_b, menu_b, "g-other-b1")
        event = ws_b.receive_json()
        assert event["type"] == "order.created"
        assert event["data"]["order_id"] == order_b["id"]
