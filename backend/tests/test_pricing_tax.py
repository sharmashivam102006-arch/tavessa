"""Stage 9 — pricing + tax unit tests (SQLite suite).

Uses the REAL functions: ``tax_service.compute_tax``,
``billing_service.freeze_tax_on_bill`` / ``get_or_create_bill`` /
``_build_lines`` — never a re-implementation of the formulae.
Integer paise throughout; boundaries ₹0 / ₹1 / odd paise.

Tax-config rows are test-local: every test that creates one
deletes it again in a ``finally`` (the SQLite suite shares one DB
and other modules assume "no tax configured" — a leaked config
would silently change their bill totals).
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.auth.models import TaxConfiguration
from app.models import MenuItem, Restaurant
from app.services.billing_service import (
    _build_lines,
    freeze_tax_on_bill,
    get_or_create_bill,
)
from app.services.table_service import resolve_guest_context
from app.services.tax_service import compute_tax
from tests.helpers import (
    fresh_table_token,
    headers,
    menu_by_name,
    open_session,
    seed,
)


@pytest.fixture
def setup(client, db_session):
    seed(db_session)
    # A fresh table per test: the shared table-5 session accumulates
    # orders across tests, which would leak into bill totals here.
    token = fresh_table_token(db_session)
    open_session(client, token)
    by_name, _body = menu_by_name(client, token)
    restaurant = (
        db_session.execute(select(Restaurant).where(Restaurant.slug == "zaika"))
        .scalars()
        .first()
    )
    return {"token": token, "menu": by_name, "restaurant_id": str(restaurant.id)}


def _place(client, token, items, key):
    return client.post(
        "/api/v1/orders",
        json={"items": items},
        headers={**headers(token), "Idempotency-Key": key},
    )


def _place_one(client, setup, key, name="Butter Naan", quantity=1):
    item = setup["menu"][name]
    response = _place(
        client,
        setup["token"],
        [{"menuItemId": item["id"], "quantity": quantity}],
        key,
    )
    assert response.status_code == 201, response.text
    return response.json()["order"]


def _tax_config(db_session, restaurant_id, *, enabled=True, rate_bps=500,
                mode="exclusive", label="GST", effective_from=None):
    row = TaxConfiguration(
        restaurant_id=uuid.UUID(restaurant_id),
        branch_id=None,
        enabled=enabled,
        rate_bps=rate_bps,
        mode=mode,
        label=label,
        effective_from=effective_from
        or (datetime.now(timezone.utc) - timedelta(days=1)),
    )
    db_session.add(row)
    db_session.commit()
    return row


def _delete_tax_configs(db_session, restaurant_id):
    rows = db_session.execute(
        select(TaxConfiguration).where(
            TaxConfiguration.restaurant_id == uuid.UUID(restaurant_id)
        )
    ).scalars().all()
    for row in rows:
        db_session.delete(row)
    db_session.commit()


# ----------------------------------------------------------------------
# compute_tax — the formulae, directly
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "subtotal,rate,expected_tax,expected_total",
    [
        (0, 500, 0, 0),          # ₹0 boundary
        (1, 500, 0, 1),          # ₹0.01 — tax floors to 0
        (100, 500, 5, 105),      # exact paise
        (101, 500, 5, 106),      # odd paise, floors (101*500//10000 = 5)
        (199, 500, 9, 208),      # 199*500//10000 = 9 (not 10)
        (999, 500, 49, 1048),
        (10000, 500, 500, 10500),
        (100001, 500, 5000, 105001),  # odd-paise total
        (10000, 0, 0, 10000),    # explicit 0% rate
        (10000, 10000, 10000, 20000),  # 100% rate
        (10000, 1800, 1800, 11800),    # 18% GST
        (99999, 1800, 17999, 117998),
    ],
)
def test_compute_tax_exclusive(subtotal, rate, expected_tax, expected_total):
    config = TaxConfiguration(
        restaurant_id=uuid.uuid4(), branch_id=None, enabled=True,
        rate_bps=rate, mode="exclusive", label="GST",
        effective_from=datetime.now(timezone.utc),
    )
    assert compute_tax(subtotal, config) == (expected_tax, expected_total)


@pytest.mark.parametrize(
    "subtotal,rate,expected_tax,expected_total",
    [
        (0, 500, 0, 0),
        (1, 500, 0, 1),
        (10500, 500, 500, 10500),   # tax component inside the total
        (101, 500, 4, 101),         # 101*500//10500 = 4
        (100, 500, 4, 100),         # 100*500//10500 = 4
        (10000, 1800, 1525, 10000),  # 10000*1800//11800 = 1525
        (99999, 1800, 15254, 99999),
        (10000, 0, 0, 10000),
    ],
)
def test_compute_tax_inclusive(subtotal, rate, expected_tax, expected_total):
    config = TaxConfiguration(
        restaurant_id=uuid.uuid4(), branch_id=None, enabled=True,
        rate_bps=rate, mode="inclusive", label="GST (incl.)",
        effective_from=datetime.now(timezone.utc),
    )
    assert compute_tax(subtotal, config) == (expected_tax, expected_total)


def test_compute_tax_unconfigured_and_disabled():
    assert compute_tax(10000, None) == (0, 10000)
    disabled = TaxConfiguration(
        restaurant_id=uuid.uuid4(), branch_id=None, enabled=False,
        rate_bps=500, mode="exclusive", label="GST",
        effective_from=datetime.now(timezone.utc),
    )
    assert compute_tax(10000, disabled) == (0, 10000)
    no_rate = TaxConfiguration(
        restaurant_id=uuid.uuid4(), branch_id=None, enabled=True,
        rate_bps=None, mode="exclusive", label="GST",
        effective_from=datetime.now(timezone.utc),
    )
    assert compute_tax(10000, no_rate) == (0, 10000)


# ----------------------------------------------------------------------
# Freeze — the snapshot never moves afterwards
# ----------------------------------------------------------------------


def test_freeze_snapshots_rate_mode_label_and_totals(client, db_session, setup):
    _place_one(client, setup, f"tax-freeze-{uuid.uuid4().hex}")
    try:
        _tax_config(db_session, setup["restaurant_id"], rate_bps=500,
                    mode="exclusive", label="GST")
        ctx = resolve_guest_context(db_session, setup["token"])
        bill_out, _created = get_or_create_bill(db_session, ctx)
        from app.models import Bill

        bill = db_session.get(Bill, bill_out.id)
        subtotal = bill.subtotal_paise
        freeze_tax_on_bill(db_session, bill)
        db_session.commit()
        db_session.refresh(bill)
        assert bill.tax_rate_bps_snapshot == 500
        assert bill.tax_mode_snapshot == "exclusive"
        assert bill.tax_label_snapshot == "GST"
        assert bill.tax_paise == subtotal * 500 // 10000
        assert bill.total_paise == subtotal + bill.tax_paise
        frozen_total, frozen_tax = bill.total_paise, bill.tax_paise

        # A later config change (10%, inclusive) must not move it.
        _tax_config(
            db_session, setup["restaurant_id"], rate_bps=1000,
            mode="inclusive", label="GST 10%",
            effective_from=datetime.now(timezone.utc),
        )
        freeze_tax_on_bill(db_session, bill)  # idempotent no-op path
        db_session.refresh(bill)
        assert bill.total_paise == frozen_total
        assert bill.tax_paise == frozen_tax
        assert bill.tax_rate_bps_snapshot == 500
        assert bill.tax_mode_snapshot == "exclusive"
    finally:
        _delete_tax_configs(db_session, setup["restaurant_id"])


def test_freeze_unconfigured_stays_unconfigured(client, db_session, setup):
    _place_one(client, setup, f"tax-unconf-{uuid.uuid4().hex}")
    try:
        ctx = resolve_guest_context(db_session, setup["token"])
        bill_out, _created = get_or_create_bill(db_session, ctx)
        from app.models import Bill

        bill = db_session.get(Bill, bill_out.id)
        freeze_tax_on_bill(db_session, bill)
        db_session.commit()
        db_session.refresh(bill)
        assert bill.tax_rate_bps_snapshot is None
        assert bill.tax_mode_snapshot is None
        assert bill.tax_label_snapshot is None
        assert bill.tax_paise == 0
        assert bill.total_paise == bill.subtotal_paise
        frozen_total = bill.total_paise
        # Configuring tax afterwards must not rewrite the frozen bill.
        _tax_config(db_session, setup["restaurant_id"], rate_bps=500)
        freeze_tax_on_bill(db_session, bill)
        db_session.refresh(bill)
        assert bill.total_paise == frozen_total
        assert bill.tax_rate_bps_snapshot is None
    finally:
        _delete_tax_configs(db_session, setup["restaurant_id"])


def test_freeze_inclusive_snapshot(client, db_session, setup):
    _place_one(client, setup, f"tax-incl-{uuid.uuid4().hex}")
    try:
        _tax_config(db_session, setup["restaurant_id"], rate_bps=500,
                    mode="inclusive", label="GST (incl.)")
        ctx = resolve_guest_context(db_session, setup["token"])
        bill_out, _created = get_or_create_bill(db_session, ctx)
        from app.models import Bill

        bill = db_session.get(Bill, bill_out.id)
        freeze_tax_on_bill(db_session, bill)
        db_session.commit()
        db_session.refresh(bill)
        assert bill.tax_mode_snapshot == "inclusive"
        assert bill.total_paise == bill.subtotal_paise
        assert bill.tax_paise == bill.subtotal_paise * 500 // 10500
    finally:
        _delete_tax_configs(db_session, setup["restaurant_id"])


# ----------------------------------------------------------------------
# Snapshot rule — a menu price change after ordering cannot move a bill
# ----------------------------------------------------------------------


def test_menu_price_change_after_ordering_cannot_change_bill(
    client, db_session, setup
):
    order = _place_one(client, setup, f"tax-snap-{uuid.uuid4().hex}", quantity=2)
    snapshot_unit = setup["menu"]["Butter Naan"]["pricePaise"]
    # The menu price moves (even to ₹1) after the order is placed.
    item_row = db_session.get(MenuItem, uuid.UUID(setup["menu"]["Butter Naan"]["id"]))
    item_row.price_paise = 100
    db_session.commit()

    ctx = resolve_guest_context(db_session, setup["token"])
    bill_out, _created = get_or_create_bill(db_session, ctx)
    assert bill_out.subtotal_paise == snapshot_unit * 2
    assert bill_out.total_paise == snapshot_unit * 2
    assert bill_out.items[0].unit_price_paise == snapshot_unit
    assert order["totalPaise"] == snapshot_unit * 2


def test_build_lines_uses_snapshots_not_menu(client, db_session, setup):
    """_build_lines directly: totals come from order-item snapshots."""
    from app.models import Bill, Order, OrderItem

    _place_one(client, setup, f"tax-lines-{uuid.uuid4().hex}")
    ctx = resolve_guest_context(db_session, setup["token"])
    bill_out, _created = get_or_create_bill(db_session, ctx)
    bill = db_session.get(Bill, bill_out.id)
    # Change the menu price AND the order snapshot's unit price is
    # what _build_lines must use — rebuild from the same items.
    item_row = db_session.get(MenuItem, uuid.UUID(setup["menu"]["Butter Naan"]["id"]))
    item_row.price_paise = 100
    db_session.commit()
    order_items = (
        db_session.execute(select(OrderItem).join(Order).where(
            Order.table_session_id == ctx.session.id))
        .scalars().all()
    )
    subtotal = _build_lines(db_session, bill, order_items)
    assert subtotal == setup["menu"]["Butter Naan"]["pricePaise"]
    assert bill.total_paise == subtotal
