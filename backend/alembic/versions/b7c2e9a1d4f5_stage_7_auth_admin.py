"""Stage 7: auth + admin — users, refresh sessions, WS tickets,
tax configurations, refunds, audit logs; bills/payments/branch
alterations.

Creates ``users``, ``refresh_sessions``, ``ws_tickets``,
``tax_configurations``, ``refunds``, ``audit_logs`` (see
app/auth/models.py). Alters:
- ``bills``: status CHECK gains voided/refunded/partially_refunded;
  new columns voided_at / void_reason / voided_by and the tax
  snapshot columns (tax_rate_bps_snapshot / tax_mode_snapshot /
  tax_label_snapshot, NULL = unconfigured at freeze).
- ``payments``: status CHECK gains refunded/partially_refunded.
- ``branches``: timezone (default Asia/Kolkata), contact_name /
  contact_phone / contact_email.
- ``restaurants``: contact_name / contact_email.

Existing rows are preserved: the new columns are all nullable
(except branches.timezone, which backfills to Asia/Kolkata) and
no existing status value is outside the widened CHECKs.

Revision ID: b7c2e9a1d4f5
Revises: a0f6368eca02
Create Date: 2026-11-24
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b7c2e9a1d4f5'
down_revision: Union[str, None] = 'a0f6368eca02'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'users',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('restaurant_id', sa.Uuid(), nullable=False),
        sa.Column('branch_id', sa.Uuid(), nullable=True),
        sa.Column('email', sa.Text(), nullable=False),
        sa.Column('password_hash', sa.Text(), nullable=False),
        sa.Column('role', sa.Text(), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('can_manage_staff', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('can_manage_settings', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('can_refund', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('can_void', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('must_change_password', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('deactivated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_login_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint("role IN ('owner', 'manager', 'waiter', 'kitchen')", name='user_role_valid'),
        sa.ForeignKeyConstraint(['restaurant_id'], ['restaurants.id']),
        sa.ForeignKeyConstraint(['branch_id'], ['branches.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('restaurant_id', 'email', name='uq_users_restaurant_email'),
    )
    op.create_index('ix_users_restaurant_role', 'users', ['restaurant_id', 'role'])

    op.create_table(
        'refresh_sessions',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.Column('family_id', sa.Uuid(), nullable=False),
        sa.Column('token_hash', sa.Text(), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('ip_address', sa.Text(), nullable=True),
        sa.Column('user_agent', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_refresh_sessions_family', 'refresh_sessions', ['family_id'])
    op.create_index('ix_refresh_sessions_token_hash', 'refresh_sessions', ['token_hash'])

    op.create_table(
        'ws_tickets',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.Column('restaurant_id', sa.Uuid(), nullable=False),
        sa.Column('channel', sa.Text(), nullable=False),
        sa.Column('ticket_hash', sa.Text(), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('used_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint("channel IN ('kitchen', 'staff')", name='ws_ticket_channel_valid'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.ForeignKeyConstraint(['restaurant_id'], ['restaurants.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('ticket_hash', name='uq_ws_tickets_ticket_hash'),
    )
    op.create_index('ix_ws_tickets_hash', 'ws_tickets', ['ticket_hash'])

    op.create_table(
        'tax_configurations',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('restaurant_id', sa.Uuid(), nullable=False),
        sa.Column('branch_id', sa.Uuid(), nullable=True),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('rate_bps', sa.Integer(), nullable=True),
        sa.Column('mode', sa.Text(), nullable=False, server_default='exclusive'),
        sa.Column('label', sa.Text(), nullable=True),
        sa.Column('effective_from', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_by', sa.Uuid(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint(
            "rate_bps IS NULL OR (rate_bps >= 0 AND rate_bps <= 10000)",
            name='tax_rate_bps_range',
        ),
        sa.CheckConstraint("mode IN ('inclusive', 'exclusive')", name='tax_mode_valid'),
        sa.CheckConstraint("enabled = false OR rate_bps IS NOT NULL", name='tax_enabled_requires_rate'),
        sa.ForeignKeyConstraint(['restaurant_id'], ['restaurants.id']),
        sa.ForeignKeyConstraint(['branch_id'], ['branches.id']),
        sa.ForeignKeyConstraint(['created_by'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('restaurant_id', 'branch_id', 'effective_from', name='uq_tax_config_restaurant_branch_effective'),
    )
    op.create_index('ix_tax_config_restaurant_effective', 'tax_configurations', ['restaurant_id', 'effective_from'])

    op.create_table(
        'refunds',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('restaurant_id', sa.Uuid(), nullable=False),
        sa.Column('bill_id', sa.Uuid(), nullable=False),
        sa.Column('payment_id', sa.Uuid(), nullable=False),
        sa.Column('amount_paise', sa.BigInteger(), nullable=False),
        sa.Column('reason', sa.Text(), nullable=False),
        sa.Column('refund_reference', sa.Text(), nullable=False),
        sa.Column('refunded_by', sa.Uuid(), nullable=True),
        sa.Column('refunded_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('idempotency_key', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint('amount_paise > 0', name='refund_amount_positive'),
        sa.ForeignKeyConstraint(['restaurant_id'], ['restaurants.id']),
        sa.ForeignKeyConstraint(['bill_id'], ['bills.id']),
        sa.ForeignKeyConstraint(['payment_id'], ['payments.id']),
        sa.ForeignKeyConstraint(['refunded_by'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('restaurant_id', 'refund_reference', name='uq_refunds_restaurant_reference'),
        sa.UniqueConstraint('restaurant_id', 'bill_id', 'idempotency_key', name='uq_refunds_restaurant_bill_key'),
    )
    op.create_index('ix_refunds_restaurant_refunded', 'refunds', ['restaurant_id', 'refunded_at'])

    op.create_table(
        'audit_logs',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('restaurant_id', sa.Uuid(), nullable=False),
        sa.Column('branch_id', sa.Uuid(), nullable=True),
        sa.Column('actor_user_id', sa.Uuid(), nullable=True),
        sa.Column('action', sa.Text(), nullable=False),
        sa.Column('entity_type', sa.Text(), nullable=True),
        sa.Column('entity_id', sa.Uuid(), nullable=True),
        sa.Column('details', sa.JSON(), nullable=True),
        sa.Column('correlation_id', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['restaurant_id'], ['restaurants.id']),
        sa.ForeignKeyConstraint(['branch_id'], ['branches.id']),
        sa.ForeignKeyConstraint(['actor_user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_audit_logs_restaurant_created', 'audit_logs', ['restaurant_id', 'created_at'])

    # --- bills: widen status CHECK + void/tax-snapshot columns --------
    with op.batch_alter_table('bills', recreate='auto') as batch_op:
        batch_op.drop_constraint('bill_status_valid', type_='check')
        batch_op.create_check_constraint(
            'bill_status_valid',
            "status IN ('issued', 'payment_requested', 'paid', 'voided', 'refunded', 'partially_refunded')",
        )
        batch_op.add_column(sa.Column('voided_at', sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column('void_reason', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('voided_by', sa.Uuid(), nullable=True))
        batch_op.add_column(sa.Column('tax_rate_bps_snapshot', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('tax_mode_snapshot', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('tax_label_snapshot', sa.Text(), nullable=True))
        batch_op.create_foreign_key('fk_bills_voided_by_users', 'users', ['voided_by'], ['id'])

    # --- payments: widen status CHECK ----------------------------------
    with op.batch_alter_table('payments', recreate='auto') as batch_op:
        batch_op.drop_constraint('payment_status_valid', type_='check')
        batch_op.create_check_constraint(
            'payment_status_valid',
            "status IN ('confirmed', 'refunded', 'partially_refunded')",
        )

    # --- branches: timezone + contact fields ---------------------------
    with op.batch_alter_table('branches', recreate='auto') as batch_op:
        batch_op.add_column(sa.Column('timezone', sa.Text(), nullable=False, server_default='Asia/Kolkata'))
        batch_op.add_column(sa.Column('contact_name', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('contact_phone', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('contact_email', sa.Text(), nullable=True))

    # --- restaurants: contact fields -----------------------------------
    with op.batch_alter_table('restaurants', recreate='auto') as batch_op:
        batch_op.add_column(sa.Column('contact_name', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('contact_email', sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('restaurants', recreate='auto') as batch_op:
        batch_op.drop_column('contact_email')
        batch_op.drop_column('contact_name')

    with op.batch_alter_table('branches', recreate='auto') as batch_op:
        batch_op.drop_column('contact_email')
        batch_op.drop_column('contact_phone')
        batch_op.drop_column('contact_name')
        batch_op.drop_column('timezone')

    with op.batch_alter_table('payments', recreate='auto') as batch_op:
        batch_op.drop_constraint('payment_status_valid', type_='check')
        batch_op.create_check_constraint(
            'payment_status_valid', "status IN ('confirmed')"
        )

    with op.batch_alter_table('bills', recreate='auto') as batch_op:
        batch_op.drop_constraint('fk_bills_voided_by_users', type_='foreignkey')
        batch_op.drop_column('tax_label_snapshot')
        batch_op.drop_column('tax_mode_snapshot')
        batch_op.drop_column('tax_rate_bps_snapshot')
        batch_op.drop_column('voided_by')
        batch_op.drop_column('void_reason')
        batch_op.drop_column('voided_at')
        batch_op.drop_constraint('bill_status_valid', type_='check')
        batch_op.create_check_constraint(
            'bill_status_valid',
            "status IN ('issued', 'payment_requested', 'paid')",
        )

    op.drop_index('ix_audit_logs_restaurant_created', table_name='audit_logs')
    op.drop_table('audit_logs')
    op.drop_index('ix_refunds_restaurant_refunded', table_name='refunds')
    op.drop_table('refunds')
    op.drop_index('ix_tax_config_restaurant_effective', table_name='tax_configurations')
    op.drop_table('tax_configurations')
    op.drop_index('ix_ws_tickets_hash', table_name='ws_tickets')
    op.drop_table('ws_tickets')
    op.drop_index('ix_refresh_sessions_token_hash', table_name='refresh_sessions')
    op.drop_index('ix_refresh_sessions_family', table_name='refresh_sessions')
    op.drop_table('refresh_sessions')
    op.drop_index('ix_users_restaurant_role', table_name='users')
    op.drop_table('users')
