"""Add canonical source, financial-event, and evidence records.

Revision ID: 0004_v02_sources
Revises: 0003_v01_readiness
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0004_v02_sources"
down_revision = "0003_v01_readiness"
branch_labels = None
depends_on = None


def _policy(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {table}_tenant_isolation ON {table} "
        "FOR ALL TO recovery_owner, recovery_app, recovery_worker "
        "USING (org_id = nullif(current_setting('app.current_org_id', true), '')) "
        "WITH CHECK (org_id = nullif(current_setting('app.current_org_id', true), ''))"
    )


def upgrade() -> None:
    op.create_table(
        "source_record_version",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("org_id", sa.String(128), nullable=False),
        sa.Column("source_kind", sa.String(64), nullable=False),
        sa.Column("source_record_id", sa.String(256), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("raw_envelope_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("row_number", sa.Integer(), nullable=True),
        sa.Column("declared_org_id", sa.String(128), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("source_created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["org_id", "raw_envelope_id"], ["raw_envelope.org_id", "raw_envelope.id"], name="fk_source_record_raw_envelope_tenant"),
        sa.UniqueConstraint("org_id", "source_kind", "source_record_id", "content_sha256", name="uq_source_record_version_content"),
        sa.UniqueConstraint("org_id", "id", name="uq_source_record_version_org_id"),
        sa.CheckConstraint("btrim(org_id) <> ''", name="ck_source_record_version_org_nonempty"),
    )
    op.create_index("ix_source_record_version_org_id", "source_record_version", ["org_id"])
    op.create_table(
        "financial_event",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("org_id", sa.String(128), nullable=False),
        sa.Column("source_record_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(128), nullable=False),
        sa.Column("direction", sa.String(16), nullable=False),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("quantity", sa.Float(), nullable=True),
        sa.Column("posting_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("posting_time_precision", sa.String(16), nullable=True),
        sa.Column("incident_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("incident_time_precision", sa.String(16), nullable=True),
        sa.Column("business_references", postgresql.JSONB(), nullable=False),
        sa.Column("normalized_fields", postgresql.JSONB(), nullable=False),
        sa.ForeignKeyConstraint(["org_id", "source_record_version_id"], ["source_record_version.org_id", "source_record_version.id"], name="fk_financial_event_source_version_tenant"),
        sa.UniqueConstraint("org_id", "source_record_version_id", name="uq_financial_event_source_version"),
        sa.UniqueConstraint("org_id", "id", name="uq_financial_event_org_id"),
        sa.CheckConstraint("btrim(org_id) <> ''", name="ck_financial_event_org_nonempty"),
        sa.CheckConstraint("direction IN ('DEBIT', 'CREDIT', 'ADJUSTMENT')", name="ck_financial_event_direction"),
        sa.CheckConstraint("currency ~ '^[A-Z]{3}$'", name="ck_financial_event_currency"),
        sa.CheckConstraint("quantity IS NULL OR quantity >= 0", name="ck_financial_event_quantity"),
    )
    op.create_index("ix_financial_event_org_id", "financial_event", ["org_id"])
    op.create_table(
        "evidence_record",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("org_id", sa.String(128), nullable=False),
        sa.Column("source_record_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("evidence_kind", sa.String(64), nullable=False),
        sa.Column("observed_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("observed_time_precision", sa.String(16), nullable=True),
        sa.Column("coverage_quantity", sa.Float(), nullable=True),
        sa.Column("coverage_scope", postgresql.JSONB(), nullable=False),
        sa.Column("normalized_fields", postgresql.JSONB(), nullable=False),
        sa.ForeignKeyConstraint(["org_id", "source_record_version_id"], ["source_record_version.org_id", "source_record_version.id"], name="fk_evidence_record_source_version_tenant"),
        sa.UniqueConstraint("org_id", "source_record_version_id", name="uq_evidence_record_source_version"),
        sa.UniqueConstraint("org_id", "id", name="uq_evidence_record_org_id"),
        sa.CheckConstraint("btrim(org_id) <> ''", name="ck_evidence_record_org_nonempty"),
        sa.CheckConstraint("coverage_quantity IS NULL OR coverage_quantity >= 0", name="ck_evidence_record_coverage_quantity"),
    )
    op.create_index("ix_evidence_record_org_id", "evidence_record", ["org_id"])
    for table in ("source_record_version", "financial_event", "evidence_record"):
        _policy(table)
        op.execute(f"ALTER TABLE {table} OWNER TO recovery_owner")
    op.execute("GRANT SELECT, INSERT ON source_record_version, financial_event, evidence_record TO recovery_app")
    op.execute("GRANT SELECT ON source_record_version, financial_event, evidence_record TO recovery_worker")


def downgrade() -> None:
    for table in ("evidence_record", "financial_event", "source_record_version"):
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}")
    op.drop_table("evidence_record")
    op.drop_table("financial_event")
    op.drop_table("source_record_version")
