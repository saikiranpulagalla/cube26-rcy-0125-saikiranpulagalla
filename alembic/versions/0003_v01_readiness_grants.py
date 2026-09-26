"""Grant safe migration metadata visibility to runtime roles.

Revision ID: 0003_v01_readiness
Revises: 0002_v01_repair
"""

from alembic import op

revision = "0003_v01_readiness"
down_revision = "0002_v01_repair"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("GRANT SELECT ON alembic_version TO recovery_app, recovery_worker")


def downgrade() -> None:
    op.execute("REVOKE SELECT ON alembic_version FROM recovery_app, recovery_worker")
