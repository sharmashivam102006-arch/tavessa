"""Guest auth — QR tokens + sessions (Stage 3)."""
from datetime import datetime, timedelta, timezone

from tests.helpers import headers, make_token, make_token_row, menu_by_name, open_session, seed


def test_valid_token_opens_session(client, db_session):
    seed(db_session)
    token = make_token(db_session, 5)
    body = open_session(client, token)
    assert body["session"]["tableNumber"] == 5
    assert body["session"]["status"] == "active"
    assert body["restaurant"]["name"] == "Zaika"
    assert body["restaurant"]["rating"] == 4.6
    assert body["table"]["number"] == 5
    assert body["menuUrl"] == f"/t/{token}"
    assert body["session"]["restaurantId"] == body["restaurant"]["id"]
    assert body["session"]["tableId"]


def test_session_resumed_not_duplicated(client, db_session):
    seed(db_session)
    token = make_token(db_session, 5)
    first = open_session(client, token)
    second = open_session(client, token)
    assert first["session"]["id"] == second["session"]["id"]


def test_current_session_endpoint(client, db_session):
    seed(db_session)
    token = make_token(db_session, 5)
    response = client.get("/api/v1/guest/session", headers=headers(token))
    assert response.status_code == 200
    assert response.json()["session"]["tableNumber"] == 5


def test_unknown_token_401(client, db_session):
    seed(db_session)
    response = client.get("/api/v1/guest/t/this-token-does-not-exist")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_table_token"


def test_revoked_token_410(client, db_session):
    seed(db_session)
    raw, _row = make_token_row(db_session, 5, status="revoked")
    response = client.get(f"/api/v1/guest/t/{raw}")
    assert response.status_code == 410
    assert response.json()["error"]["code"] == "session_expired"


def test_expired_token_410(client, db_session):
    seed(db_session)
    past = datetime.now(timezone.utc) - timedelta(days=1)
    raw, _row = make_token_row(db_session, 5, expires_at=past)
    response = client.get(f"/api/v1/guest/t/{raw}")
    assert response.status_code == 410
    assert response.json()["error"]["code"] == "session_expired"


def test_missing_header_401(client, db_session):
    seed(db_session)
    response = client.get("/api/v1/guest/session")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_table_token"
    response = client.get("/api/v1/guest/menu")
    assert response.status_code == 401


def test_cross_table_order_access_404(client, db_session):
    seed(db_session)
    token5 = make_token(db_session, 5)
    token6 = make_token(db_session, 6)
    open_session(client, token5)
    open_session(client, token6)
    _, menu = menu_by_name(client, token5)
    butter = next(i for i in menu["items"] if i["name"] == "Old Delhi Butter Chicken")
    placed = client.post(
        "/api/v1/orders",
        json={"items": [{"menuItemId": butter["id"], "quantity": 1}]},
        headers={**headers(token5), "Idempotency-Key": "cross-table-1"},
    )
    assert placed.status_code == 201, placed.text
    order_id = placed.json()["order"]["id"]
    # Table 6's token must not see Table 5's order — 404, not 403.
    response = client.get(f"/api/v1/guest/orders/{order_id}", headers=headers(token6))
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
    response = client.get("/api/v1/guest/orders", headers=headers(token6))
    assert response.status_code == 200
    assert response.json()["data"] == []
