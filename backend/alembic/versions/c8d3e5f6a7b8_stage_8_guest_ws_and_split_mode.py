"""Stage 8: guest WebSocket tickets + split mode.

- ``bill_splits.mode`` (TEXT, nullable): 'equal' | 'custom' — how
  the split set was created. NULL = created before Stage 8
  (unknown); the freeze treats NULL as 'custom' (never silently
  re-derived). See billing_service._reconcile_splits_at_freeze.
- ``ws_tickets``: ``user_id`` becomes nullable (guest tickets have
  no user); new ``table_session_id`` FK (guest tickets only);
  channel CHECK gains 'guest'.

Existing rows are preserved: the new columns are nullable and no
existing value violates the widened CHECK.

Revision ID: c8d3e5f6a7b8
Revises: b7c2e9a1d4f5
Create Date: 2026-11-25
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c8d3e5f6a7b8'
down_revision: Union[str, None] = 'b7c2e9a1d4f5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('bill_splits', recreate='auto') as batch_op:
        batch_op.add_column(sa.Column('mode', sa.Text(), nullable=True))
        batch_op.create_check_constraint(
            'bill_split_mode_valid',
            "mode IS NULL OR mode IN ('equal', 'custom')",
        )

    with op.batch_alter_table('ws_tickets', recreate='auto') as batch_op:
        batch_op.alter_column(
            'user_id', existing_type=sa.Uuid(), nullable=True
        )
        batch_op.add_column(
            sa.Column('table_session_id', sa.Uuid(), nullable=True)
        )
        batch_op.create_foreign_key(
            'fk_ws_tickets_table_session',
            'table_sessions', ['table_session_id'], ['id'],
        )
        batch_op.drop_constraint(
            'ws_ticket_channel_valid', type_='check'
        )
        batch_op.create_check_constraint(
            'ws_ticket_channel_valid',
            "channel IN ('kitchen', 'staff', 'guest')",
        )
    op.create_index(
        'ix_ws_tickets_table_session', 'ws_tickets', ['table_session_id']
    )


def downgrade() -> None:
    op.drop_index('ix_ws_tickets_table_session', table_name='ws_tickets')
    # Guest tickets cannot exist in the Stage 7 schema (user_id was
    # NOT NULL and 'guest' was not a valid channel) — delete any
    # that were created, so user_id can go back to NOT NULL.
    op.execute("DELETE FROM ws_tickets WHERE channel = 'guest'")
    with op.batch_alter_table('ws_tickets', recreate='auto') as batch_op:
        batch_op.drop_constraint(
            'ws_ticket_channel_valid', type_='check'
        )
        batch_op.create_check_constraint(
            'ws_ticket_channel_valid',
            "channel IN ('kitchen', 'staff')",
        )
        batch_op.drop_constraint(
            'fk_ws_tickets_table_session', type_='foreignkey'
        )
        batch_op.drop_column('table_session_id')
        batch_op.alter_column(
            'user_id', existing_type=sa.Uuid(), nullable=False
        )

    with op.batch_alter_table('bill_splits', recreate='auto') as batch_op:
        batch_op.drop_constraint('bill_split_mode_valid', type_='check')
        batch_op.drop_column('mode')
