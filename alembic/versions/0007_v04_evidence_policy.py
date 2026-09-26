"""Add mechanically verifiable evidence assertions and policy provenance.

Revision ID: 0007_v04_evidence_policy
Revises: 0006_v03_ledger
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0007_v04_evidence_policy"
down_revision = "0006_v03_ledger"
branch_labels = None
depends_on = None


def _policy(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {table}_tenant_isolation ON {table} FOR ALL "
        "TO recovery_owner, recovery_app, recovery_worker "
        "USING (org_id = nullif(current_setting('app.current_org_id', true), '')) "
        "WITH CHECK (org_id = nullif(current_setting('app.current_org_id', true), ''))"
    )


def upgrade() -> None:
    op.create_table(
        "evidence_assertion",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("org_id", sa.String(128), nullable=False),
        sa.Column("evidence_record_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_record_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("proposition_key", sa.String(256), nullable=False),
        sa.Column("subject_key", sa.String(256), nullable=False),
        sa.Column("polarity", sa.String(16), nullable=False),
        sa.Column("fact_path", sa.String(512), nullable=False),
        sa.Column("asserted_value", postgresql.JSONB(), nullable=False),
        sa.Column("scope", postgresql.JSONB(), nullable=False),
        sa.Column("decisive", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["org_id", "evidence_record_id"], ["evidence_record.org_id", "evidence_record.id"], name="fk_assertion_evidence_tenant"),
        sa.ForeignKeyConstraint(["org_id", "source_record_version_id"], ["source_record_version.org_id", "source_record_version.id"], name="fk_assertion_source_tenant"),
        sa.UniqueConstraint("org_id", "id", name="uq_evidence_assertion_org_id"),
        sa.CheckConstraint("btrim(org_id) <> ''", name="ck_evidence_assertion_org_nonempty"),
        sa.CheckConstraint("polarity IN ('SUPPORTS', 'CONTRADICTS')", name="ck_evidence_assertion_polarity"),
    )
    op.create_index("ix_evidence_assertion_org_id", "evidence_assertion", ["org_id"])
    op.create_table(
        "evidence_lifecycle_event",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("org_id", sa.String(128), nullable=False),
        sa.Column("assertion_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["org_id", "assertion_id"], ["evidence_assertion.org_id", "evidence_assertion.id"], name="fk_evidence_lifecycle_assertion_tenant"),
        sa.CheckConstraint("btrim(org_id) <> ''", name="ck_evidence_lifecycle_org_nonempty"),
        sa.CheckConstraint("state IN ('AVAILABLE', 'REVOKED', 'SUPERSEDED')", name="ck_evidence_lifecycle_state"),
    )
    op.create_index("ix_evidence_lifecycle_event_org_id", "evidence_lifecycle_event", ["org_id"])
    op.create_table(
        "policy_source_version",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("org_id", sa.String(128), nullable=False),
        sa.Column("policy_key", sa.String(256), nullable=False),
        sa.Column("authority_class", sa.String(16), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("effective_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("applicability", postgresql.JSONB(), nullable=False),
        sa.Column("raw_text", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("org_id", "policy_key", "content_sha256", name="uq_policy_source_version_content"),
        sa.UniqueConstraint("org_id", "id", name="uq_policy_source_version_org_id"),
        sa.CheckConstraint("btrim(org_id) <> ''", name="ck_policy_source_org_nonempty"),
        sa.CheckConstraint("authority_class IN ('OFFICIAL', 'SYNTHETIC', 'UNVERIFIED')", name="ck_policy_source_authority"),
    )
    op.create_index("ix_policy_source_version_org_id", "policy_source_version", ["org_id"])
    for table in ("evidence_assertion", "evidence_lifecycle_event", "policy_source_version"):
        _policy(table)
        op.execute(f"ALTER TABLE {table} OWNER TO recovery_owner")
        op.execute(f"GRANT SELECT, INSERT ON {table} TO recovery_app")
        op.execute(f"GRANT SELECT ON {table} TO recovery_worker")


def downgrade() -> None:
    for table in ("policy_source_version", "evidence_lifecycle_event", "evidence_assertion"):
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}")
        op.drop_table(table)
