"""Register owner-managed synthetic fixture authority and policy lifecycle.

Revision ID: 0015_trusted_synthetic_authority
Revises: 0014_current_pointer_lock
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0015_trusted_synthetic_authority"
down_revision = "0014_current_pointer_lock"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "policy_source_version",
        sa.Column("lifecycle_state", sa.String(16), nullable=False, server_default="ACTIVE"),
    )
    op.create_check_constraint(
        "ck_policy_lifecycle",
        "policy_source_version",
        "lifecycle_state IN ('ACTIVE', 'SUPERSEDED', 'REVOKED')",
    )
    op.create_table(
        "synthetic_fixture_profile",
        sa.Column("org_id", sa.String(128), nullable=False),
        sa.Column("fixture_profile", sa.String(128), nullable=False),
        sa.Column("fixture_sha256", sa.String(64), nullable=False),
        sa.Column("policy_source_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["org_id", "policy_source_version_id"],
            ["policy_source_version.org_id", "policy_source_version.id"],
            name="fk_synthetic_profile_policy_tenant",
        ),
        sa.PrimaryKeyConstraint("org_id", "fixture_profile"),
        sa.UniqueConstraint("org_id", "fixture_sha256", name="uq_synthetic_profile_hash_tenant"),
        sa.CheckConstraint("btrim(org_id) <> ''", name="ck_synthetic_profile_org_nonempty"),
        sa.CheckConstraint("fixture_sha256 ~ '^[0-9a-f]{64}$'", name="ck_synthetic_profile_hash"),
    )
    op.execute("ALTER TABLE synthetic_fixture_profile ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE synthetic_fixture_profile FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY synthetic_fixture_profile_tenant_isolation ON synthetic_fixture_profile "
        "FOR ALL TO recovery_owner, recovery_app, recovery_worker "
        "USING (org_id = nullif(current_setting('app.current_org_id', true), '')) "
        "WITH CHECK (org_id = nullif(current_setting('app.current_org_id', true), ''))"
    )
    op.execute("ALTER TABLE synthetic_fixture_profile OWNER TO recovery_owner")
    op.execute("GRANT SELECT ON synthetic_fixture_profile TO recovery_app, recovery_worker")
    op.execute("REVOKE INSERT, UPDATE, DELETE ON policy_source_version FROM recovery_app")


def downgrade() -> None:
    op.execute("GRANT INSERT ON policy_source_version TO recovery_app")
    op.execute("DROP POLICY synthetic_fixture_profile_tenant_isolation ON synthetic_fixture_profile")
    op.drop_table("synthetic_fixture_profile")
    op.drop_constraint("ck_policy_lifecycle", "policy_source_version", type_="check")
    op.drop_column("policy_source_version", "lifecycle_state")
