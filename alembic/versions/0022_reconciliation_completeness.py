"""Invalidate decisions when scoped reconciliation certainty changes.

Revision ID: 0022_reconciliation_completeness
Revises: 0021_opportunity_conservation
"""

from alembic import op

revision = "0022_reconciliation_completeness"
down_revision = "0021_opportunity_conservation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE TRIGGER reconciliation_state_revision_bump "
        "AFTER INSERT OR UPDATE ON reconciliation_state "
        "FOR EACH ROW EXECUTE FUNCTION revision_bump()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER reconciliation_state_revision_bump ON reconciliation_state")
