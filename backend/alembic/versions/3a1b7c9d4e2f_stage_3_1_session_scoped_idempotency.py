"""Stage 3.1: session-scope the idempotency key.

Adds ``table_session_id`` to ``idempotency_keys`` and replaces the
unique constraint (restaurant_id, endpoint, key) with
(restaurant_id, table_session_id, endpoint, key), so a key is
per-session: one session can never replay or reuse another session's
key, and concurrent claimants for the same scoped key serialise on
the DB constraint (claim-before-create, api-plan.md § 10).

Backfill: every Stage 3 row stored an order_id, so the session is
recovered from orders.table_session_id. Any row with a NULL order_id
never completed (Stage 3 always stored order_id on success) and is
deleted — none are expected to exist.

Revision ID: 3a1b7c9d4e2f
Revises: 9cd1cf3c6050
Create Date: 2026-11-23 22:30:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '3a1b7c9d4e2f'
down_revision: Union[str, None] = '9cd1cf3c6050'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Add the column nullable so existing rows survive the add.
    op.add_column(
        'idempotency_keys',
        sa.Column('table_session_id', sa.Uuid(), nullable=True),
    )
    # 2. Backfill the session from the linked order.
    op.execute(
        "UPDATE idempotency_keys "
        "SET table_session_id = orders.table_session_id "
        "FROM orders WHERE idempotency_keys.order_id = orders.id"
    )
    # 3. Drop rows that never completed (NULL order_id): they hold no
    # stored response and no order, so nothing is lost. None are
    # expected from Stage 3, which always stored order_id on success.
    op.execute("DELETE FROM idempotency_keys WHERE table_session_id IS NULL")
    # 4. Now the column can be NOT NULL.
    op.alter_column('idempotency_keys', 'table_session_id', nullable=False)
    op.create_foreign_key(
        op.f('fk_idempotency_keys_table_session_id_table_sessions'),
        'idempotency_keys', 'table_sessions',
        ['table_session_id'], ['id'],
    )
    # 5. Swap the unique constraint to the session-scoped one.
    op.drop_constraint(
        'uq_idempotency_keys_restaurant_endpoint_key',
        'idempotency_keys', type_='unique',
    )
    op.create_unique_constraint(
        'uq_idempotency_keys_restaurant_session_endpoint_key',
        'idempotency_keys',
        ['restaurant_id', 'table_session_id', 'endpoint', 'key'],
    )


def downgrade() -> None:
    op.drop_constraint(
        'uq_idempotency_keys_restaurant_session_endpoint_key',
        'idempotency_keys', type_='unique',
    )
    op.create_unique_constraint(
        'uq_idempotency_keys_restaurant_endpoint_key',
        'idempotency_keys',
        ['restaurant_id', 'endpoint', 'key'],
    )
    op.drop_constraint(
        op.f('fk_idempotency_keys_table_session_id_table_sessions'),
        'idempotency_keys', type_='foreignkey',
    )
    op.drop_column('idempotency_keys', 'table_session_id')
