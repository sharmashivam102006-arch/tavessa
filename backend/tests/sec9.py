"""Shared helpers for the Stage 9 security suite (not a test module).

All helpers use the shared conftest SQLite DB. Users get unique
emails per call so tests never collide on the (restaurant_id, email)
unique constraint, and guest flows use fresh tables so one test's
session/bill never leaks into another's.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.core.security import hash_password
from app.models import Branch, DiningTable, Restaurant, User
from app.seed.zaika import RESTAURANT_SLUG, seed_zaika
from tests.helpers import fresh_table_token, headers, make_token, menu_by_name, open_session


def seed(db_session):
    return seed_zaika(db_session)


def zaika_id(db_session):
    return (
        db_session.execute(
            select(Restaurant).where(Restaurant.slug == RESTAURANT_SLUG)
        )
        .scalars()
        .first()
        .id
    )


def zaika_branch_id(db_session):
    rid = zaika_id(db_session)
    return (
        db_session.execute(select(Branch).where(Branch.restaurant_id == rid))
        .scalars()
        .first()
        .id
    )


def make_user(db_session, role, password="Passw0rd123", flags=None,
              restaurant_id=None, branch_id=None, email=None):
    """Create + persist a user in Zaika (or the given restaurant)."""
    email = email or f"sec9-{role}-{uuid.uuid4().hex[:8]}@zaika.example"
    user = User(
        restaurant_id=restaurant_id or zaika_id(db_session),
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
    return user, password


def login(client, email, password):
    return client.post(
        "/api/v1/auth/login", json={"email": email, "password": password}
    )


def auth_headers(client, email, password="Passw0rd123"):
    response = login(client, email, password)
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['data']['accessToken']}"}


def user_headers(client, db_session, role, flags=None):
    user, password = make_user(db_session, role, flags=flags)
    return auth_headers(client, user.email, password), user


def audit_total(client, owner_headers, action):
    """Total audit rows for one action, via the admin read API."""
    response = client.get(
        f"/api/v1/admin/audit-logs?action={action}&per_page=1",
        headers=owner_headers,
    )
    assert response.status_code == 200, response.text
    return response.json()["pagination"]["total"]


def place_bill(client, db_session, token=None, item_name="Butter Naan", quantity=1):
    """Guest order -> bill. Returns (bill dict, menu item dict)."""
    token = token or fresh_table_token(db_session)
    open_session(client, token)
    menu = menu_by_name(client, token)[0]
    item = menu[item_name]
    response = client.post(
        "/api/v1/orders",
        json={"items": [{"menuItemId": item["id"], "quantity": quantity}]},
        headers={**headers(token), "Idempotency-Key": f"sec9-{uuid.uuid4().hex}"},
    )
    assert response.status_code == 201, response.text
    bill = client.post("/api/v1/guest/bill", headers=headers(token)).json()["data"]
    return bill, item, token


def pay_bill(client, db_session, bill, amount_paise=None, key=None):
    """Staff cash payment (dev key). Returns the response."""
    return client.post(
        f"/api/v1/staff/bills/{bill['id']}/payments?restaurant_id={zaika_id(db_session)}",
        json={"amountPaise": amount_paise if amount_paise is not None else bill["totalPaise"]},
        headers={
            "X-Staff-Dev-Key": "dev-staff-only",
            "Idempotency-Key": key or f"sec9-pay-{uuid.uuid4().hex}",
        },
    )


def make_second_restaurant(db_session):
    """A second restaurant (Bistro) + branch + owner, for
    cross-tenant tests. Caller must call ``drop_second_restaurant``
    in a finally block — the shared suite assumes a single
    restaurant for the restaurant_id-omission rule."""
    other = Restaurant(name="Bistro", slug="bistro-sec9")
    db_session.add(other)
    db_session.flush()
    branch = Branch(restaurant_id=other.id, name="Bistro — Main", slug="main")
    db_session.add(branch)
    db_session.commit()
    db_session.refresh(other)
    db_session.refresh(branch)
    return other, branch


def drop_second_restaurant(db_session, other, branch, users=()):
    for user in users:
        db_session.delete(user)
    db_session.delete(branch)
    db_session.delete(other)
    db_session.commit()


def future_iso(days=365):
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()


@pytest.fixture(autouse=True)
def cleanup_placed_orders(db_session):
    """Autouse cleanup for Stage 9 security test modules (import
    this fixture there): any order the test leaves in a
    non-terminal state (placed / preparing / ready) is moved to
    'cancelled' afterwards. The shared SQLite suite lists the
    kitchen queue oldest-first, page 1 = 20 rows — leftover placed
    orders from security tests would otherwise push later modules'
    orders off page 1 and break their tests (and the seed suite
    counts). Bills are untouched; only the order status changes,
    which is exactly what the queue filters on."""
    from app.models import Order

    before = {o.id for o in db_session.execute(select(Order)).scalars()}
    yield
    for row in db_session.execute(select(Order)).scalars():
        if row.id not in before and row.status in (
            "placed", "accepted", "preparing", "ready",
        ):
            row.status = "cancelled"
            row.cancelled_at = datetime.now(timezone.utc)
    db_session.commit()
