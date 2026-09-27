"""Require explicit reconciliation state before treating allocation absence as zero.

Revision ID: 0017_reconciliation_state
Revises: 0016_opportunity_identity
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0017_reconciliation_state"
down_revision = "0016_opportunity_identity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "reconciliation_state",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("org_id", sa.String(128), nullable=False),
        sa.Column("obligation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("domain", sa.String(16), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("cutoff", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_set_sha256", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(
            ["org_id", "obligation_id"],
            ["economic_obligation.org_id", "economic_obligation.id"],
            name="fk_reconciliation_obligation_tenant",
        ),
        sa.UniqueConstraint("org_id", "obligation_id", "domain", name="uq_reconciliation_domain"),
        sa.CheckConstraint("domain IN ('SETTLEMENT', 'PURSUIT')", name="ck_reconciliation_domain"),
        sa.CheckConstraint(
            "state IN ('RECONCILED_NONE', 'RECONCILED_COMPLETE', 'UNKNOWN')",
            name="ck_reconciliation_state",
        ),
        sa.CheckConstraint("source_set_sha256 ~ '^[0-9a-f]{64}$'", name="ck_reconciliation_hash"),
    )
    op.execute("ALTER TABLE reconciliation_state ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE reconciliation_state FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY reconciliation_state_tenant_isolation ON reconciliation_state "
        "FOR ALL TO recovery_owner, recovery_app, recovery_worker "
        "USING (org_id = nullif(current_setting('app.current_org_id', true), '')) "
        "WITH CHECK (org_id = nullif(current_setting('app.current_org_id', true), ''))"
    )
    op.execute("ALTER TABLE reconciliation_state OWNER TO recovery_owner")
    op.execute("GRANT SELECT, INSERT ON reconciliation_state TO recovery_app")
    op.execute("GRANT SELECT ON reconciliation_state TO recovery_worker")


def downgrade() -> None:
    op.execute("DROP POLICY reconciliation_state_tenant_isolation ON reconciliation_state")
    op.drop_table("reconciliation_state")
