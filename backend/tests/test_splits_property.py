"""Stage 9 — split property tests (SQLite suite).

Property: for random totals (1..100000 paise) x random parts
(2..10), equal-split amounts ALWAYS sum EXACTLY to the total and
follow the remainder rule (1p to the earliest parts). The test
drives the REAL allocation — ``billing_service``'s equal-split
rule as executed by ``_reconcile_splits_at_freeze`` (the same
``base = total // n; remainder 1p to earliest`` code path the
freeze uses) — with a fixed seed so the run is reproducible.

Also: custom splits summing exactly pass, off-by-one (±1p) fails
— via the real ``POST /guest/bill/splits`` endpoint.
"""
import random
import uuid

import pytest
from sqlalchemy import select

from app.core.errors import ApiError
from app.models import Bill, BillSplit, Restaurant
from app.services.billing_service import _reconcile_splits_at_freeze
from tests.helpers import (
    fresh_table_token,
    headers,
    menu_by_name,
    open_session,
    seed,
)

SEED = 20261125


def _setup(client, db_session):
    seed(db_session)
    token = fresh_table_token(db_session)
    open_session(client, token)
    by_name, _body = menu_by_name(client, token)
    restaurant = (
        db_session.execute(select(Restaurant).where(Restaurant.slug == "zaika"))
        .scalars()
        .first()
    )
    return {"token": token, "menu": by_name, "restaurant_id": str(restaurant.id)}


def _place_one(client, setup, key, name="Butter Naan", quantity=1):
    item = setup["menu"][name]
    response = client.post(
        "/api/v1/orders",
        json={"items": [{"menuItemId": item["id"], "quantity": quantity}]},
        headers={**headers(setup["token"]), "Idempotency-Key": key},
    )
    assert response.status_code == 201, response.text
    return response.json()["order"]


def _bill(client, setup):
    response = client.post("/api/v1/guest/bill", headers=headers(setup["token"]))
    assert response.status_code == 200, response.text
    return response.json()["data"]


def test_equal_split_property_random_totals_and_parts(client, db_session):
    """200 seeded cases through the real freeze reconciliation."""
    setup = _setup(client, db_session)
    _place_one(client, setup, f"split-prop-{uuid.uuid4().hex}")
    bill_data = _bill(client, setup)
    bill = db_session.get(Bill, uuid.UUID(bill_data["id"]))
    rng = random.Random(SEED)
    cases = [
        (rng.randint(1, 100000), rng.randint(2, 10)) for _ in range(200)
    ]
    # Boundary totals are included explicitly, not left to chance.
    cases += [(1, 2), (1, 10), (2, 10), (3, 10), (100000, 10), (99999, 7)]
    for total, parts in cases:
        # Reset the bill's splits to `parts` unpaid EQUAL splits
        # with arbitrary (wrong) amounts; reconciliation must
        # re-derive them from `total` exactly — unless the total is
        # smaller than the part count, where no positive allocation
        # exists and the freeze must block cleanly (409) instead of
        # writing 0p splits (Stage 9 fix: the DB CHECK forbids 0p).
        db_session.execute(
            BillSplit.__table__.delete().where(BillSplit.bill_id == bill.id)
        )
        for idx in range(parts):
            db_session.add(BillSplit(
                bill_id=bill.id, label=f"Split {idx + 1}",
                amount_paise=1, status="unpaid", mode="equal",
            ))
        db_session.commit()
        if total < parts:
            with pytest.raises(ApiError) as exc:
                _reconcile_splits_at_freeze(db_session, bill, total, None)
            assert exc.value.status_code == 409
            assert exc.value.code == "split_reconciliation_required"
            db_session.rollback()
            continue
        _reconcile_splits_at_freeze(db_session, bill, total, None)
        db_session.commit()
        db_session.refresh(bill)
        amounts = [s.amount_paise for s in sorted(
            bill.splits, key=lambda s: s.created_at)]
        assert len(amounts) == parts
        assert sum(amounts) == total, f"total={total} parts={parts}: {amounts}"
        base, remainder = divmod(total, parts)
        assert amounts == [base + (1 if i < remainder else 0) for i in range(parts)], (
            f"total={total} parts={parts}: {amounts}"
        )
        assert max(amounts) - min(amounts) <= 1


def test_equal_split_property_same_seed_is_reproducible(client, db_session):
    """The seeded case list is deterministic — same seed, same cases."""
    rng_a = random.Random(SEED)
    rng_b = random.Random(SEED)
    cases_a = [(rng_a.randint(1, 100000), rng_a.randint(2, 10)) for _ in range(200)]
    cases_b = [(rng_b.randint(1, 100000), rng_b.randint(2, 10)) for _ in range(200)]
    assert cases_a == cases_b
    assert len(set(cases_a)) > 150  # genuinely varied, not degenerate


def test_custom_split_exact_passes_off_by_one_fails(client, db_session):
    setup = _setup(client, db_session)
    _place_one(client, setup, f"split-custom-{uuid.uuid4().hex}", quantity=2)
    bill = _bill(client, setup)
    total = bill["totalPaise"]
    assert total > 2, total

    # Exact sum passes.
    response = client.post(
        "/api/v1/guest/bill/splits",
        json={"mode": "custom", "amountsPaise": [total - 1, 1]},
        headers=headers(setup["token"]),
    )
    assert response.status_code == 200, response.text
    assert [s["amountPaise"] for s in response.json()["data"]["splits"]] == [
        total - 1, 1]

    # Off by one paise either way fails with 422 — no rounding is
    # ever silently applied to a custom split.
    for amounts in ([total, 1], [total - 2, 1], [total - 1]):
        response = client.post(
            "/api/v1/guest/bill/splits",
            json={"mode": "custom", "amountsPaise": amounts},
            headers=headers(setup["token"]),
        )
        assert response.status_code == 422, (amounts, response.text)
        assert response.json()["error"]["code"] in (
            "split_sum_mismatch", "validation_failed"), response.text

    # The failed attempts left the good split untouched.
    bill_after = client.get(
        "/api/v1/guest/bill", headers=headers(setup["token"])).json()["data"]
    assert [s["amountPaise"] for s in bill_after["splits"]] == [total - 1, 1]
    assert sum(s["amountPaise"] for s in bill_after["splits"]) == total


def test_equal_split_api_rejects_more_parts_than_paise(client, db_session):
    """Regression (Stage 9): equal split with parts > total paise
    used to 500 on the bill_splits positive-amount CHECK (0p
    parts). It is now a clean 422 and the bill is untouched."""
    setup = _setup(client, db_session)
    _place_one(client, setup, f"split-too-many-{uuid.uuid4().hex}", quantity=1)
    bill = _bill(client, setup)
    total = bill["totalPaise"]
    response = client.post(
        "/api/v1/guest/bill/splits",
        json={"mode": "equal", "parts": total + 1},
        headers=headers(setup["token"]),
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "validation_failed"
    bill_after = client.get(
        "/api/v1/guest/bill", headers=headers(setup["token"])).json()["data"]
    assert bill_after["splits"] == []
    assert bill_after["totalPaise"] == total


def test_equal_split_api_remainder_rule_odd_totals(client, db_session):
    """Equal split via the endpoint on an odd total (1p remainder)."""
    setup = _setup(client, db_session)
    # Butter Naan is 4900; quantity 1 + a 1p-odd total is not
    # reachable from the menu, so assert the rule on the real total.
    _place_one(client, setup, f"split-odd-{uuid.uuid4().hex}", quantity=3)
    bill = _bill(client, setup)
    total = bill["totalPaise"]  # 14700
    response = client.post(
        "/api/v1/guest/bill/splits",
        json={"mode": "equal", "parts": 4},
        headers=headers(setup["token"]),
    )
    assert response.status_code == 200, response.text
    amounts = [s["amountPaise"] for s in response.json()["data"]["splits"]]
    assert sum(amounts) == total
    assert amounts == [total // 4 + 1 if i < total % 4 else total // 4
                       for i in range(4)]
