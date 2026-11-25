"""Stage 9 — RBAC + tenant negative tests (role x endpoint matrix).

Every protected endpoint is probed with: no credentials (401),
a valid JWT for a role that lacks the capability (403, or the
endpoint's DESIGNED code — cross-tenant reads are 404 by the
api-plan.md 404-vs-403 rule, an explicit foreign restaurant_id on
admin lists is 403 by ``resolve_admin_restaurant``), a guest table
token (401), and the wrong dev key (401/403). The DESIGNED code is
asserted — no endpoint behaviour was changed to fit these tests.
"""
import uuid

import pytest
from sqlalchemy import select

from app.models import Restaurant, User
from tests.helpers import headers, make_token
from tests.sec9 import (
    auth_headers,
    drop_second_restaurant,
    make_second_restaurant,
    make_user,
    place_bill,
    seed,
    user_headers,
    zaika_id,
    cleanup_placed_orders,  # noqa: F401  (autouse fixture)
)

STAFF_KEY = {"X-Staff-Dev-Key": "dev-staff-only"}
KITCHEN_KEY = {"X-Kitchen-Dev-Key": "dev-kitchen-only"}


@pytest.fixture
def owner_h(client, db_session):
    seed(db_session)
    h, _ = user_headers(client, db_session, "owner")
    return h


# --- Admin endpoints: unauthenticated + wrong role ----------------------

ADMIN_PROBES = [
    # (method, path, allowed roles) — allowed roles get 200/201/404-by-id
    # (not 401/403); every other role must get 403.
    ("GET", "/api/v1/admin/dashboard/overview", ("owner", "manager")),
    ("GET", "/api/v1/admin/orders", ("owner", "manager")),
    ("GET", "/api/v1/admin/bills", ("owner", "manager")),
    ("GET", "/api/v1/admin/payments", ("owner", "manager")),
    ("GET", "/api/v1/admin/refunds", ("owner", "manager")),
    ("GET", "/api/v1/admin/audit-logs", ("owner", "manager")),
    ("GET", "/api/v1/admin/menu/categories", ("owner", "manager")),
    ("GET", "/api/v1/admin/menu/items", ("owner", "manager")),
    ("GET", "/api/v1/admin/users", ("owner",)),  # manager needs flag
    ("GET", "/api/v1/admin/settings/restaurant", ("owner",)),  # manager flag
    ("GET", "/api/v1/admin/settings/branches", ("owner",)),
    ("GET", "/api/v1/admin/settings/tables", ("owner",)),
    ("GET", "/api/v1/admin/settings/tax", ("owner",)),  # owner-only capability
]


@pytest.mark.parametrize("method,path,allowed", ADMIN_PROBES)
def test_admin_endpoints_unauthenticated_401(client, db_session, method, path, allowed):
    seed(db_session)
    response = client.request(method, path)
    assert response.status_code == 401, (method, path, response.text)
    assert response.json()["error"]["code"] == "unauthenticated"


@pytest.mark.parametrize("method,path,allowed", ADMIN_PROBES)
def test_admin_endpoints_wrong_role_403(client, db_session, method, path, allowed):
    seed(db_session)
    for role in ("waiter", "kitchen"):
        h, _ = user_headers(client, db_session, role)
        response = client.request(method, path, headers=h)
        assert response.status_code == 403, (method, path, role, response.text)
    # Manager without flags is denied on the owner/flag-gated paths.
    if "manager" not in allowed:
        h, _ = user_headers(client, db_session, "manager")
        response = client.request(method, path, headers=h)
        assert response.status_code == 403, (method, path, "manager", response.text)


@pytest.mark.parametrize("method,path,allowed", ADMIN_PROBES)
def test_admin_endpoints_allowed_role_not_denied(client, db_session, method, path, allowed):
    seed(db_session)
    for role in allowed:
        flags = None
        if role == "manager" and path in (
            "/api/v1/admin/users",
            "/api/v1/admin/settings/restaurant",
            "/api/v1/admin/settings/branches",
            "/api/v1/admin/settings/tables",
        ):
            flags = {"can_manage_staff": True, "can_manage_settings": True}
        h, _ = user_headers(client, db_session, role, flags=flags)
        response = client.request(method, path, headers=h)
        assert response.status_code in (200, 201), (method, path, role, response.text)


def test_admin_mutations_wrong_role_403(client, db_session, owner_h):
    """Mutating admin endpoints: waiter/kitchen/manager-no-flag -> 403.
    A random UUID id is used where an id is required — the permission
    check runs before the resource lookup, so 403 (not 404) is the
    designed outcome."""
    seed(db_session)
    rid = uuid.uuid4()
    # Denied for waiter, kitchen AND manager (no flag / owner-only).
    denied_all = [
        ("POST", "/api/v1/admin/users",
         {"email": "n@x.example", "password": "SomePass123", "role": "waiter"}),
        ("POST", f"/api/v1/admin/users/{rid}/deactivate", None),
        ("POST", f"/api/v1/admin/users/{rid}/reset-password", {"password": "ResetPass123"}),
        ("POST", f"/api/v1/admin/bills/{rid}/void", {"reason": "x"}),
        ("POST", f"/api/v1/admin/bills/{rid}/refunds",
         {"paymentId": str(rid), "amountPaise": 1, "reason": "x"}),
        ("POST", "/api/v1/admin/settings/tax",
         {"enabled": True, "rateBps": 500, "mode": "exclusive",
          "label": "GST", "effectiveFrom": "2030-01-01T00:00:00Z"}),
        ("PATCH", "/api/v1/admin/settings/restaurant", {"name": "X"}),
    ]
    # Denied for waiter/kitchen but ALLOWED for manager (manage_menu
    # is in the manager base set — architecture.md § 8.3).
    denied_staff_only = [
        ("POST", "/api/v1/admin/menu/categories", {"name": "X"}),
        ("POST", "/api/v1/admin/menu/items",
         {"categoryId": str(rid), "name": "X", "pricePaise": 100}),
    ]
    for role in ("waiter", "kitchen", "manager"):
        h, _ = user_headers(client, db_session, role)
        for method, path, body in denied_all:
            kwargs = {"headers": h}
            if body is not None:
                kwargs["json"] = body
            # Refunds also need an idempotency key to reach the
            # permission check first — without it the router returns
            # 422 before the service; permission is a dependency so
            # 403 still wins. No key is sent on purpose: 403 proves
            # the dep runs before the handler body check.
            response = client.request(method, path, **kwargs)
            assert response.status_code == 403, (method, path, role, response.text)
    for role in ("waiter", "kitchen"):
        h, _ = user_headers(client, db_session, role)
        for method, path, body in denied_staff_only:
            response = client.request(method, path, headers=h, json=body)
            assert response.status_code == 403, (method, path, role, response.text)


def test_manager_flag_gates_refund_void_and_tax(client, db_session):
    seed(db_session)
    rid = uuid.uuid4()
    # Manager WITH can_refund may call refunds (404: bill unknown —
    # permission passed, lookup failed, which is the designed code).
    h, _ = user_headers(client, db_session, "manager", flags={"can_refund": True})
    response = client.post(
        f"/api/v1/admin/bills/{rid}/refunds",
        json={"paymentId": str(rid), "amountPaise": 1, "reason": "x"},
        headers={**h, "Idempotency-Key": f"sec9-{uuid.uuid4().hex}"},
    )
    assert response.status_code == 404, response.text
    # Manager with can_void may call void (404 for the same reason).
    h2, _ = user_headers(client, db_session, "manager", flags={"can_void": True})
    response = client.post(
        f"/api/v1/admin/bills/{rid}/void", json={"reason": "x"}, headers=h2
    )
    assert response.status_code == 404, response.text
    # But configure_tax has NO manager flag: even a fully-flagged
    # manager is 403 (owner-only capability, architecture.md § 8.3).
    h3, _ = user_headers(
        client, db_session, "manager",
        flags={"can_manage_staff": True, "can_manage_settings": True,
               "can_refund": True, "can_void": True},
    )
    response = client.get("/api/v1/admin/settings/tax", headers=h3)
    assert response.status_code == 403, response.text


# --- Kitchen / staff: JWT vs dev key -------------------------------------

def test_kitchen_staff_jwt_role_gates(client, db_session):
    seed(db_session)
    kh, _ = user_headers(client, db_session, "kitchen")
    wh, _ = user_headers(client, db_session, "waiter")
    # Kitchen JWT: kitchen yes, staff no.
    assert client.get("/api/v1/kitchen/orders", headers=kh).status_code == 200
    assert client.get("/api/v1/staff/tables", headers=kh).status_code == 403
    # Waiter JWT: staff yes, kitchen no.
    assert client.get("/api/v1/staff/tables", headers=wh).status_code == 200
    assert client.get("/api/v1/kitchen/orders", headers=wh).status_code == 403
    # No credentials at all: 401 on both.
    assert client.get("/api/v1/kitchen/orders").status_code == 401
    assert client.get("/api/v1/staff/tables").status_code == 401


def test_dev_keys_do_not_cross_channels(client, db_session):
    seed(db_session)
    # Kitchen key opens kitchen, not staff; staff key opens staff,
    # not kitchen. Wrong key -> 403; no key -> 401.
    assert client.get("/api/v1/kitchen/orders", headers=KITCHEN_KEY).status_code == 200
    assert client.get("/api/v1/staff/tables", headers=KITCHEN_KEY).status_code == 401
    assert client.get("/api/v1/staff/tables", headers=STAFF_KEY).status_code == 200
    assert client.get("/api/v1/kitchen/orders", headers=STAFF_KEY).status_code == 401
    assert client.get(
        "/api/v1/kitchen/orders", headers={"X-Kitchen-Dev-Key": "wrong"}
    ).status_code == 403
    assert client.get(
        "/api/v1/staff/tables", headers={"X-Staff-Dev-Key": "wrong"}
    ).status_code == 403


def test_dev_key_never_opens_admin_paths(client, db_session):
    seed(db_session)
    for key_headers in (STAFF_KEY, KITCHEN_KEY):
        assert client.get(
            "/api/v1/admin/dashboard/overview", headers=key_headers
        ).status_code == 401
        assert client.get(
            "/api/v1/admin/users", headers=key_headers
        ).status_code == 401


def test_jwt_wins_over_dev_key_when_both_present(client, db_session):
    """A waiter JWT + a valid kitchen dev key is still 403 on the
    kitchen: when a Bearer token is present the JWT path decides
    (deps.py _jwt_or_dev_key), the dev key is not a fallback."""
    seed(db_session)
    wh, _ = user_headers(client, db_session, "waiter")
    response = client.get(
        "/api/v1/kitchen/orders", headers={**wh, **KITCHEN_KEY}
    )
    assert response.status_code == 403, response.text


def test_staff_payment_and_kitchen_patch_role_gates(client, db_session):
    seed(db_session)
    bill, _, _ = place_bill(client, db_session)
    kh, _ = user_headers(client, db_session, "kitchen")
    # Kitchen JWT cannot record a cash payment (needs serve_orders).
    response = client.post(
        f"/api/v1/staff/bills/{bill['id']}/payments?restaurant_id={zaika_id(db_session)}",
        json={"amountPaise": 100},
        headers={**kh, "Idempotency-Key": f"sec9-{uuid.uuid4().hex}"},
    )
    assert response.status_code == 403, response.text
    # Kitchen JWT cannot serve either (serve is staff-channel, but
    # the JWT permission check is the same gate).
    assert client.get("/api/v1/staff/orders", headers=kh).status_code == 403
    # No auth on the payment endpoint: 401 (before idempotency check).
    response = client.post(
        f"/api/v1/staff/bills/{bill['id']}/payments?restaurant_id={zaika_id(db_session)}",
        json={"amountPaise": 100},
    )
    assert response.status_code == 401, response.text


# --- Guest table token grants nothing on staff/kitchen/admin -------------

def test_guest_table_token_denied_on_staff_paths(client, db_session):
    seed(db_session)
    token = make_token(db_session, 5)
    guest = headers(token)  # X-Table-Token only
    for method, path in [
        ("GET", "/api/v1/admin/dashboard/overview"),
        ("GET", "/api/v1/admin/users"),
        ("GET", "/api/v1/staff/tables"),
        ("GET", "/api/v1/kitchen/orders"),
        ("GET", "/api/v1/auth/me"),
    ]:
        response = client.request(method, path, headers=guest)
        assert response.status_code == 401, (method, path, response.text)
    # The raw guest token as a Bearer token is not a JWT either.
    bearer = {"Authorization": f"Bearer {token}"}
    assert client.get("/api/v1/admin/dashboard/overview", headers=bearer).status_code == 401
    assert client.get("/api/v1/staff/tables", headers=bearer).status_code == 401


# --- Cross-restaurant ------------------------------------------------------

def test_cross_restaurant_admin_denied(client, db_session):
    seed(db_session)
    bill, _, _ = place_bill(client, db_session)
    other, branch = make_second_restaurant(db_session)
    b_owner, b_pass = make_user(
        db_session, "owner", restaurant_id=other.id, branch_id=branch.id,
        email=f"bistro-owner-{uuid.uuid4().hex[:8]}@example.invalid",
    )
    b_headers = auth_headers(client, b_owner.email, b_pass)
    try:
        zaika = zaika_id(db_session)
        # Explicit foreign restaurant_id on a list -> 403 (designed:
        # resolve_admin_restaurant).
        response = client.get(
            f"/api/v1/admin/dashboard/overview?restaurant_id={zaika}",
            headers=b_headers,
        )
        assert response.status_code == 403, response.text
        response = client.get(
            f"/api/v1/admin/orders?restaurant_id={zaika}", headers=b_headers
        )
        assert response.status_code == 403, response.text
        # A Zaika bill id through B's admin -> 404 (existence is not
        # leaked across tenants, api-plan.md 404-vs-403 rule).
        response = client.get(f"/api/v1/admin/bills/{bill['id']}", headers=b_headers)
        assert response.status_code == 404, response.text
        # A Zaika user id through B's admin -> 404.
        zaika_owner = db_session.execute(
            select(User).where(User.restaurant_id == zaika, User.role == "owner")
        ).scalars().first()
        if zaika_owner is not None:
            response = client.post(
                f"/api/v1/admin/users/{zaika_owner.id}/deactivate", headers=b_headers
            )
            assert response.status_code == 404, response.text
        # B's own dashboard (no explicit id) works — scoping is to
        # the caller's restaurant, not a denial of everything.
        assert client.get(
            "/api/v1/admin/dashboard/overview", headers=b_headers
        ).status_code == 200
    finally:
        drop_second_restaurant(db_session, other, branch, users=[b_owner])


def test_cross_restaurant_kitchen_and_staff_404(client, db_session):
    seed(db_session)
    bill, _, _ = place_bill(client, db_session)
    other, branch = make_second_restaurant(db_session)
    b_kitchen, b_pass = make_user(
        db_session, "kitchen", restaurant_id=other.id, branch_id=branch.id,
        email=f"bistro-kitchen-{uuid.uuid4().hex[:8]}@example.invalid",
    )
    b_headers = auth_headers(client, b_kitchen.email, b_pass)
    try:
        zaika = zaika_id(db_session)
        # B's kitchen user asking for Zaika's queue -> 404.
        response = client.get(
            f"/api/v1/kitchen/orders?restaurant_id={zaika}", headers=b_headers
        )
        assert response.status_code == 404, response.text
        # B's kitchen user asking for Zaika's bill via staff -> 404
        # (staff channel needs serve_orders; kitchen lacks it, so
        # the designed code here is 403 — assert exactly that).
        response = client.get(
            f"/api/v1/staff/bills/{bill['id']}?restaurant_id={zaika}",
            headers=b_headers,
        )
        assert response.status_code == 403, response.text
    finally:
        drop_second_restaurant(db_session, other, branch, users=[b_kitchen])


def test_cross_restaurant_with_dev_key_scoping(client, db_session):
    """Dev-key calls are NOT tenant-scoped by a user — but the
    restaurant_id query param still scopes them: a dev-key call
    for a foreign restaurant id returns that restaurant's data
    only when the id exists, and 404 for an unknown id. (Dev keys
    are non-production only; this pins the current behaviour.)"""
    seed(db_session)
    other, branch = make_second_restaurant(db_session)
    try:
        # Unknown restaurant id -> 404.
        response = client.get(
            f"/api/v1/kitchen/orders?restaurant_id={uuid.uuid4()}",
            headers=KITCHEN_KEY,
        )
        assert response.status_code == 404, response.text
        # The second restaurant's own queue is empty but reachable.
        response = client.get(
            f"/api/v1/kitchen/orders?restaurant_id={other.id}",
            headers=KITCHEN_KEY,
        )
        assert response.status_code == 200, response.text
        assert response.json()["data"] == []
    finally:
        drop_second_restaurant(db_session, other, branch)


# --- WS tickets: role gate ---------------------------------------------------

def test_ws_ticket_role_gate(client, db_session):
    seed(db_session)
    wh, _ = user_headers(client, db_session, "waiter")
    # Waiter cannot get a kitchen ticket (403); kitchen user can.
    assert client.post(
        "/api/v1/ws/tickets", json={"channel": "kitchen"}, headers=wh
    ).status_code == 403
    kh, _ = user_headers(client, db_session, "kitchen")
    assert client.post(
        "/api/v1/ws/tickets", json={"channel": "kitchen"}, headers=kh
    ).status_code == 200
    # No token -> 401; bad channel value -> 422.
    assert client.post("/api/v1/ws/tickets", json={"channel": "kitchen"}).status_code == 401
    assert client.post(
        "/api/v1/ws/tickets", json={"channel": "guest"}, headers=kh
    ).status_code == 422
