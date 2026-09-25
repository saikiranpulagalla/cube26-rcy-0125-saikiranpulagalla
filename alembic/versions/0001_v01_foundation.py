"""v0.1 safe ingestion foundation

Revision ID: 0001_v01
Revises:
Create Date: 2026-09-25
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0001_v01"
down_revision = None
branch_labels = None
depends_on = None

PROTECTED = ("tenant_state", "raw_envelope", "work_intent", "work_attempt", "audit_event")


def _tenant_policy(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {table}_tenant_isolation ON {table} "
        "FOR ALL TO recovery_app "
        "USING (org_id = current_setting('app.current_org_id', true)) "
        "WITH CHECK (org_id = current_setting('app.current_org_id', true))"
    )


def upgrade() -> None:
    op.create_table(
        "tenant_state",
        sa.Column("org_id", sa.String(length=128), primary_key=True),
        sa.Column("decision_revision", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_table(
        "raw_envelope",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("org_id", sa.String(length=128), nullable=False),
        sa.Column("actor_id", sa.String(length=128), nullable=False),
        sa.Column("actor_role", sa.String(length=64), nullable=False),
        sa.Column("source_name", sa.String(length=256), nullable=False),
        sa.Column("content_type", sa.String(length=128), nullable=False),
        sa.Column("input_format", sa.String(length=32), nullable=False),
        sa.Column("raw_bytes", sa.LargeBinary(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=256), nullable=False),
        sa.Column("validation_status", sa.String(length=32), nullable=False),
        sa.Column("quarantine_reason", sa.Text(), nullable=True),
        sa.Column("declared_source_orgs", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("fixture_provenance", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("octet_length(raw_bytes) > 0", name="ck_raw_envelope_nonempty"),
        sa.UniqueConstraint("org_id", "idempotency_key", name="uq_raw_envelope_org_idempotency"),
        sa.UniqueConstraint("org_id", "id", name="uq_raw_envelope_org_id"),
    )
    op.create_index("ix_raw_envelope_org_id", "raw_envelope", ["org_id"])
    op.create_table(
        "work_intent",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("org_id", sa.String(length=128), nullable=False),
        sa.Column("envelope_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False, server_default="QUEUED"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column(
            "next_run_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("lease_owner", sa.String(length=128), nullable=True),
        sa.Column("lease_token", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("attempt_count >= 0", name="ck_work_attempt_nonnegative"),
        sa.ForeignKeyConstraint(
            ["org_id", "envelope_id"],
            ["raw_envelope.org_id", "raw_envelope.id"],
            name="fk_work_envelope_tenant",
        ),
        sa.UniqueConstraint("org_id", "envelope_id", name="uq_work_envelope_tenant"),
        sa.UniqueConstraint("org_id", "id", name="uq_work_intent_org_id"),
    )
    op.create_index("ix_work_intent_org_id", "work_intent", ["org_id"])
    op.create_table(
        "work_attempt",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("org_id", sa.String(length=128), nullable=False),
        sa.Column("work_intent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("lease_owner", sa.String(length=128), nullable=False),
        sa.Column("lease_token", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("outcome", sa.String(length=64), nullable=True),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["org_id", "work_intent_id"],
            ["work_intent.org_id", "work_intent.id"],
            name="fk_attempt_work_tenant",
        ),
        sa.UniqueConstraint(
            "org_id", "work_intent_id", "attempt_number", name="uq_attempt_number_tenant"
        ),
    )
    op.create_index("ix_work_attempt_org_id", "work_attempt", ["org_id"])
    op.create_table(
        "audit_event",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("org_id", sa.String(length=128), nullable=False),
        sa.Column("actor_id", sa.String(length=128), nullable=True),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("subject_type", sa.String(length=64), nullable=False),
        sa.Column("subject_id", sa.String(length=128), nullable=False),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("event_type <> ''", name="ck_audit_event_nonempty_type"),
    )
    op.create_index("ix_audit_event_org_id", "audit_event", ["org_id"])

    for table in PROTECTED:
        _tenant_policy(table)

    for table in PROTECTED:
        op.execute(f"ALTER TABLE {table} OWNER TO recovery_owner")
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO recovery_app")


def downgrade() -> None:
    for table in reversed(PROTECTED):
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}")
    op.drop_table("audit_event")
    op.drop_table("work_attempt")
    op.drop_table("work_intent")
    op.drop_table("raw_envelope")
    op.drop_table("tenant_state")
