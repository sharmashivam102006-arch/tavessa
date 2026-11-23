"""Shared helpers for Stage 3 guest tests (not a test module)."""
import itertools
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.models import DiningTable, Restaurant, TableToken
from app.seed.zaika import RESTAURANT_SLUG, seed_zaika
from app.services.table_service import create_table_token

_fresh_table_counter = itertools.count(900)


def seed(db_session):
    return seed_zaika(db_session)


def table(db_session, number=5):
    restaurant = (
        db_session.execute(
            select(Restaurant).where(Restaurant.slug == RESTAURANT_SLUG)
        )
        .scalars()
        .first()
    )
    return (
        db_session.execute(
            select(DiningTable).where(
                DiningTable.restaurant_id == restaurant.id,
                DiningTable.number == number,
            )
        )
        .scalars()
        .first()
    )


def make_token(db_session, number=5):
    """A fresh valid token for a table (revokes any previous active one)."""
    raw, _row = create_table_token(db_session, table(db_session, number))
    return raw


def make_token_row(db_session, number=5, **overrides):
    """Create a token and mutate the row (for revoked/expired cases)."""
    raw, row = create_table_token(db_session, table(db_session, number))
    for key, value in overrides.items():
        setattr(row, key, value)
    db_session.commit()
    return raw, row


def open_session(client, token):
    response = client.get(f"/api/v1/guest/t/{token}")
    assert response.status_code == 200, response.text
    return response.json()


def headers(token):
    return {"X-Table-Token": token}


def fresh_table_token(db_session):
    """A token for a brand-new table (unique number) — isolates a
    test's session/orders from every other test sharing the test DB."""
    base = table(db_session, 5)
    number = next(_fresh_table_counter)
    row = DiningTable(
        restaurant_id=base.restaurant_id,
        branch_id=base.branch_id,
        number=number,
        status="available",
    )
    db_session.add(row)
    db_session.commit()
    raw, _ = create_table_token(db_session, row)
    return raw


def menu_by_name(client, token):
    response = client.get("/api/v1/guest/menu", headers=headers(token))
    assert response.status_code == 200, response.text
    body = response.json()
    return {item["name"]: item for item in body["items"]}, body
