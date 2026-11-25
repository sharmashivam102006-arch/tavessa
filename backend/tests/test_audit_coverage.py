"""Stage 9 — audit coverage (item 7).

For every money/state mutation the checklist names, assert an
audit row is written (delta via the admin read API) AND that the
matching FAILED operation writes no success row. Audit actions
checked here exist in app/services/* and app/auth/service.py;
nothing was added to the audit service for this file — these tests
pin existing coverage. Two deliberate behaviours are pinned:
unknown-email login failures write NO audit row (no tenant to
scope it to — auth/service.py), and update_user always writes
``user.role_changed`` even for flag-only changes (existing quirk).
"""
import uuid
from datetime import datetime, timezone

from tests.sec9 import (
    audit_total,
    auth_headers,
    future_iso,
    login,
    make_user,
    pay_bill,
    place_bill,
    seed,
    user_headers,
    zaika_id,
    cleanup_placed_orders,  # noqa: F401  (autouse fixture)
)
from tests.helpers import headers


def _owner(client, db_session):
    h, user = user_headers(client, db_session, "owner")
    return h, user


def test_audit_payment_recorded_and_failed_payment_writes_none(client, db_session):
    seed(db_session)
    h, _ = _owner(client, db_session)
    bill, _, _ = place_bill(client, db_session)
    before = audit_total(client, h, "payment.recorded")
    # Partial payment first: the bill stays open, so the next
    # attempt can reach the amount validation (a fully-paid bill
    # answers 409 "already paid" before amount checks — designed).
    ok = pay_bill(client, db_session, bill, amount_paise=100)
    assert ok.status_code == 201, ok.text
    assert audit_total(client, h, "payment.recorded") == before + 1
    # A failed payment (over outstanding) writes no new row.
    bad = pay_bill(client, db_session, bill,
                   amount_paise=bill["totalPaise"] - 100 + 1)
    assert bad.status_code == 422, bad.text
    assert audit_total(client, h, "payment.recorded") == before + 1


def test_audit_refund_created_and_failed_refund_writes_none(client, db_session):
    seed(db_session)
    h, _ = _owner(client, db_session)
    bill, _, _ = place_bill(client, db_session)
    pay = pay_bill(client, db_session, bill)
    assert pay.status_code == 201, pay.text
    payment_id = pay.json()["data"]["payment"]["id"]
    before = audit_total(client, h, "refund.created")
    ok = client.post(
        f"/api/v1/admin/bills/{bill['id']}/refunds",
        json={"paymentId": payment_id, "amountPaise": 100, "reason": "test"},
        headers={**h, "Idempotency-Key": f"sec9-aud-{uuid.uuid4().hex}"},
    )
    assert ok.status_code == 201, ok.text
    assert audit_total(client, h, "refund.created") == before + 1
    # Over-refund attempt writes no new row.
    bad = client.post(
        f"/api/v1/admin/bills/{bill['id']}/refunds",
        json={"paymentId": payment_id, "amountPaise": bill["totalPaise"],
              "reason": "too much"},
        headers={**h, "Idempotency-Key": f"sec9-aud-{uuid.uuid4().hex}"},
    )
    assert bad.status_code == 422, bad.text
    assert audit_total(client, h, "refund.created") == before + 1


def test_audit_bill_voided_and_failed_void_writes_none(client, db_session):
    seed(db_session)
    h, _ = _owner(client, db_session)
    bill, _, _ = place_bill(client, db_session)
    before = audit_total(client, h, "bill.voided")
    ok = client.post(
        f"/api/v1/admin/bills/{bill['id']}/void",
        json={"reason": "guest left"}, headers=h,
    )
    assert ok.status_code == 200, ok.text
    assert audit_total(client, h, "bill.voided") == before + 1
    # Re-void (409) writes no new row.
    again = client.post(
        f"/api/v1/admin/bills/{bill['id']}/void",
        json={"reason": "again"}, headers=h,
    )
    assert again.status_code == 409, again.text
    assert audit_total(client, h, "bill.voided") == before + 1
    # Voiding a PAID bill (409) also writes no row.
    bill2, _, _ = place_bill(client, db_session)
    assert pay_bill(client, db_session, bill2).status_code == 201
    bad = client.post(
        f"/api/v1/admin/bills/{bill2['id']}/void",
        json={"reason": "too late"}, headers=h,
    )
    assert bad.status_code == 409, bad.text
    assert audit_total(client, h, "bill.voided") == before + 1


def test_audit_menu_price_changed_only_when_price_moves(client, db_session):
    seed(db_session)
    h, _ = _owner(client, db_session)
    items = client.get("/api/v1/admin/menu/items?per_page=100", headers=h).json()["data"]
    naan = next(i for i in items if i["name"] == "Butter Naan")
    old_price = naan["pricePaise"]
    before_changed = audit_total(client, h, "menu.price_changed")
    before_updated = audit_total(client, h, "menu.item_updated")
    # Name-only change: menu.item_updated, NOT menu.price_changed.
    assert client.patch(
        f"/api/v1/admin/menu/items/{naan['id']}",
        json={"name": "Butter Naan"}, headers=h,
    ).status_code == 200
    assert audit_total(client, h, "menu.price_changed") == before_changed
    assert audit_total(client, h, "menu.item_updated") == before_updated + 1
    # Real price change: menu.price_changed.
    assert client.patch(
        f"/api/v1/admin/menu/items/{naan['id']}",
        json={"pricePaise": old_price + 100}, headers=h,
    ).status_code == 200
    assert audit_total(client, h, "menu.price_changed") == before_changed + 1
    # Restore the price so later tests see the seeded price.
    assert client.patch(
        f"/api/v1/admin/menu/items/{naan['id']}",
        json={"pricePaise": old_price}, headers=h,
    ).status_code == 200


def test_audit_user_lifecycle(client, db_session):
    seed(db_session)
    h, _ = _owner(client, db_session)
    before_created = audit_total(client, h, "user.created")
    before_role = audit_total(client, h, "user.role_changed")
    before_deact = audit_total(client, h, "user.deactivated")
    before_reset = audit_total(client, h, "user.password_reset")
    waiter_email = f"sec9-audit-{uuid.uuid4().hex[:8]}@zaika.example"
    created = client.post(
        "/api/v1/admin/users",
        json={"email": waiter_email, "password": "WaiterPass123", "role": "waiter"},
        headers=h,
    )
    assert created.status_code == 201, created.text
    waiter_id = created.json()["data"]["id"]
    assert audit_total(client, h, "user.created") == before_created + 1
    # Role change -> user.role_changed.
    assert client.patch(
        f"/api/v1/admin/users/{waiter_id}", json={"role": "kitchen"}, headers=h
    ).status_code == 200
    assert audit_total(client, h, "user.role_changed") == before_role + 1
    # Password reset -> user.password_reset.
    assert client.post(
        f"/api/v1/admin/users/{waiter_id}/reset-password",
        json={"password": "ResetPass123"}, headers=h,
    ).status_code == 200
    assert audit_total(client, h, "user.password_reset") == before_reset + 1
    # Deactivate -> user.deactivated.
    assert client.post(
        f"/api/v1/admin/users/{waiter_id}/deactivate", headers=h
    ).status_code == 200
    assert audit_total(client, h, "user.deactivated") == before_deact + 1
    # A FAILED user create (bad role) writes no user.created row.
    bad = client.post(
        "/api/v1/admin/users",
        json={"email": "bad@x.example", "password": "SomePass123", "role": "nope"},
        headers=h,
    )
    assert bad.status_code == 422
    assert audit_total(client, h, "user.created") == before_created + 1


def test_audit_password_changed_and_tax_config_created(client, db_session):
    seed(db_session)
    h, _ = _owner(client, db_session)
    waiter, password = make_user(db_session, "waiter")
    wh = auth_headers(client, waiter.email, password)
    before_pw = audit_total(client, h, "user.password_changed")
    assert client.post(
        "/api/v1/auth/change-password",
        json={"currentPassword": password, "newPassword": "NewPassw0rd123"},
        headers=wh,
    ).status_code == 200
    assert audit_total(client, h, "user.password_changed") == before_pw + 1
    # Wrong current password: no password_changed row.
    fresh = login(client, waiter.email, "NewPassw0rd123")
    fresh_h = {"Authorization": f"Bearer {fresh.json()['data']['accessToken']}"}
    assert client.post(
        "/api/v1/auth/change-password",
        json={"currentPassword": "wrong", "newPassword": "AnotherPass1"},
        headers=fresh_h,
    ).status_code == 401
    assert audit_total(client, h, "user.password_changed") == before_pw + 1
    # Tax config (far-future effective date, so no live bill changes
    # totals for later tests) -> tax.config_created; then remove the
    # row so the shared suite's "tax unconfigured" state is restored.
    from sqlalchemy import select
    from app.models import TaxConfiguration

    before_tax = audit_total(client, h, "tax.config_created")
    created = client.post(
        "/api/v1/admin/settings/tax",
        json={"enabled": True, "rateBps": 500, "mode": "exclusive",
              "label": "GST", "effectiveFrom": future_iso(days=365)},
        headers=h,
    )
    assert created.status_code == 201, created.text
    assert audit_total(client, h, "tax.config_created") == before_tax + 1
    row = db_session.execute(
        select(TaxConfiguration).where(
            TaxConfiguration.label == "GST",
            TaxConfiguration.rate_bps == 500,
        )
    ).scalars().first()
    assert row is not None
    db_session.delete(row)
    db_session.commit()


def test_audit_login_success_and_failure(client, db_session):
    seed(db_session)
    h, _ = _owner(client, db_session)
    waiter, password = make_user(db_session, "waiter")
    before_ok = audit_total(client, h, "login.success")
    before_fail = audit_total(client, h, "login.failed")
    assert login(client, waiter.email, password).status_code == 200
    assert audit_total(client, h, "login.success") == before_ok + 1
    assert login(client, waiter.email, "wrong-password").status_code == 401
    assert audit_total(client, h, "login.failed") == before_fail + 1
    # A failed login writes login.failed, NOT login.success.
    assert audit_total(client, h, "login.success") == before_ok + 1
    # PINNED BY DESIGN (auth/service.py): an unknown email has no
    # tenant to scope an audit row to, so it writes NO audit row.
    assert login(client, "nobody@zaika.example", "whatever123").status_code == 401
    assert audit_total(client, h, "login.failed") == before_fail + 1


def test_audit_rows_never_contain_secrets(client, db_session):
    seed(db_session)
    h, _ = _owner(client, db_session)
    waiter, password = make_user(db_session, "waiter")
    login(client, waiter.email, "wrong-password")
    login(client, waiter.email, password)
    rows = client.get(
        "/api/v1/admin/audit-logs?per_page=100", headers=h
    ).json()["data"]
    blob = str(rows).lower()
    assert password.lower() not in blob
    assert "wrong-password" not in blob
    assert "tavessa_refresh" not in blob
