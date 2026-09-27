"""Constrain synthetic opportunities to one obligation per debit and basis.

Revision ID: 0016_opportunity_identity
Revises: 0015_trusted_synthetic_authority
"""

from alembic import op

revision = "0016_opportunity_identity"
down_revision = "0015_trusted_synthetic_authority"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE UNIQUE INDEX uq_obligation_financial_basis "
        "ON economic_obligation (org_id, financial_event_id, recovery_basis) "
        "WHERE financial_event_id IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX uq_obligation_financial_basis")
