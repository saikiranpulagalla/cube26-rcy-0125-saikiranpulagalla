"""Add the modeled organization index for reconciliation state.

Revision ID: 0018_reconciliation_org_index
Revises: 0017_reconciliation_state
"""

from alembic import op

revision = "0018_reconciliation_org_index"
down_revision = "0017_reconciliation_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_reconciliation_state_org_id", "reconciliation_state", ["org_id"])


def downgrade() -> None:
    op.drop_index("ix_reconciliation_state_org_id", table_name="reconciliation_state")
