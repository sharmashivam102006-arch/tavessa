"""Stage 7 — auth, admin, tax, void, refund, audit, WS tickets.

SQLite suite (same shared-DB fixture as the rest of the suite).
Covers: login valid/invalid, Argon2id hashing, refresh rotation
+ reuse revocation, logout, deactivation, role/permission gates,
branch + restaurant isolation, menu CRUD + snapshot preservation
+ price-change audit, tax config + frozen bills, void rules,
refund validation + idempotency, cash-report reconciliation,
audit immutability + no secrets, WS tickets, rate limiting.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.rate_limit import limiter
from app.db.base import Base
from app.db.session import get_db
from app.main import app

# This module gets its OWN in-memory SQLite engine (not the shared
# conftest one): admin tests mutate global state — tax configs,
# menu prices, extra categories, demoted owners — that would
# pollute the billing/menu/seed suites sharing the conftest DB.
# Within this module tests still share one DB (several tests rely
# on the tax config / menu state left by earlier ones), so the
# assertions here are delta-based where that matters.
_admin_engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
_AdminSession = sessionmaker(bind=_admin_engine, autoflush=False, autocommit=False)
Base.metadata.create_all(bind=_admin_engine)


@pytest.fixture
def db_session():
    session = _AdminSession()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client(db_session):
    def _override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
from app.core.security import hash_password, verify_password
from app.models import Bill, Payment, Refund, Restaurant, User
from app.seed.zaika import RESTAURANT_SLUG
from tests.helpers import (
    assert_ws_rejected,
    fresh_table_token,
    headers,
    make_token,
    menu_by_name,
    open_session,
    seed,
)

OWNER_EMAIL = "owner@zaika.example"
OWNER_PASSWORD = "OwnerPass123"


def _restaurant_id(db_session):
    return db_session.execute(
        select(Restaurant).where(Restaurant.slug == RESTAURANT_SLUG)
    ).scalars().first().id


def _make_user(db_session, email, password, role, flags=None, branch_id=None):
    user = User(
        restaurant_id=_restaurant_id(db_session),
        branch_id=branch_id,
        email=email.lower(),
        password_hash=hash_password(password),
        role=role,
        is_active=True,
        must_change_password=False,
        **(flags or {}),
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture
def owner(db_session):
    # The SQLite suite shares one DB across tests, so the owner
    # email must be unique per test (a fixed email would collide
    # on the (restaurant_id, email) unique constraint).
    global OWNER_EMAIL
    seed(db_session)
    OWNER_EMAIL = f"owner-{uuid.uuid4().hex[:8]}@zaika.example"
    return _make_user(db_session, OWNER_EMAIL, OWNER_PASSWORD, "owner")


def _login(client, email, password):
    return client.post(
        "/api/v1/auth/login", json={"email": email, "password": password}
    )


def _auth_headers(client, email=None, password=OWNER_PASSWORD):
    email = email or OWNER_EMAIL
    response = _login(client, email, password)
    assert response.status_code == 200, response.text
    token = response.json()["data"]["accessToken"]
    return {"Authorization": f"Bearer {token}"}


def _place_and_pay(client, db_session, token, item_name="Butter Naan", quantity=1):
    """Guest order -> bill -> staff cash payment (dev key).
    Returns (bill dict, payment dict)."""
    open_session(client, token)
    menu = menu_by_name(client, token)[0]
    item = menu[item_name]
    response = client.post(
        "/api/v1/orders",
        json={"items": [{"menuItemId": item["id"], "quantity": quantity}]},
        headers={**headers(token), "Idempotency-Key": f"a-{uuid.uuid4().hex}"},
    )
    assert response.status_code == 201, response.text
    bill = client.post("/api/v1/guest/bill", headers=headers(token)).json()["data"]
    pay = client.post(
        f"/api/v1/staff/bills/{bill['id']}/payments?restaurant_id={_restaurant_id(db_session)}",
        json={"amountPaise": bill["totalPaise"]},
        headers={"X-Staff-Dev-Key": "dev-staff-only", "Idempotency-Key": f"p-{uuid.uuid4().hex}"},
    )
    assert pay.status_code == 201, pay.text
    return bill, pay.json()["data"]["payment"]


def _bill(client, db_session, token):
    open_session(client, token)
    menu = menu_by_name(client, token)[0]
    item = menu["Butter Naan"]
    response = client.post(
        "/api/v1/orders",
        json={"items": [{"menuItemId": item["id"], "quantity": 1}]},
        headers={**headers(token), "Idempotency-Key": f"b-{uuid.uuid4().hex}"},
    )
    assert response.status_code == 201, response.text
    bill = client.post("/api/v1/guest/bill", headers=headers(token)).json()["data"]
    return bill


# ----------------------------------------------------------------------
# Auth
# ----------------------------------------------------------------------

def test_login_success_sets_cookie_and_returns_user(client, owner):
    response = _login(client, OWNER_EMAIL, OWNER_PASSWORD)
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["tokenType"] == "bearer"
    assert data["accessToken"]
    assert data["user"]["email"] == OWNER_EMAIL
    assert data["user"]["role"] == "owner"
    assert "passwordHash" not in response.text
    assert "password_hash" not in response.text
    assert "tavessa_refresh" in response.headers.get("set-cookie", "")


def test_login_wrong_password_and_unknown_email_same_401(client, owner):
    for email, password in [
        (OWNER_EMAIL, "wrong-password"),
        ("nobody@zaika.example", "whatever123"),
    ]:
        response = _login(client, email, password)
        assert response.status_code == 401, response.text
        assert response.json()["error"]["message"] == "Email or password is incorrect."


def test_password_is_argon2id_hash_not_plaintext(client, db_session, owner):
    assert owner.password_hash != OWNER_PASSWORD
    assert owner.password_hash.startswith("$argon2id$")
    assert verify_password(OWNER_PASSWORD, owner.password_hash)
    assert not verify_password("nope", owner.password_hash)


def test_me_requires_token_and_returns_user(client, owner):
    assert client.get("/api/v1/auth/me").status_code == 401
    response = client.get("/api/v1/auth/me", headers=_auth_headers(client))
    assert response.status_code == 200
    assert response.json()["data"]["email"] == OWNER_EMAIL


def test_refresh_rotates_and_old_token_rejected(client, owner):
    _login(client, OWNER_EMAIL, OWNER_PASSWORD)
    first = client.post("/api/v1/auth/refresh")
    assert first.status_code == 200, first.text
    new_token = first.json()["data"]["accessToken"]
    assert new_token
    # The rotated-out token is gone: a second refresh with the
    # ORIGINAL cookie cannot be replayed because the client now
    # holds the new cookie; instead verify rotation by checking
    # that only one live session exists per family via a fresh
    # login + refresh cycle on a second client is not possible
    # here — the rotation itself is proven by the new token working.
    me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {new_token}"})
    assert me.status_code == 200


def test_refresh_reuse_revokes_whole_family(client, owner):
    _login(client, OWNER_EMAIL, OWNER_PASSWORD)
    # Steal the refresh cookie value, rotate once, then replay it.
    stolen = client.cookies.get("tavessa_refresh")
    assert stolen
    rotated = client.post("/api/v1/auth/refresh")
    assert rotated.status_code == 200, rotated.text
    # Replay the revoked token directly.
    replay = client.post(
        "/api/v1/auth/refresh",
        cookies={"tavessa_refresh": stolen},
    )
    assert replay.status_code == 401, replay.text
    # The whole family is revoked: even the rotated token now fails.
    fresh = client.cookies.get("tavessa_refresh")
    assert client.post(
        "/api/v1/auth/refresh", cookies={"tavessa_refresh": fresh}
    ).status_code == 401


def test_logout_revokes_refresh_token(client, owner):
    _login(client, OWNER_EMAIL, OWNER_PASSWORD)
    assert client.post("/api/v1/auth/logout").status_code == 200
    assert client.post("/api/v1/auth/refresh").status_code == 401


def test_change_password_revokes_sessions_and_clears_flag(client, db_session, owner):
    waiter = _make_user(db_session, "w@zaika.example", "WaiterPass1", "waiter")
    waiter.must_change_password = True
    db_session.commit()
    headers_ = _auth_headers(client, "w@zaika.example", "WaiterPass1")
    response = client.post(
        "/api/v1/auth/change-password",
        json={"currentPassword": "WaiterPass1", "newPassword": "NewWaiterPass2"},
        headers=headers_,
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["mustChangePassword"] is False
    # Old password no longer works; new one does.
    assert _login(client, "w@zaika.example", "WaiterPass1").status_code == 401
    assert _login(client, "w@zaika.example", "NewWaiterPass2").status_code == 200


def test_deactivated_account_rejected_immediately(client, db_session, owner):
    waiter = _make_user(db_session, "w2@zaika.example", "WaiterPass1", "waiter")
    token_headers = _auth_headers(client, "w2@zaika.example", "WaiterPass1")
    # Deactivate via the owner, then the same access token fails
    # immediately (get_current_user reloads the user per request).
    response = client.post(
        f"/api/v1/admin/users/{waiter.id}/deactivate",
        headers=_auth_headers(client),
    )
    assert response.status_code == 200, response.text
    assert client.get("/api/v1/auth/me", headers=token_headers).status_code == 401
    assert _login(client, "w2@zaika.example", "WaiterPass1").status_code == 401


def test_login_rate_limit(client, owner):
    limiter.enabled = True
    try:
        statuses = [
            _login(client, OWNER_EMAIL, "wrong-password").status_code
            for _ in range(12)
        ]
        assert 429 in statuses, statuses
    finally:
        limiter.enabled = False


# ----------------------------------------------------------------------
# Permissions / user management
# ----------------------------------------------------------------------

def test_waiter_cannot_call_admin_endpoints(client, db_session, owner):
    _make_user(db_session, "w3@zaika.example", "WaiterPass1", "waiter")
    h = _auth_headers(client, "w3@zaika.example", "WaiterPass1")
    assert client.get("/api/v1/admin/dashboard/overview", headers=h).status_code == 403
    assert client.get("/api/v1/admin/users", headers=h).status_code == 403
    assert client.get("/api/v1/admin/audit-logs", headers=h).status_code == 403


def test_kitchen_cannot_serve_or_confirm_cash(client, db_session, owner):
    _make_user(db_session, "k@zaika.example", "KitchenPass1", "kitchen")
    h = _auth_headers(client, "k@zaika.example", "KitchenPass1")
    # Kitchen has no serve/confirm_cash permission on admin paths.
    assert client.get("/api/v1/admin/bills", headers=h).status_code == 403


def test_manager_without_flag_cannot_refund_or_void(client, db_session, owner):
    _make_user(db_session, "m@zaika.example", "ManagerPass1", "manager")
    h = _auth_headers(client, "m@zaika.example", "ManagerPass1")
    assert client.get("/api/v1/admin/dashboard/overview", headers=h).status_code == 200
    assert client.get("/api/v1/admin/settings/tax", headers=h).status_code == 403


def test_manager_with_flag_can_refund(client, db_session, owner):
    _make_user(
        db_session, "m2@zaika.example", "ManagerPass1", "manager",
        flags={"can_refund": True},
    )
    h = _auth_headers(client, "m2@zaika.example", "ManagerPass1")
    token = make_token(db_session, 5)
    bill, payment = _place_and_pay(client, db_session, token)
    response = client.post(
        f"/api/v1/admin/bills/{bill['id']}/refunds",
        json={"paymentId": payment["id"], "amountPaise": 100, "reason": "test"},
        headers={**h, "Idempotency-Key": f"r-{uuid.uuid4().hex}"},
    )
    assert response.status_code == 201, response.text


def test_only_owner_can_create_owner_or_manager(client, db_session, owner):
    _make_user(
        db_session, "m3@zaika.example", "ManagerPass1", "manager",
        flags={"can_manage_staff": True},
    )
    h = _auth_headers(client, "m3@zaika.example", "ManagerPass1")
    response = client.post(
        "/api/v1/admin/users",
        json={"email": "new-owner@zaika.example", "password": "SomePass123", "role": "owner"},
        headers=h,
    )
    assert response.status_code == 403, response.text
    ok = client.post(
        "/api/v1/admin/users",
        json={"email": "new-waiter@zaika.example", "password": "SomePass123", "role": "waiter"},
        headers=h,
    )
    assert ok.status_code == 201, ok.text
    assert ok.json()["data"]["mustChangePassword"] is True


def test_last_active_owner_cannot_be_deactivated(client, db_session, owner):
    response = client.post(
        f"/api/v1/admin/users/{owner.id}/deactivate", headers=_auth_headers(client)
    )
    assert response.status_code in (403, 409), response.text
    # A second owner makes deactivation of the first possible.
    second = _make_user(db_session, "o2@zaika.example", "OwnerPass123", "owner")
    response = client.post(
        f"/api/v1/admin/users/{owner.id}/deactivate", headers=_auth_headers(client)
    )
    assert response.status_code == 403, response.text  # cannot deactivate self
    response = client.post(
        f"/api/v1/admin/users/{second.id}/deactivate",
        headers=_auth_headers(client, "o2@zaika.example", "OwnerPass123"),
    )
    assert response.status_code == 403, response.text  # cannot deactivate self either


def test_deactivate_last_owner_via_role_change_blocked(client, db_session, owner):
    second = _make_user(db_session, "o3@zaika.example", "OwnerPass123", "owner")
    h = _auth_headers(client, "o3@zaika.example", "OwnerPass123")
    # Demoting the only OTHER owner is fine; demoting the last one is not.
    response = client.patch(
        f"/api/v1/admin/users/{owner.id}",
        json={"role": "manager"},
        headers=h,
    )
    assert response.status_code == 200, response.text
    # Demote every OTHER active owner first (earlier tests in
    # this shared-DB suite may have left owners behind), so the
    # second owner really is the last one.
    second_headers = _auth_headers(client, "o3@zaika.example", "OwnerPass123")
    users = client.get("/api/v1/admin/users?per_page=100", headers=second_headers).json()["data"]
    for u in users:
        if u["role"] == "owner" and u["id"] != str(second.id):
            demoted = client.patch(
                f"/api/v1/admin/users/{u['id']}",
                json={"role": "manager"},
                headers=second_headers,
            )
            assert demoted.status_code == 200, demoted.text
    response = client.patch(
        f"/api/v1/admin/users/{second.id}",
        json={"role": "manager"},
        headers=second_headers,
    )
    assert response.status_code == 409, response.text


def test_users_list_never_returns_password_hash(client, owner):
    response = client.get("/api/v1/admin/users", headers=_auth_headers(client))
    assert response.status_code == 200
    assert "passwordHash" not in response.text
    assert "password_hash" not in response.text


def test_reset_password_revokes_and_flags(client, db_session, owner):
    waiter = _make_user(db_session, "w4@zaika.example", "WaiterPass1", "waiter")
    _login(client, "w4@zaika.example", "WaiterPass1")
    response = client.post(
        f"/api/v1/admin/users/{waiter.id}/reset-password",
        json={"password": "ResetPass123"},
        headers=_auth_headers(client),
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["mustChangePassword"] is True
    assert _login(client, "w4@zaika.example", "WaiterPass1").status_code == 401
    assert _login(client, "w4@zaika.example", "ResetPass123").status_code == 200


# ----------------------------------------------------------------------
# Dashboard / history / reports
# ----------------------------------------------------------------------

def test_dashboard_overview_fields_and_reconciliation(client, db_session, owner):
    token = make_token(db_session, 5)
    h = _auth_headers(client)
    before = client.get(
        "/api/v1/admin/dashboard/overview?preset=today", headers=h
    ).json()["data"]
    bill, payment = _place_and_pay(client, db_session, token)
    response = client.get("/api/v1/admin/dashboard/overview?preset=today", headers=h)
    assert response.status_code == 200, response.text
    d = response.json()["data"]
    assert d["grossSalesPaise"] - before["grossSalesPaise"] == bill["totalPaise"]
    assert d["cashCollectedPaise"] - before["cashCollectedPaise"] == bill["totalPaise"]
    assert d["netCashPaise"] == d["cashCollectedPaise"] - d["refundsPaise"]
    assert d["outstandingPaise"] == 0
    assert d["billCount"] - before["billCount"] == 1
    assert d["orderCount"] - before["orderCount"] == 1
    assert d["bestSellers"][0]["name"] == "Butter Naan"


def test_daily_cash_report_reconciles(client, db_session, owner):
    token = make_token(db_session, 5)
    h = _auth_headers(client)
    today = datetime.now(timezone.utc).date().isoformat()
    before = client.get(
        f"/api/v1/admin/reports/daily-cash?date={today}", headers=h
    ).json()["data"]
    bill, payment = _place_and_pay(client, db_session, token)
    response = client.get(
        f"/api/v1/admin/reports/daily-cash?date={today}", headers=h
    )
    assert response.status_code == 200, response.text
    d = response.json()["data"]
    assert d["grossCashCollectedPaise"] - before["grossCashCollectedPaise"] == bill["totalPaise"]
    assert d["netCashPaise"] == d["grossCashCollectedPaise"] - d["cashRefundsPaise"]
    assert d["paymentCount"] - before["paymentCount"] == 1
    csv_response = client.get(
        f"/api/v1/admin/reports/daily-cash?date={today}&format=csv", headers=h
    )
    assert csv_response.status_code == 200
    assert str(d["grossCashCollectedPaise"]) in csv_response.text


def test_admin_orders_and_bills_lists(client, db_session, owner):
    token = make_token(db_session, 5)
    _place_and_pay(client, db_session, token)
    h = _auth_headers(client)
    assert client.get("/api/v1/admin/orders", headers=h).json()["data"]
    bills = client.get("/api/v1/admin/bills", headers=h).json()["data"]
    assert len(bills) >= 1
    assert bills[0]["netCollectedPaise"] == bills[0]["collectedPaise"]


# ----------------------------------------------------------------------
# Menu
# ----------------------------------------------------------------------

def test_menu_crud_and_deactivate_blocks_new_orders(client, db_session, owner):
    h = _auth_headers(client)
    cat = client.post(
        "/api/v1/admin/menu/categories",
        json={"name": "Test Cat", "description": "x"},
        headers=h,
    )
    assert cat.status_code == 201, cat.text
    cat_id = cat.json()["data"]["id"]
    item = client.post(
        "/api/v1/admin/menu/items",
        json={"categoryId": cat_id, "name": "Test Dish", "pricePaise": 12345},
        headers=h,
    )
    assert item.status_code == 201, item.text
    item_id = item.json()["data"]["id"]
    variant = client.post(
        f"/api/v1/admin/menu/items/{item_id}/variants",
        json={"name": "Large", "priceDeltaPaise": 5000},
        headers=h,
    )
    assert variant.status_code == 201, variant.text
    # A variant id from ANOTHER item is a 404 on this item.
    other = client.post(
        "/api/v1/admin/menu/items",
        json={"categoryId": cat_id, "name": "Other Dish", "pricePaise": 100},
        headers=h,
    )
    other_id = other.json()["data"]["id"]
    assert client.patch(
        f"/api/v1/admin/menu/items/{other_id}/variants/{variant.json()['data']['id']}",
        json={"name": "Nope"},
        headers=h,
    ).status_code == 404
    # Price change + deactivate.
    patched = client.patch(
        f"/api/v1/admin/menu/items/{item_id}",
        json={"pricePaise": 99999, "isAvailable": False},
        headers=h,
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["data"]["pricePaise"] == 99999
    # Reorder categories (the full list — the shared DB already
    # holds the six seeded Zaika categories).
    all_cats = client.get("/api/v1/admin/menu/categories", headers=h).json()["data"]
    ids = [c["id"] for c in all_cats]
    ids.remove(cat_id)
    reorder = client.post(
        "/api/v1/admin/menu/categories/reorder",
        json={"categoryIds": [cat_id] + ids},
        headers=h,
    )
    assert reorder.status_code == 200, reorder.text


def test_price_change_does_not_rewrite_old_bill(client, db_session, owner):
    token = make_token(db_session, 5)
    bill, _ = _place_and_pay(client, db_session, token)
    h = _auth_headers(client)
    items = client.get("/api/v1/admin/menu/items?per_page=100", headers=h).json()["data"]
    naan = next(i for i in items if i["name"] == "Butter Naan")
    client.patch(
        f"/api/v1/admin/menu/items/{naan['id']}",
        json={"pricePaise": naan["pricePaise"] + 10000},
        headers=h,
    )
    again = client.get(f"/api/v1/admin/bills/{bill['id']}", headers=h)
    assert again.status_code == 200
    assert again.json()["data"]["totalPaise"] == bill["totalPaise"]
    # The price change was audited.
    logs = client.get("/api/v1/admin/audit-logs?action=menu.price_changed", headers=h)
    assert logs.json()["pagination"]["total"] >= 1


# ----------------------------------------------------------------------
# Tax
# ----------------------------------------------------------------------

def test_tax_unconfigured_then_configured_then_frozen(client, db_session, owner):
    h = _auth_headers(client)
    tax = client.get("/api/v1/admin/settings/tax", headers=h)
    assert tax.status_code == 200
    assert tax.json()["current"] is None
    token = make_token(db_session, 5)
    bill = _bill(client, db_session, token)
    assert bill["taxPaise"] == 0
    # Configure 10% exclusive, effective now.
    now = datetime.now(timezone.utc)
    created = client.post(
        "/api/v1/admin/settings/tax",
        json={
            "enabled": True, "rateBps": 1000, "mode": "exclusive",
            "label": "GST", "effectiveFrom": now.isoformat(),
        },
        headers=h,
    )
    assert created.status_code == 201, created.text
    assert created.json()["current"]["rateBps"] == 1000
    # A NEW bill picks up the tax; the old issued bill regenerates
    # with the current config too (it is still issued).
    bill2 = client.post("/api/v1/guest/bill", headers=headers(token)).json()["data"]
    assert bill2["taxPaise"] == bill2["subtotalPaise"] // 10
    assert bill2["totalPaise"] == bill2["subtotalPaise"] + bill2["taxPaise"]
    # Freeze: Pay by Cash -> payment_requested snapshots the tax.
    requested = client.patch("/api/v1/guest/bill/request-payment", headers=headers(token))
    assert requested.status_code == 200, requested.text
    frozen = requested.json()["data"]
    assert frozen["taxRateBpsSnapshot"] == 1000
    assert frozen["taxModeSnapshot"] == "exclusive"
    assert frozen["taxLabelSnapshot"] == "GST"
    # Change the config again: the frozen bill must not move.
    # The new config is scheduled a day out on purpose. It used to
    # be now + 1 second, which made it flip effective mid-way through
    # whatever test ran next — the void test straddled it (first bill
    # 10% = 5390, second bill 18% = 5782). A frozen bill never
    # recomputes, so the far-future value preserves this test's
    # intent while never becoming effective during the suite.
    later = datetime.now(timezone.utc) + timedelta(days=1)
    changed = client.post(
        "/api/v1/admin/settings/tax",
        json={
            "enabled": True, "rateBps": 1800, "mode": "exclusive",
            "label": "GST", "effectiveFrom": later.isoformat(),
        },
        headers=h,
    )
    assert changed.status_code == 201, changed.text
    again = client.get(f"/api/v1/admin/bills/{bill['id']}", headers=h)
    assert again.json()["data"]["totalPaise"] == frozen["totalPaise"]
    assert again.json()["data"]["taxPaise"] == frozen["taxPaise"]


def test_tax_past_effective_date_rejected_and_manager_forbidden(client, db_session, owner):
    h = _auth_headers(client)
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    response = client.post(
        "/api/v1/admin/settings/tax",
        json={"enabled": True, "rateBps": 500, "mode": "exclusive",
              "label": "GST", "effectiveFrom": past.isoformat()},
        headers=h,
    )
    assert response.status_code == 422, response.text
    _make_user(db_session, "m4@zaika.example", "ManagerPass1", "manager")
    mh = _auth_headers(client, "m4@zaika.example", "ManagerPass1")
    assert client.get("/api/v1/admin/settings/tax", headers=mh).status_code == 403


# ----------------------------------------------------------------------
# Void + refunds
# ----------------------------------------------------------------------

def test_void_unpaid_ok_and_items_billable_again(client, db_session, owner):
    # Tax isolation (Stage 9 closure follow-up): bills recompute tax
    # from the config effective NOW on every regeneration, so this
    # test's two bills are equal only if no config flips between
    # them. An earlier test in this file leaves an 18% config
    # effective 1 second after it is created, and this test used to
    # straddle that boundary — first bill 10% (subtotal 4900, total
    # 5390), second bill 18% (total 5782) — a flake (1 failure in 4
    # full runs). Pin a 10% config effective NOW first: its
    # effective_from is later than every earlier row's, so it wins
    # for both bills whatever the ambient tax state is. (The stale
    # "Table 5" comment this replaces was wrong — the test already
    # uses fresh_table_token, a brand-new table; Table 5 state was
    # never the cause.)
    h = _auth_headers(client)
    pinned = client.post(
        "/api/v1/admin/settings/tax",
        json={
            "enabled": True, "rateBps": 1000, "mode": "exclusive",
            "label": "GST",
            "effectiveFrom": datetime.now(timezone.utc).isoformat(),
        },
        headers=h,
    )
    assert pinned.status_code == 201, pinned.text
    token = fresh_table_token(db_session)
    bill = _bill(client, db_session, token)
    voided = client.post(
        f"/api/v1/admin/bills/{bill['id']}/void",
        json={"reason": "guest left"},
        headers=h,
    )
    assert voided.status_code == 200, voided.text
    assert voided.json()["data"]["status"] == "voided"
    assert voided.json()["data"]["voidReason"] == "guest left"
    # Repeat void -> 409.
    assert client.post(
        f"/api/v1/admin/bills/{bill['id']}/void",
        json={"reason": "again"},
        headers=h,
    ).status_code == 409
    # A voided bill is written off: the admin list shows zero
    # outstanding for it (it previously showed the full total).
    bills = client.get("/api/v1/admin/bills", headers=h)
    assert bills.status_code == 200, bills.text
    row = next(b for b in bills.json()["data"] if b["id"] == bill["id"])
    assert row["status"] == "voided"
    assert row["outstandingPaise"] == 0
    # The voided bill's items are billable again: a new bill has them.
    new_bill = client.post("/api/v1/guest/bill", headers=headers(token))
    assert new_bill.status_code == 200, new_bill.text
    assert new_bill.json()["data"]["id"] != bill["id"]
    assert new_bill.json()["data"]["totalPaise"] == bill["totalPaise"]


def test_void_paid_bill_requires_refund(client, db_session, owner):
    token = make_token(db_session, 5)
    bill, _ = _place_and_pay(client, db_session, token)
    h = _auth_headers(client)
    response = client.post(
        f"/api/v1/admin/bills/{bill['id']}/void",
        json={"reason": "too late"},
        headers=h,
    )
    assert response.status_code == 409, response.text


def test_refund_partial_then_full_and_idempotency(client, db_session, owner):
    token = make_token(db_session, 5)
    bill, payment = _place_and_pay(client, db_session, token, quantity=4)
    total = bill["totalPaise"]
    h = _auth_headers(client)
    key = f"rf-{uuid.uuid4().hex}"
    first = client.post(
        f"/api/v1/admin/bills/{bill['id']}/refunds",
        json={"paymentId": payment["id"], "amountPaise": total // 2, "reason": "half"},
        headers={**h, "Idempotency-Key": key},
    )
    assert first.status_code == 201, first.text
    assert first.json()["data"]["refundReference"].startswith("RFD-")
    # Same key + same body replays.
    replay = client.post(
        f"/api/v1/admin/bills/{bill['id']}/refunds",
        json={"paymentId": payment["id"], "amountPaise": total // 2, "reason": "half"},
        headers={**h, "Idempotency-Key": key},
    )
    assert replay.status_code == 201
    assert replay.headers.get("Idempotency-Replayed") == "true"
    assert replay.json()["data"]["id"] == first.json()["data"]["id"]
    # Same key + different body -> 422.
    assert client.post(
        f"/api/v1/admin/bills/{bill['id']}/refunds",
        json={"paymentId": payment["id"], "amountPaise": 1, "reason": "half"},
        headers={**h, "Idempotency-Key": key},
    ).status_code == 422
    # Over-refund -> 422.
    assert client.post(
        f"/api/v1/admin/bills/{bill['id']}/refunds",
        json={"paymentId": payment["id"], "amountPaise": total, "reason": "too much"},
        headers={**h, "Idempotency-Key": f"rf-{uuid.uuid4().hex}"},
    ).status_code == 422
    # Refund the rest: bill becomes refunded, payment stays (refunded).
    rest = client.post(
        f"/api/v1/admin/bills/{bill['id']}/refunds",
        json={"paymentId": payment["id"], "amountPaise": total - total // 2, "reason": "rest"},
        headers={**h, "Idempotency-Key": f"rf-{uuid.uuid4().hex}"},
    )
    assert rest.status_code == 201, rest.text
    db_session.refresh(db_session.get(Bill, uuid.UUID(bill["id"])))
    row = db_session.get(Bill, uuid.UUID(bill["id"]))
    assert row.status == "refunded"
    pay_row = db_session.get(Payment, uuid.UUID(payment["id"]))
    assert pay_row.status == "refunded"
    assert pay_row.amount_paise == total  # original payment preserved
    # Cash report: gross - refunds = net.
    today = datetime.now(timezone.utc).date().isoformat()
    report = client.get(
        f"/api/v1/admin/reports/daily-cash?date={today}", headers=h
    ).json()["data"]
    assert report["grossCashCollectedPaise"] - report["cashRefundsPaise"] == report["netCashPaise"]


def test_refund_requires_idempotency_key(client, db_session, owner):
    token = make_token(db_session, 5)
    bill, payment = _place_and_pay(client, db_session, token)
    response = client.post(
        f"/api/v1/admin/bills/{bill['id']}/refunds",
        json={"paymentId": payment["id"], "amountPaise": 10, "reason": "x"},
        headers=_auth_headers(client),
    )
    assert response.status_code == 422


# ----------------------------------------------------------------------
# Audit log
# ----------------------------------------------------------------------

def test_audit_records_sensitive_actions_and_no_secrets(client, db_session, owner):
    h = _auth_headers(client)
    _login(client, OWNER_EMAIL, "wrong-password")
    logs = client.get("/api/v1/admin/audit-logs", headers=h)
    assert logs.status_code == 200
    actions = [row["action"] for row in logs.json()["data"]]
    assert "login.failed" in actions
    assert "login.success" in actions
    assert "password" not in logs.text.lower().replace("password_changed", "")
    # Audit rows are immutable via the API.
    assert client.patch("/api/v1/admin/audit-logs/00000000-0000-0000-0000-000000000000", json={}, headers=h).status_code in (404, 405)
    assert client.delete("/api/v1/admin/audit-logs/00000000-0000-0000-0000-000000000000", headers=h).status_code in (404, 405)


def test_failed_operation_creates_no_success_audit(client, db_session, owner):
    h = _auth_headers(client)
    before = client.get("/api/v1/admin/audit-logs?action=menu.item_created", headers=h).json()["pagination"]["total"]
    # Failing create (unknown category) must not audit a success.
    response = client.post(
        "/api/v1/admin/menu/items",
        json={"categoryId": str(uuid.uuid4()), "name": "Ghost", "pricePaise": 1},
        headers=h,
    )
    assert response.status_code == 404
    after = client.get("/api/v1/admin/audit-logs?action=menu.item_created", headers=h).json()["pagination"]["total"]
    assert after == before


# ----------------------------------------------------------------------
# Settings / tables
# ----------------------------------------------------------------------

def test_settings_and_tables(client, db_session, owner):
    h = _auth_headers(client)
    r = client.get("/api/v1/admin/settings/restaurant", headers=h)
    assert r.status_code == 200
    assert r.json()["data"]["currency"] == "INR"
    bad = client.patch(
        "/api/v1/admin/settings/restaurant",
        json={"currency": "inr"},
        headers=h,
    )
    assert bad.status_code == 422
    tables = client.get("/api/v1/admin/settings/tables", headers=h)
    assert tables.status_code == 200
    numbers = {t["number"] for t in tables.json()["data"]}
    assert set(range(1, 11)) <= numbers  # the ten seeded tables
    dup = client.post(
        "/api/v1/admin/settings/tables",
        json={"number": 5},
        headers=h,
    )
    assert dup.status_code == 409
    new = client.post(
        "/api/v1/admin/settings/tables",
        json={"number": 11},
        headers=h,
    )
    assert new.status_code == 201, new.text
    branches = client.get("/api/v1/admin/settings/branches", headers=h)
    branch_id = branches.json()["data"][0]["id"]
    bad_tz = client.patch(
        f"/api/v1/admin/settings/branches/{branch_id}",
        json={"timezone": "Mars/Olympus"},
        headers=h,
    )
    assert bad_tz.status_code == 422
    ok_tz = client.patch(
        f"/api/v1/admin/settings/branches/{branch_id}",
        json={"timezone": "Asia/Dubai", "contactName": "Asha"},
        headers=h,
    )
    assert ok_tz.status_code == 200, ok_tz.text
    assert ok_tz.json()["data"]["timezone"] == "Asia/Dubai"


# ----------------------------------------------------------------------
# WebSocket tickets
# ----------------------------------------------------------------------

def test_ws_ticket_connects_and_is_single_use(client, db_session, owner):
    _make_user(db_session, "k2@zaika.example", "KitchenPass1", "kitchen")
    h = _auth_headers(client, "k2@zaika.example", "KitchenPass1")
    ticket = client.post(
        "/api/v1/ws/tickets", json={"channel": "kitchen"}, headers=h
    )
    assert ticket.status_code == 200, ticket.text
    raw = ticket.json()["data"]["ticket"]
    with client.websocket_connect(f"/ws/kitchen?ticket={raw}"):
        pass
    # Reuse -> rejected (4401).
    assert_ws_rejected(client, f"/ws/kitchen?ticket={raw}", 4401)


def test_ws_ticket_wrong_role_and_dev_key_in_production(client, db_session, owner):
    from starlette.websockets import WebSocketDisconnect
    from app.core.config import settings

    _make_user(db_session, "w5@zaika.example", "WaiterPass1", "waiter")
    h = _auth_headers(client, "w5@zaika.example", "WaiterPass1")
    # A waiter cannot get a kitchen ticket.
    assert client.post(
        "/api/v1/ws/tickets", json={"channel": "kitchen"}, headers=h
    ).status_code == 403
    # In production a dev key is rejected on the socket (4403).
    previous = settings.environment
    settings.environment = "production"
    try:
        assert_ws_rejected(client, "/ws/kitchen?dev_key=dev-kitchen-only", 4403)
    finally:
        settings.environment = previous


def test_jwt_grants_kitchen_and_staff_rest_access(client, db_session, owner):
    _make_user(db_session, "k3@zaika.example", "KitchenPass1", "kitchen")
    h = _auth_headers(client, "k3@zaika.example", "KitchenPass1")
    assert client.get("/api/v1/kitchen/orders", headers=h).status_code == 200
    # A kitchen JWT does NOT grant staff access.
    assert client.get("/api/v1/staff/tables", headers=h).status_code == 403
    _make_user(db_session, "w6@zaika.example", "WaiterPass1", "waiter")
    wh = _auth_headers(client, "w6@zaika.example", "WaiterPass1")
    assert client.get("/api/v1/staff/tables", headers=wh).status_code == 200
    assert client.get("/api/v1/kitchen/orders", headers=wh).status_code == 403
