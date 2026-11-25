"""Rate-limit tests — Stage 8.

These run with the slowapi limiter ENABLED. The rest of the suite
disables it globally (tests/conftest.py: the whole SQLite suite
shares one TestClient IP, so the caps would be exhausted by
legitimate test traffic). This module re-enables it in a
module-scoped fixture and restores the disabled state afterwards,
so no other module is affected; the per-test reset clears the
in-memory counters between tests.

The production limits are NOT weakened here — these tests assert
the real limits (login 10/minute, order placement 60/minute).
"""
import pytest

from app.core.rate_limit import limiter
from tests.helpers import headers, menu_by_name, open_session, seed


@pytest.fixture(scope="module", autouse=True)
def limiter_enabled():
    """Enable the limiter for this module only; restore afterwards."""
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


def test_login_rate_limited_after_ten_failures(client):
    """10 failed logins are answered (401); the 11th in a minute
    is rate-limited (429). A nonexistent email fails the same way
    as a wrong password — no information leaks either way."""
    for _ in range(10):
        response = client.post(
            "/api/v1/auth/login",
            json={"email": "nobody@zaika.example", "password": "wrong-password"},
        )
        assert response.status_code == 401, response.text
    response = client.post(
        "/api/v1/auth/login",
        json={"email": "nobody@zaika.example", "password": "wrong-password"},
    )
    assert response.status_code == 429, response.text


def test_order_placement_rate_limited_after_sixty(client, db_session):
    """60 order placements in a minute succeed; the 61st is
    rate-limited (429). Each placement uses its own idempotency
    key, so the 429 is the limiter, not an idempotency replay."""
    seed(db_session)
    from tests.helpers import make_token

    token = make_token(db_session, 5)
    open_session(client, token)
    menu, _ = menu_by_name(client, token)
    item = next(iter(menu.values()))
    body = {
        "items": [
            {
                "menuItemId": item["id"],
                "quantity": 1,
                "specialInstructions": "rate test",
            }
        ]
    }
    for n in range(60):
        response = client.post(
            "/api/v1/orders",
            json=body,
            headers={**headers(token), "Idempotency-Key": f"rate-limit-{n}"},
        )
        assert response.status_code == 201, (n, response.text)
    response = client.post(
        "/api/v1/orders",
        json=body,
        headers={**headers(token), "Idempotency-Key": "rate-limit-60"},
    )
    assert response.status_code == 429, response.text
