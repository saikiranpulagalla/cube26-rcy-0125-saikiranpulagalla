"""Add v0.5 publication, reservation, immutability and revision guards.

Revision ID: 0009_v05_publication_guards
Revises: 0008_v05_assessment
"""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0009_v05_publication_guards"
down_revision = "0008_v05_assessment"
branch_labels = None
depends_on = None


def _policy(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY {table}_tenant_isolation ON {table} FOR ALL TO recovery_owner, recovery_app, recovery_worker USING (org_id = nullif(current_setting('app.current_org_id', true), '')) WITH CHECK (org_id = nullif(current_setting('app.current_org_id', true), ''))")


def upgrade() -> None:
    op.add_column("synthetic_packet_reservation", sa.Column("idempotency_key", sa.String(256), nullable=False, server_default="legacy-unusable"))
    op.add_column("synthetic_packet_reservation", sa.Column("pursuit_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_unique_constraint("uq_packet_org_idempotency", "synthetic_packet_reservation", ["org_id", "idempotency_key"])
    op.create_foreign_key("fk_packet_pursuit_tenant", "synthetic_packet_reservation", "claim_pursuit", ["org_id", "pursuit_id"], ["org_id", "id"])
    op.create_table("current_recovery_recommendation", sa.Column("org_id", sa.String(128), primary_key=True), sa.Column("obligation_id", postgresql.UUID(as_uuid=True), primary_key=True), sa.Column("assessment_id", postgresql.UUID(as_uuid=True), nullable=False), sa.ForeignKeyConstraint(["org_id", "obligation_id"], ["economic_obligation.org_id", "economic_obligation.id"], name="fk_current_obligation_tenant"), sa.ForeignKeyConstraint(["org_id", "assessment_id"], ["recovery_assessment.org_id", "recovery_assessment.id"], name="fk_current_assessment_tenant"), sa.UniqueConstraint("org_id", "assessment_id", name="uq_current_assessment_tenant"))
    _policy("current_recovery_recommendation")
    op.execute("ALTER TABLE current_recovery_recommendation OWNER TO recovery_owner")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON current_recovery_recommendation TO recovery_app")
    op.execute("CREATE FUNCTION immutable_assessment() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'assessments are immutable'; END; $$")
    op.execute("CREATE TRIGGER recovery_assessment_immutable BEFORE UPDATE OR DELETE ON recovery_assessment FOR EACH ROW EXECUTE FUNCTION immutable_assessment()")
    op.execute("CREATE FUNCTION revision_bump() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$ BEGIN UPDATE tenant_state SET decision_revision = decision_revision + 1, updated_at = now() WHERE org_id = NEW.org_id; RETURN NEW; END; $$")
    for table in ("economic_obligation", "amount_derivation", "settlement_allocation", "settlement_reversal", "claim_pursuit", "pursuit_allocation", "evidence_assertion", "evidence_lifecycle_event", "policy_source_version"):
        op.execute(f"CREATE TRIGGER {table}_revision_bump AFTER INSERT OR UPDATE ON {table} FOR EACH ROW EXECUTE FUNCTION revision_bump()")


def downgrade() -> None:
    for table in ("economic_obligation", "amount_derivation", "settlement_allocation", "settlement_reversal", "claim_pursuit", "pursuit_allocation", "evidence_assertion", "evidence_lifecycle_event", "policy_source_version"):
        op.execute(f"DROP TRIGGER {table}_revision_bump ON {table}")
    op.execute("DROP FUNCTION revision_bump()")
    op.execute("DROP TRIGGER recovery_assessment_immutable ON recovery_assessment")
    op.execute("DROP FUNCTION immutable_assessment()")
    op.execute("DROP POLICY IF EXISTS current_recovery_recommendation_tenant_isolation ON current_recovery_recommendation")
    op.drop_table("current_recovery_recommendation")
    op.drop_constraint("fk_packet_pursuit_tenant", "synthetic_packet_reservation", type_="foreignkey")
    op.drop_constraint("uq_packet_org_idempotency", "synthetic_packet_reservation", type_="unique")
    op.drop_column("synthetic_packet_reservation", "pursuit_id")
    op.drop_column("synthetic_packet_reservation", "idempotency_key")
