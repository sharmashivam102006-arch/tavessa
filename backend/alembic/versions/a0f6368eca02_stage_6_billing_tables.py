"""Stage 6: billing tables + payment-claim scope on idempotency_keys.

Creates ``bills``, ``bill_items``, ``bill_splits`` and ``payments``
per database-design.md §§ 3.16–3.19 as built in Stage 6 (see
app/models/billing.py for the recorded Stage 6 decisions: no
refunds / audit_logs / void columns or states, collected_by
nullable, change_due always 0, receipt numbers unique per
restaurant).

Extends ``idempotency_keys`` for payment claims (api-plan.md § 10
as built in Stage 6): ``table_session_id`` becomes nullable —
order claims always set it, payment claims leave it NULL and set
``bill_id`` instead — plus ``bill_id`` / ``payment_id`` columns
and a partial unique index
(restaurant_id, bill_id, endpoint, key) WHERE bill_id IS NOT NULL.
The order scope and its unique constraint are NOT changed, so the
Stage 3.2 order path is untouched. No existing rows are affected:
order claims keep their session, and no payment claims exist yet.

Revision ID: a0f6368eca02
Revises: 3a1b7c9d4e2f
Create Date: 2026-11-23 23:45:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a0f6368eca02'
down_revision: Union[str, None] = '3a1b7c9d4e2f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'bills',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('restaurant_id', sa.Uuid(), nullable=False),
        sa.Column('branch_id', sa.Uuid(), nullable=True),
        sa.Column('table_session_id', sa.Uuid(), nullable=False),
        sa.Column('table_id', sa.Uuid(), nullable=False),
        sa.Column('bill_number', sa.Text(), nullable=False),
        sa.Column('status', sa.Text(), nullable=False, server_default='issued'),
        sa.Column('subtotal_paise', sa.BigInteger(), nullable=False),
        sa.Column('tax_paise', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('service_charge_paise', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('discount_paise', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('total_paise', sa.BigInteger(), nullable=False),
        sa.Column('amount_paid_paise', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('issued_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('payment_requested_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('paid_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('version', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint("status IN ('issued', 'payment_requested', 'paid')", name='bill_status_valid'),
        sa.CheckConstraint('subtotal_paise >= 0', name='bill_subtotal_non_negative'),
        sa.CheckConstraint('tax_paise >= 0', name='bill_tax_non_negative'),
        sa.CheckConstraint('service_charge_paise >= 0', name='bill_service_charge_non_negative'),
        sa.CheckConstraint('discount_paise >= 0', name='bill_discount_non_negative'),
        sa.CheckConstraint('total_paise >= 0', name='bill_total_non_negative'),
        sa.CheckConstraint('amount_paid_paise >= 0', name='bill_amount_paid_non_negative'),
        sa.ForeignKeyConstraint(['restaurant_id'], ['restaurants.id']),
        sa.ForeignKeyConstraint(['branch_id'], ['branches.id']),
        sa.ForeignKeyConstraint(['table_session_id'], ['table_sessions.id']),
        sa.ForeignKeyConstraint(['table_id'], ['dining_tables.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('restaurant_id', 'bill_number', name='uq_bills_restaurant_number'),
    )
    op.create_index('ix_bills_restaurant_status', 'bills', ['restaurant_id', 'status'])
    op.create_index('ix_bills_restaurant_issued', 'bills', ['restaurant_id', 'issued_at'])
    op.create_index(
        'uq_bills_one_open_per_session', 'bills', ['table_session_id'],
        unique=True,
        postgresql_where=sa.text("status IN ('issued', 'payment_requested')"),
        sqlite_where=sa.text("status IN ('issued', 'payment_requested')"),
    )

    op.create_table(
        'bill_items',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('bill_id', sa.Uuid(), nullable=False),
        sa.Column('order_item_id', sa.Uuid(), nullable=True),
        sa.Column('order_id', sa.Uuid(), nullable=True),
        sa.Column('item_name_snapshot', sa.Text(), nullable=False),
        sa.Column('variant_name_snapshot', sa.Text(), nullable=True),
        sa.Column('add_on_names_snapshot', sa.JSON(), nullable=False, server_default='[]'),
        sa.Column('unit_price_paise', sa.BigInteger(), nullable=False),
        sa.Column('quantity', sa.Integer(), nullable=False),
        sa.Column('line_total_paise', sa.BigInteger(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint('quantity >= 1', name='bill_item_quantity_positive'),
        sa.CheckConstraint('unit_price_paise >= 0', name='bill_item_unit_price_non_negative'),
        sa.CheckConstraint('line_total_paise >= 0', name='bill_item_line_total_non_negative'),
        sa.ForeignKeyConstraint(['bill_id'], ['bills.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['order_item_id'], ['order_items.id']),
        sa.ForeignKeyConstraint(['order_id'], ['orders.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_bill_items_bill', 'bill_items', ['bill_id'])

    op.create_table(
        'bill_splits',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('bill_id', sa.Uuid(), nullable=False),
        sa.Column('label', sa.Text(), nullable=False),
        sa.Column('amount_paise', sa.BigInteger(), nullable=False),
        sa.Column('status', sa.Text(), nullable=False, server_default='unpaid'),
        sa.Column('paid_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint('amount_paise > 0', name='bill_split_amount_positive'),
        sa.CheckConstraint("status IN ('unpaid', 'payment_requested', 'paid')", name='bill_split_status_valid'),
        sa.ForeignKeyConstraint(['bill_id'], ['bills.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_bill_splits_bill', 'bill_splits', ['bill_id'])

    op.create_table(
        'payments',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('restaurant_id', sa.Uuid(), nullable=False),
        sa.Column('bill_id', sa.Uuid(), nullable=False),
        sa.Column('bill_split_id', sa.Uuid(), nullable=True),
        sa.Column('method', sa.Text(), nullable=False, server_default='cash'),
        sa.Column('idempotency_key', sa.Text(), nullable=False),
        sa.Column('amount_paise', sa.BigInteger(), nullable=False),
        sa.Column('collected_by', sa.Uuid(), nullable=True),
        sa.Column('collected_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('change_due_paise', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('status', sa.Text(), nullable=False, server_default='confirmed'),
        sa.Column('receipt_number', sa.Text(), nullable=False),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint("method = 'cash'", name='payment_method_cash_only'),
        sa.CheckConstraint('amount_paise > 0', name='payment_amount_positive'),
        sa.CheckConstraint('change_due_paise >= 0', name='payment_change_due_non_negative'),
        sa.CheckConstraint("status IN ('confirmed')", name='payment_status_valid'),
        sa.ForeignKeyConstraint(['restaurant_id'], ['restaurants.id']),
        sa.ForeignKeyConstraint(['bill_id'], ['bills.id']),
        sa.ForeignKeyConstraint(['bill_split_id'], ['bill_splits.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('restaurant_id', 'receipt_number', name='uq_payments_restaurant_receipt'),
    )
    op.create_index('ix_payments_bill', 'payments', ['bill_id'])
    op.create_index('ix_payments_restaurant_collected', 'payments', ['restaurant_id', 'collected_at'])

    # --- Payment-claim scope on idempotency_keys ---------------------
    op.add_column('idempotency_keys', sa.Column('bill_id', sa.Uuid(), nullable=True))
    op.add_column('idempotency_keys', sa.Column('payment_id', sa.Uuid(), nullable=True))
    # Existing order claims all have a session; relaxing the column
    # to nullable affects no stored row, and no payment claim exists
    # yet. (Same op.alter_column convention as the Stage 3.1
    # migration; migrations run against PostgreSQL.)
    op.alter_column('idempotency_keys', 'table_session_id', nullable=True)
    op.create_foreign_key(
        op.f('fk_idempotency_keys_bill_id_bills'),
        'idempotency_keys', 'bills', ['bill_id'], ['id'],
    )
    op.create_foreign_key(
        op.f('fk_idempotency_keys_payment_id_payments'),
        'idempotency_keys', 'payments', ['payment_id'], ['id'],
    )
    op.create_index(
        'uq_idempotency_keys_payment_scope', 'idempotency_keys',
        ['restaurant_id', 'bill_id', 'endpoint', 'key'],
        unique=True,
        postgresql_where=sa.text('bill_id IS NOT NULL'),
        sqlite_where=sa.text('bill_id IS NOT NULL'),
    )


def downgrade() -> None:
    op.drop_index('uq_idempotency_keys_payment_scope', table_name='idempotency_keys')
    op.drop_constraint(
        op.f('fk_idempotency_keys_payment_id_payments'),
        'idempotency_keys', type_='foreignkey',
    )
    op.drop_constraint(
        op.f('fk_idempotency_keys_bill_id_bills'),
        'idempotency_keys', type_='foreignkey',
    )
    op.alter_column('idempotency_keys', 'table_session_id', nullable=False)
    op.drop_column('idempotency_keys', 'payment_id')
    op.drop_column('idempotency_keys', 'bill_id')

    op.drop_index('ix_payments_restaurant_collected', table_name='payments')
    op.drop_index('ix_payments_bill', table_name='payments')
    op.drop_table('payments')
    op.drop_index('ix_bill_splits_bill', table_name='bill_splits')
    op.drop_table('bill_splits')
    op.drop_index('ix_bill_items_bill', table_name='bill_items')
    op.drop_table('bill_items')
    op.drop_index('uq_bills_one_open_per_session', table_name='bills')
    op.drop_index('ix_bills_restaurant_issued', table_name='bills')
    op.drop_index('ix_bills_restaurant_status', table_name='bills')
    op.drop_table('bills')
