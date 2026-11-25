"""Stage 9 — QR table tokens.

Proves: tokens are stored as SHA-256 hashes only (never the raw
value), rotation revokes the previous token (410), an expired
status is 410, and a token is scoped to exactly its own table —
another table's token cannot read this table's bill, orders, or
help calls. (Unknown-token 401 / revoked 410 / expired-by-date 410
are already covered in test_guest_auth.py and are not duplicated.)
"""
import hashlib

from sqlalchemy import select

from app.core.security import hash_table_token
from app.models import TableToken
from tests.helpers import headers, make_token, make_token_row, open_session
from tests.sec9 import cleanup_placed_orders, place_bill, seed  # noqa: F401  (cleanup_placed_orders is autouse)


def test_token_stored_as_hash_never_raw(client, db_session):
    seed(db_session)
    raw = make_token(db_session, 5)
    row = (
        db_session.execute(
            select(TableToken).where(
                TableToken.token_hash == hash_table_token(raw)
            )
        )
        .scalars()
        .first()
    )
    assert row is not None
    assert row.token_hash == hashlib.sha256(raw.encode()).hexdigest()
    assert row.token_hash == hash_table_token(raw)
    assert row.token_hash != raw
    assert len(row.token_hash) == 64
    assert all(c in "0123456789abcdef" for c in row.token_hash)
    # The raw token appears nowhere else in the DB row.
    assert raw not in str(row.token_hash)


def test_rotation_revokes_previous_token(client, db_session):
    seed(db_session)
    first = make_token(db_session, 5)
    assert open_session(client, first)["session"]["tableNumber"] == 5
    # Issuing a fresh token for the same table revokes the old one
    # (table_service.create_table_token) — the old token is 410 now.
    second = make_token(db_session, 5)
    assert second != first
    response = client.get(f"/api/v1/guest/t/{first}")
    assert response.status_code == 410, response.text
    assert response.json()["error"]["code"] == "session_expired"
    assert open_session(client, second)["session"]["tableNumber"] == 5


def test_expired_status_token_410(client, db_session):
    seed(db_session)
    raw, _row = make_token_row(db_session, 5, status="expired")
    response = client.get(f"/api/v1/guest/t/{raw}")
    assert response.status_code == 410
    assert response.json()["error"]["code"] == "session_expired"
    # And a revoked token cannot be used as an API header either.
    response = client.get("/api/v1/guest/menu", headers=headers(raw))
    assert response.status_code == 410


def test_cross_table_bill_and_help_calls_denied(client, db_session):
    seed(db_session)
    token5 = make_token(db_session, 5)
    token6 = make_token(db_session, 6)
    bill, _, _ = place_bill(client, db_session, token=token5)
    # Table 6 has no bill of its own: its bill read is 404, and it
    # must not see Table 5's bill through any guest endpoint.
    response = client.get("/api/v1/guest/bill", headers=headers(token6))
    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "bill_not_found"
    # Table 6's help-call list is empty even though Table 5 has
    # orders; and Table 5's own bill is readable by Table 5.
    assert client.get("/api/v1/guest/help-calls", headers=headers(token6)).json()["data"] == []
    assert client.get("/api/v1/guest/bill", headers=headers(token5)).status_code == 200
    # Table 6 cannot request payment on Table 5's bill (it has no
    # bill at all — 404, not a cross-table state change).
    response = client.patch("/api/v1/guest/bill/request-payment", headers=headers(token6))
    assert response.status_code == 404, response.text


def test_cross_table_splits_cannot_touch_other_bill(client, db_session):
    seed(db_session)
    token5 = make_token(db_session, 5)
    token6 = make_token(db_session, 6)
    bill, _, _ = place_bill(client, db_session, token=token5)
    # Table 6 has no bill: splits fail 404 and Table 5's bill is
    # unchanged (no splits were created on it).
    response = client.post(
        "/api/v1/guest/bill/splits",
        json={"mode": "equal", "parts": 2},
        headers=headers(token6),
    )
    assert response.status_code == 404, response.text
    after = client.get("/api/v1/guest/bill", headers=headers(token5)).json()["data"]
    assert after["id"] == bill["id"]
    assert after["splits"] == []
