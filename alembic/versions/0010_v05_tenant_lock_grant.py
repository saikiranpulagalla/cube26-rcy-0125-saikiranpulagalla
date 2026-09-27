"""Permit the runtime role to take the tenant revision lock required by v0.5.

Revision ID: 0010_v05_tenant_lock_grant
Revises: 0009_v05_publication_guards
"""

from alembic import op

revision = "0010_v05_tenant_lock_grant"
down_revision = "0009_v05_publication_guards"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("GRANT UPDATE (decision_revision) ON tenant_state TO recovery_app")


def downgrade() -> None:
    op.execute("REVOKE UPDATE (decision_revision) ON tenant_state FROM recovery_app")
