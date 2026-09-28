"""Restrict assessment publication to the fenced worker role.

Revision ID: 0019_worker_publication
Revises: 0018_reconciliation_org_index
"""

from alembic import op

revision = "0019_worker_publication"
down_revision = "0018_reconciliation_org_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "REVOKE EXECUTE ON FUNCTION public.publish_recovery_assessment(uuid, uuid, bigint, text, bigint, text, jsonb) "
        "FROM recovery_app"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION public.publish_recovery_assessment(uuid, uuid, bigint, text, bigint, text, jsonb) "
        "TO recovery_worker"
    )


def downgrade() -> None:
    op.execute(
        "REVOKE EXECUTE ON FUNCTION public.publish_recovery_assessment(uuid, uuid, bigint, text, bigint, text, jsonb) "
        "FROM recovery_worker"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION public.publish_recovery_assessment(uuid, uuid, bigint, text, bigint, text, jsonb) "
        "TO recovery_app"
    )
