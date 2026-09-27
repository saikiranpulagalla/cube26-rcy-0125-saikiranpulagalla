"""Add synthetic-only assessments and non-filing packet reservations.

Revision ID: 0008_v05_assessment
Revises: 0007_v04_evidence_policy
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0008_v05_assessment"
down_revision = "0007_v04_evidence_policy"
branch_labels = None
depends_on = None


def _policy(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {table}_tenant_isolation ON {table} FOR ALL TO recovery_owner, recovery_app, recovery_worker USING (org_id = nullif(current_setting('app.current_org_id', true), '')) WITH CHECK (org_id = nullif(current_setting('app.current_org_id', true), ''))"
    )


def upgrade() -> None:
    op.create_table(
        "recovery_assessment",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("org_id", sa.String(128), nullable=False),
        sa.Column("obligation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_revision", sa.BigInteger(), nullable=False),
        sa.Column("conclusion", sa.String(32), nullable=False),
        sa.Column("recoverable_minor", sa.BigInteger()),
        sa.Column("currency", sa.String(3)),
        sa.Column("dependency_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "obligation_id"],
            ["economic_obligation.org_id", "economic_obligation.id"],
            name="fk_assessment_obligation_tenant",
        ),
        sa.UniqueConstraint("org_id", "id", name="uq_recovery_assessment_org_id"),
        sa.CheckConstraint(
            "conclusion IN ('REVIEW', 'NO_CLAIM', 'SYNTHETIC_CLAIM_READY')",
            name="ck_assessment_conclusion",
        ),
        sa.CheckConstraint(
            "recoverable_minor IS NULL OR recoverable_minor > 0",
            name="ck_assessment_amount_positive",
        ),
    )
    op.create_index("ix_recovery_assessment_org_id", "recovery_assessment", ["org_id"])
    op.create_table(
        "synthetic_packet_reservation",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("org_id", sa.String(128), nullable=False),
        sa.Column("assessment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("packet", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "assessment_id"],
            ["recovery_assessment.org_id", "recovery_assessment.id"],
            name="fk_packet_assessment_tenant",
        ),
        sa.UniqueConstraint("org_id", "assessment_id", name="uq_packet_assessment_tenant"),
    )
    op.create_index(
        "ix_synthetic_packet_reservation_org_id", "synthetic_packet_reservation", ["org_id"]
    )
    for table in ("recovery_assessment", "synthetic_packet_reservation"):
        _policy(table)
        op.execute(f"ALTER TABLE {table} OWNER TO recovery_owner")
        op.execute(f"GRANT SELECT, INSERT ON {table} TO recovery_app")
        op.execute(f"GRANT SELECT ON {table} TO recovery_worker")


def downgrade() -> None:
    for table in ("synthetic_packet_reservation", "recovery_assessment"):
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}")
        op.drop_table(table)
