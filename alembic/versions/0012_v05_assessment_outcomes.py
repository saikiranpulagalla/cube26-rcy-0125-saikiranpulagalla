"""Add the frozen non-claim assessment outcomes.

Revision ID: 0012_v05_assessment_outcomes
Revises: 0011_v05_safe_tenant_lock
"""

from alembic import op

revision = "0012_v05_assessment_outcomes"
down_revision = "0011_v05_safe_tenant_lock"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_assessment_conclusion", "recovery_assessment", type_="check")
    op.create_check_constraint(
        "ck_assessment_conclusion",
        "recovery_assessment",
        "conclusion IN ('REVIEW', 'NO_CLAIM', 'RESOLVED', 'ALREADY_PURSUED', 'SYNTHETIC_CLAIM_READY')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_assessment_conclusion", "recovery_assessment", type_="check")
    op.create_check_constraint(
        "ck_assessment_conclusion",
        "recovery_assessment",
        "conclusion IN ('REVIEW', 'NO_CLAIM', 'SYNTHETIC_CLAIM_READY')",
    )
