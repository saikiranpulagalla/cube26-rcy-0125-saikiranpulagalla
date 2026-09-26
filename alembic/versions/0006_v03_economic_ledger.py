"""Add obligation-specific settlement and pursuit ledger primitives.

Revision ID: 0006_v03_ledger
Revises: 0005_v02_quantity
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0006_v03_ledger"
down_revision = "0005_v02_quantity"
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
        "economic_obligation",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("org_id", sa.String(128), nullable=False),
        sa.Column("economic_key", sa.String(256), nullable=False),
        sa.Column("financial_event_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("recovery_basis", sa.String(32), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("business_instance", postgresql.JSONB(), nullable=False),
        sa.Column("quantity_scope", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["org_id", "financial_event_id"], ["financial_event.org_id", "financial_event.id"], name="fk_obligation_financial_event_tenant"),
        sa.UniqueConstraint("org_id", "economic_key", name="uq_obligation_economic_key"),
        sa.UniqueConstraint("org_id", "id", name="uq_obligation_org_id"),
        sa.CheckConstraint("btrim(org_id) <> ''", name="ck_obligation_org_nonempty"),
        sa.CheckConstraint("recovery_basis IN ('INVALID_FEE', 'ELIGIBLE_LOSS_DAMAGE', 'DUPLICATE_BILLING', 'UNDER_REIMBURSEMENT', 'OTHER_SUPPORTED', 'UNKNOWN')", name="ck_obligation_recovery_basis"),
        sa.CheckConstraint("currency ~ '^[A-Z]{3}$'", name="ck_obligation_currency"),
    )
    op.create_table(
        "amount_derivation",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("org_id", sa.String(128), nullable=False),
        sa.Column("obligation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("derivation_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("observed_amount_minor", sa.BigInteger(), nullable=True),
        sa.Column("expected_amount_minor", sa.BigInteger(), nullable=True),
        sa.Column("justified_entitlement_minor", sa.BigInteger(), nullable=True),
        sa.Column("rounding_rule", sa.String(128), nullable=False),
        sa.Column("basis_class", sa.String(32), nullable=False),
        sa.Column("source_basis", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["org_id", "obligation_id"], ["economic_obligation.org_id", "economic_obligation.id"], name="fk_amount_derivation_obligation_tenant"),
        sa.UniqueConstraint("org_id", "obligation_id", "derivation_version", name="uq_amount_derivation_version"),
        sa.CheckConstraint("btrim(org_id) <> ''", name="ck_amount_derivation_org_nonempty"),
        sa.CheckConstraint("currency ~ '^[A-Z]{3}$'", name="ck_amount_derivation_currency"),
        sa.CheckConstraint("observed_amount_minor IS NULL OR observed_amount_minor >= 0", name="ck_amount_derivation_observed_nonnegative"),
        sa.CheckConstraint("expected_amount_minor IS NULL OR expected_amount_minor >= 0", name="ck_amount_derivation_expected_nonnegative"),
        sa.CheckConstraint("justified_entitlement_minor IS NULL OR justified_entitlement_minor >= 0", name="ck_amount_derivation_entitlement_nonnegative"),
    )
    op.create_index("ix_amount_derivation_org_id", "amount_derivation", ["org_id"])
    op.create_table(
        "settlement_allocation",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("org_id", sa.String(128), nullable=False),
        sa.Column("credit_event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("obligation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("allocated_minor", sa.BigInteger(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["org_id", "credit_event_id"], ["financial_event.org_id", "financial_event.id"], name="fk_settlement_credit_event_tenant"),
        sa.ForeignKeyConstraint(["org_id", "obligation_id"], ["economic_obligation.org_id", "economic_obligation.id"], name="fk_settlement_obligation_tenant"),
        sa.UniqueConstraint("org_id", "id", name="uq_settlement_allocation_org_id"),
        sa.CheckConstraint("allocated_minor > 0", name="ck_settlement_allocation_positive"),
    )
    op.create_index("ix_settlement_allocation_org_id", "settlement_allocation", ["org_id"])
    op.create_table(
        "settlement_reversal",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("org_id", sa.String(128), nullable=False),
        sa.Column("allocation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("reversed_minor", sa.BigInteger(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["org_id", "allocation_id"], ["settlement_allocation.org_id", "settlement_allocation.id"], name="fk_settlement_reversal_allocation_tenant"),
        sa.CheckConstraint("reversed_minor > 0", name="ck_settlement_reversal_positive"),
    )
    op.create_index("ix_settlement_reversal_org_id", "settlement_reversal", ["org_id"])
    op.create_table(
        "claim_pursuit",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("org_id", sa.String(128), nullable=False),
        sa.Column("external_reference", sa.String(256), nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("declared_minor", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("org_id", "id", name="uq_claim_pursuit_org_id"),
        sa.CheckConstraint("btrim(org_id) <> ''", name="ck_claim_pursuit_org_nonempty"),
        sa.CheckConstraint("currency ~ '^[A-Z]{3}$'", name="ck_claim_pursuit_currency"),
        sa.CheckConstraint("declared_minor > 0", name="ck_claim_pursuit_positive"),
        sa.CheckConstraint("status IN ('RECOMMENDED', 'EXPORTED', 'SUBMITTED', 'PENDING', 'RESOLVED', 'REJECTED', 'WITHDRAWN')", name="ck_claim_pursuit_status"),
    )
    op.create_index("ix_claim_pursuit_org_id", "claim_pursuit", ["org_id"])
    op.create_table(
        "pursuit_allocation",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("org_id", sa.String(128), nullable=False),
        sa.Column("pursuit_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("obligation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("allocated_minor", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["org_id", "pursuit_id"], ["claim_pursuit.org_id", "claim_pursuit.id"], name="fk_pursuit_allocation_pursuit_tenant"),
        sa.ForeignKeyConstraint(["org_id", "obligation_id"], ["economic_obligation.org_id", "economic_obligation.id"], name="fk_pursuit_allocation_obligation_tenant"),
        sa.CheckConstraint("btrim(org_id) <> ''", name="ck_pursuit_allocation_org_nonempty"),
        sa.CheckConstraint("allocated_minor > 0", name="ck_pursuit_allocation_positive"),
    )
    op.create_index("ix_pursuit_allocation_org_id", "pursuit_allocation", ["org_id"])
    op.create_index("ix_economic_obligation_org_id", "economic_obligation", ["org_id"])
    for table in ("economic_obligation", "amount_derivation", "settlement_allocation", "settlement_reversal", "claim_pursuit", "pursuit_allocation"):
        _policy(table)
        op.execute(f"ALTER TABLE {table} OWNER TO recovery_owner")
        op.execute(f"GRANT SELECT, INSERT ON {table} TO recovery_app")
        op.execute(f"GRANT SELECT ON {table} TO recovery_worker")
    op.execute("GRANT UPDATE (status, external_reference) ON claim_pursuit TO recovery_app")
    op.execute(
        """
        CREATE FUNCTION guard_claim_pursuit_transition() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, public AS $$
        BEGIN
          IF NEW.org_id <> OLD.org_id OR NEW.currency <> OLD.currency
             OR NEW.declared_minor <> OLD.declared_minor OR NEW.created_at <> OLD.created_at THEN
            RAISE EXCEPTION 'claim pursuit identity and amount are immutable';
          END IF;
          IF NEW.status = OLD.status THEN
            IF OLD.external_reference IS NOT NULL
               AND NEW.external_reference IS DISTINCT FROM OLD.external_reference THEN
              RAISE EXCEPTION 'claim pursuit external reference is immutable once set';
            END IF;
            RETURN NEW;
          END IF;
          IF NOT (
            (OLD.status = 'RECOMMENDED' AND NEW.status IN ('EXPORTED', 'WITHDRAWN')) OR
            (OLD.status = 'EXPORTED' AND NEW.status IN ('SUBMITTED', 'PENDING', 'WITHDRAWN')) OR
            (OLD.status = 'SUBMITTED' AND NEW.status IN ('PENDING', 'RESOLVED', 'REJECTED', 'WITHDRAWN')) OR
            (OLD.status = 'PENDING' AND NEW.status IN ('RESOLVED', 'REJECTED', 'WITHDRAWN'))
          ) THEN
            RAISE EXCEPTION 'illegal claim pursuit status transition';
          END IF;
          RETURN NEW;
        END; $$
        """
    )
    op.execute("CREATE TRIGGER claim_pursuit_transition_guard BEFORE UPDATE ON claim_pursuit FOR EACH ROW EXECUTE FUNCTION guard_claim_pursuit_transition()")
    op.execute(
        """
        CREATE FUNCTION guard_settlement_allocation() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, public AS $$
        DECLARE available bigint; allocated bigint; event_direction text; event_currency text; obligation_currency text;
        BEGIN
          SELECT amount_minor, direction, currency INTO available, event_direction, event_currency FROM financial_event
          WHERE org_id = NEW.org_id AND id = NEW.credit_event_id FOR UPDATE;
          IF available IS NULL THEN RAISE EXCEPTION 'unknown credit event'; END IF;
          IF event_direction <> 'CREDIT' THEN RAISE EXCEPTION 'settlement allocation requires a credit event'; END IF;
          SELECT currency INTO obligation_currency FROM economic_obligation
          WHERE org_id = NEW.org_id AND id = NEW.obligation_id;
          IF obligation_currency IS NULL OR obligation_currency <> event_currency THEN
            RAISE EXCEPTION 'settlement allocation currency mismatch';
          END IF;
          SELECT COALESCE(SUM(allocated_minor), 0) INTO allocated FROM settlement_allocation
          WHERE org_id = NEW.org_id AND credit_event_id = NEW.credit_event_id;
          IF allocated + NEW.allocated_minor > available THEN
            RAISE EXCEPTION 'settlement allocation exceeds source credit';
          END IF;
          RETURN NEW;
        END; $$
        """
    )
    op.execute("CREATE TRIGGER settlement_allocation_guard BEFORE INSERT ON settlement_allocation FOR EACH ROW EXECUTE FUNCTION guard_settlement_allocation()")
    op.execute(
        """
        CREATE FUNCTION guard_settlement_reversal() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, public AS $$
        DECLARE allocated bigint; reversed bigint;
        BEGIN
          SELECT allocated_minor INTO allocated FROM settlement_allocation
          WHERE org_id = NEW.org_id AND id = NEW.allocation_id FOR UPDATE;
          IF allocated IS NULL THEN RAISE EXCEPTION 'unknown settlement allocation'; END IF;
          SELECT COALESCE(SUM(reversed_minor), 0) INTO reversed FROM settlement_reversal
          WHERE org_id = NEW.org_id AND allocation_id = NEW.allocation_id;
          IF reversed + NEW.reversed_minor > allocated THEN
            RAISE EXCEPTION 'settlement reversal exceeds allocation';
          END IF;
          RETURN NEW;
        END; $$
        """
    )
    op.execute("CREATE TRIGGER settlement_reversal_guard BEFORE INSERT ON settlement_reversal FOR EACH ROW EXECUTE FUNCTION guard_settlement_reversal()")
    op.execute(
        """
        CREATE FUNCTION guard_pursuit_allocation() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, public AS $$
        DECLARE declared bigint; allocated bigint; pursuit_currency text; obligation_currency text;
        BEGIN
          SELECT declared_minor, currency INTO declared, pursuit_currency FROM claim_pursuit
          WHERE org_id = NEW.org_id AND id = NEW.pursuit_id FOR UPDATE;
          IF declared IS NULL THEN RAISE EXCEPTION 'unknown claim pursuit'; END IF;
          SELECT currency INTO obligation_currency FROM economic_obligation
          WHERE org_id = NEW.org_id AND id = NEW.obligation_id;
          IF obligation_currency IS NULL OR obligation_currency <> pursuit_currency THEN
            RAISE EXCEPTION 'pursuit allocation currency mismatch';
          END IF;
          SELECT COALESCE(SUM(allocated_minor), 0) INTO allocated FROM pursuit_allocation
          WHERE org_id = NEW.org_id AND pursuit_id = NEW.pursuit_id;
          IF allocated + NEW.allocated_minor > declared THEN
            RAISE EXCEPTION 'pursuit allocation exceeds declared amount';
          END IF;
          RETURN NEW;
        END; $$
        """
    )
    op.execute("CREATE TRIGGER pursuit_allocation_guard BEFORE INSERT ON pursuit_allocation FOR EACH ROW EXECUTE FUNCTION guard_pursuit_allocation()")


def downgrade() -> None:
    op.execute("DROP TRIGGER claim_pursuit_transition_guard ON claim_pursuit")
    for name, table in (("pursuit_allocation_guard", "pursuit_allocation"), ("settlement_reversal_guard", "settlement_reversal"), ("settlement_allocation_guard", "settlement_allocation")):
        op.execute(f"DROP TRIGGER {name} ON {table}")
    op.execute("DROP FUNCTION guard_pursuit_allocation()")
    op.execute("DROP FUNCTION guard_claim_pursuit_transition()")
    op.execute("DROP FUNCTION guard_settlement_reversal()")
    op.execute("DROP FUNCTION guard_settlement_allocation()")
    for index in (
        "ix_pursuit_allocation_org_id", "ix_claim_pursuit_org_id", "ix_settlement_reversal_org_id",
        "ix_settlement_allocation_org_id", "ix_amount_derivation_org_id", "ix_economic_obligation_org_id",
    ):
        op.execute(f"DROP INDEX IF EXISTS {index}")
    for table in ("pursuit_allocation", "claim_pursuit", "settlement_reversal", "settlement_allocation", "amount_derivation", "economic_obligation"):
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}")
        op.drop_table(table)
