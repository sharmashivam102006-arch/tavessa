"""Stage 9 — rate limits, item 8.

Login (10/min) and order placement (60/min) are already covered in
test_rate_limits.py and are NOT duplicated here. This module covers
the remaining limiter-enabled endpoints, each with the limiter
re-enabled in a module fixture (same Stage 8 pattern): the QR
session-open (120/min), the kitchen status PATCH (60/min), and the
staff PATCH family — serve (60/min) and cash payment (60/min).
Admin endpoints have NO limiter in code (grep: no @limiter.limit
in app/api/v1/admin.py) — that is reported, not silently fixed;
no new limiting was added (out of scope) and no limit was lowered.
"""
import uuid

import pytest

from app.core.rate_limit import limiter
from tests.helpers import headers, make_token, menu_by_name, open_session
from tests.sec9 import cleanup_placed_orders, pay_bill, place_bill, seed, zaika_id  # noqa: F401  (cleanup_placed_orders is autouse)


@pytest.fixture(scope="module", autouse=True)
def limiter_enabled():
    was_enabled = limiter.enabled
    limiter.enabled = True
    try:
        yield
    finally:
        limiter.enabled = was_enabled


@pytest.fixture(autouse=True)
def reset_limiter():
    limiter.reset()
    yield
    limiter.reset()


def test_session_open_rate_limited_after_120(client, db_session):
    """120 unknown-token session opens are answered (401); the 121st
    in a minute is 429. Unknown tokens never create a session, so
    the 429 is the limiter, not state."""
    seed(db_session)
    for _ in range(120):
        response = client.get("/api/v1/guest/t/does-not-exist-sec9")
        assert response.status_code == 401, response.text
    assert client.get("/api/v1/guest/t/does-not-exist-sec9").status_code == 429


def test_kitchen_patch_rate_limited_after_60(client, db_session):
    """60 kitchen PATCHes (1 success + 59 already-transitioned 409s)
    are answered; the 61st is 429."""
    seed(db_session)
    bill, _, token = place_bill(client, db_session)
    orders = client.get("/api/v1/guest/orders", headers=headers(token)).json()["data"]
    order_id = orders[0]["id"]
    url = f"/api/v1/kitchen/orders/{order_id}/status?restaurant_id={zaika_id(db_session)}"
    key = {"X-Kitchen-Dev-Key": "dev-kitchen-only"}
    first = client.patch(url, json={"status": "preparing"}, headers=key)
    assert first.status_code == 200, first.text
    for _ in range(59):
        response = client.patch(url, json={"status": "preparing"}, headers=key)
        assert response.status_code == 409, response.text
    assert client.patch(url, json={"status": "preparing"}, headers=key).status_code == 429


def test_staff_serve_rate_limited_after_60(client, db_session):
    """Serve needs a ready order: kitchen moves it to ready first
    (2 kitchen calls, a different limiter bucket), then 60 serve
    attempts (1 success + 59 409s); the 61st is 429."""
    seed(db_session)
    bill, _, token = place_bill(client, db_session)
    orders = client.get("/api/v1/guest/orders", headers=headers(token)).json()["data"]
    order_id = orders[0]["id"]
    rid = zaika_id(db_session)
    kkey = {"X-Kitchen-Dev-Key": "dev-kitchen-only"}
    assert client.patch(
        f"/api/v1/kitchen/orders/{order_id}/status?restaurant_id={rid}",
        json={"status": "preparing"}, headers=kkey,
    ).status_code == 200
    assert client.patch(
        f"/api/v1/kitchen/orders/{order_id}/status?restaurant_id={rid}",
        json={"status": "ready"}, headers=kkey,
    ).status_code == 200
    skey = {"X-Staff-Dev-Key": "dev-staff-only"}
    url = f"/api/v1/staff/orders/{order_id}/serve?restaurant_id={rid}"
    assert client.patch(url, headers=skey).status_code == 200
    for _ in range(59):
        response = client.patch(url, headers=skey)
        assert response.status_code == 409, response.text
    assert client.patch(url, headers=skey).status_code == 429


def test_staff_payment_rate_limited_after_60(client, db_session):
    """60 one-paise cash payments are accepted; the 61st is 429.
    (Butter Naan totals far more than 60 paise, so no payment
    fails for amount reasons — the 429 is the limiter.)"""
    seed(db_session)
    bill, _, _ = place_bill(client, db_session)
    assert bill["totalPaise"] > 60
    for n in range(60):
        response = pay_bill(client, db_session, bill, amount_paise=1,
                            key=f"sec9-payrl-{n}")
        assert response.status_code == 201, (n, response.text)
    response = pay_bill(client, db_session, bill, amount_paise=1,
                        key="sec9-payrl-60")
    assert response.status_code == 429, response.text
