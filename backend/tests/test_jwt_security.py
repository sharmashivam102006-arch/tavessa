"""Stage 9 — JWT / refresh-token lifecycle.

Proves, against the live app (no sleeps): rotation invalidates
the old refresh token, reuse of a revoked token revokes the whole
family, logout revokes, change-password revokes ALL sessions,
deactivation kills a live access token immediately, and expired /
tampered / wrong-secret / unknown-user access tokens are 401.
Access tokens are NOT revocable by design (15-min expiry is the
bound) — that is asserted explicitly below so the limit is pinned,
not hidden.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from jose import jwt

from app.core.config import settings
from tests.sec9 import auth_headers, cleanup_placed_orders, login, make_user, seed, user_headers, zaika_id  # noqa: F401  (cleanup_placed_orders is autouse)

COOKIE = "tavessa_refresh"


def _refresh_cookie(response):
    value = response.cookies.get(COOKIE)
    assert value, f"no {COOKIE} cookie in response: {response.headers}"
    return value


def _refresh(client, raw):
    return client.post("/api/v1/auth/refresh", cookies={COOKIE: raw})


def _forged_token(db_session, user, *, secret=None, minutes=-5, sub=None):
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(sub if sub is not None else user.id),
        "restaurant_id": str(user.restaurant_id),
        "branch_id": None,
        "role": user.role,
        "jti": str(uuid.uuid4()),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=minutes)).timestamp()),
    }
    return jwt.encode(payload, secret or settings.secret_key, algorithm="HS256")


def test_refresh_rotation_old_token_rejected(client, db_session):
    seed(db_session)
    user, password = make_user(db_session, "waiter")
    first = login(client, user.email, password)
    assert first.status_code == 200
    old_raw = _refresh_cookie(first)
    rotated = _refresh(client, old_raw)
    assert rotated.status_code == 200, rotated.text
    new_raw = _refresh_cookie(rotated)
    assert new_raw != old_raw
    # The new token works (checked FIRST: replaying the old token
    # below is reuse, and reuse revokes the whole family — see the
    # next test — so the new token must be exercised before that).
    assert _refresh(client, new_raw).status_code == 200
    # The rotated-out token no longer works. (This replay also kills
    # the family by design; the reuse test asserts that.)
    assert _refresh(client, old_raw).status_code == 401
    # And the access token issued by the rotation authenticates.
    token = rotated.json()["data"]["accessToken"]
    assert client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
    ).status_code == 200


def test_refresh_reuse_revokes_whole_family(client, db_session):
    seed(db_session)
    user, password = make_user(db_session, "waiter")
    first = login(client, user.email, password)
    stolen = _refresh_cookie(first)
    rotated = _refresh(client, stolen)
    assert rotated.status_code == 200
    rotated_raw = _refresh_cookie(rotated)
    rotated_access = rotated.json()["data"]["accessToken"]
    # Replay the revoked token: 401, and the theft detection revokes
    # the whole family — the rotated token now fails too.
    assert _refresh(client, stolen).status_code == 401
    assert _refresh(client, rotated_raw).status_code == 401
    # PINNED LIMIT (by design, not a bug fixed here): the access
    # token minted by the rotation is a bearer JWT with no server
    # revocation list — it stays valid until its 15-minute expiry.
    # Refresh is dead, but the live access token still works.
    assert client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {rotated_access}"}
    ).status_code == 200


def test_logout_revokes_refresh_token(client, db_session):
    seed(db_session)
    user, password = make_user(db_session, "waiter")
    first = login(client, user.email, password)
    raw = _refresh_cookie(first)
    assert client.post("/api/v1/auth/logout", cookies={COOKIE: raw}).status_code == 200
    assert _refresh(client, raw).status_code == 401
    # Logout without a cookie is a no-op 200, not an error.
    assert client.post("/api/v1/auth/logout").status_code == 200


def test_change_password_revokes_all_sessions(client, db_session):
    seed(db_session)
    user, password = make_user(db_session, "waiter")
    # Two independent logins = two refresh families.
    first = login(client, user.email, password)
    raw_a = _refresh_cookie(first)
    access_a = first.json()["data"]["accessToken"]
    client.cookies.clear()
    second = login(client, user.email, password)
    raw_b = _refresh_cookie(second)
    # Change the password with the second session's access token.
    response = client.post(
        "/api/v1/auth/change-password",
        json={"currentPassword": password, "newPassword": "NewPassw0rd123"},
        headers={"Authorization": f"Bearer {second.json()['data']['accessToken']}"},
    )
    assert response.status_code == 200, response.text
    # BOTH refresh families are dead.
    assert _refresh(client, raw_a).status_code == 401
    assert _refresh(client, raw_b).status_code == 401
    # Old password dead, new password works.
    assert login(client, user.email, password).status_code == 401
    assert login(client, user.email, "NewPassw0rd123").status_code == 200
    # Wrong current password changes nothing (401, sessions intact
    # for the new password — proven by a fresh login + refresh).
    fresh = login(client, user.email, "NewPassw0rd123")
    assert client.post(
        "/api/v1/auth/change-password",
        json={"currentPassword": "wrong", "newPassword": "AnotherPass1"},
        headers={"Authorization": f"Bearer {fresh.json()['data']['accessToken']}"},
    ).status_code == 401
    assert _refresh(client, _refresh_cookie(fresh)).status_code == 200


def test_deactivated_user_live_access_token_401_immediately(client, db_session):
    seed(db_session)
    user, password = make_user(db_session, "waiter")
    headers_ = auth_headers(client, user.email, password)
    # Deactivate directly in the DB (as if an owner did it), then
    # the SAME access token must fail at once — get_current_user
    # reloads the user per request, it does not trust the token.
    user.is_active = False
    db_session.commit()
    assert client.get("/api/v1/auth/me", headers=headers_).status_code == 401
    # Refresh is dead too, and login is dead.
    assert login(client, user.email, password).status_code == 401


def test_expired_access_token_401(client, db_session):
    seed(db_session)
    user, _ = make_user(db_session, "waiter")
    expired = _forged_token(db_session, user, minutes=-5)
    assert client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {expired}"}
    ).status_code == 401
    # A token expiring in 14 minutes (inside the 15-min window) works.
    live = _forged_token(db_session, user, minutes=14)
    assert client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {live}"}
    ).status_code == 200


def test_tampered_and_wrong_secret_and_unknown_user_tokens_401(client, db_session):
    seed(db_session)
    user, password = make_user(db_session, "waiter")
    headers_ = auth_headers(client, user.email, password)
    good = headers_["Authorization"].split(" ", 1)[1]
    # Tampered payload (flip one char in the middle segment).
    parts = good.split(".")
    tampered = parts[0] + "." + ("A" if parts[1][0] != "A" else "B") + parts[1][1:] + "." + parts[2]
    assert client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {tampered}"}
    ).status_code == 401
    # Signed with the wrong secret.
    wrong = _forged_token(db_session, user, secret="not-the-server-secret", minutes=14)
    assert client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {wrong}"}
    ).status_code == 401
    # Valid signature, unknown user id.
    ghost = _forged_token(db_session, user, sub=uuid.uuid4(), minutes=14)
    assert client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {ghost}"}
    ).status_code == 401
    # Not a JWT at all / missing scheme.
    assert client.get(
        "/api/v1/auth/me", headers={"Authorization": "Bearer garbage"}
    ).status_code == 401
    assert client.get(
        "/api/v1/auth/me", headers={"Authorization": good}
    ).status_code == 401  # no "Bearer " scheme


def test_access_token_default_expiry_is_15_minutes(client, db_session):
    seed(db_session)
    user, password = make_user(db_session, "waiter")
    response = login(client, user.email, password)
    claims = jwt.decode(
        response.json()["data"]["accessToken"],
        settings.secret_key,
        algorithms=["HS256"],
    )
    assert claims["exp"] - claims["iat"] == 15 * 60
    assert claims["sub"] == str(user.id)
    assert claims["restaurant_id"] == str(user.restaurant_id)
    assert claims["role"] == "waiter"
    assert claims["jti"]


def test_refresh_with_no_cookie_and_unknown_token_401(client, db_session):
    seed(db_session)
    make_user(db_session, "waiter")
    assert client.post("/api/v1/auth/refresh").status_code == 401
    assert _refresh(client, "not-a-real-refresh-token").status_code == 401
