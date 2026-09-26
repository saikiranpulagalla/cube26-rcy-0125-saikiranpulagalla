"""Harden the v0.1 persistence and role boundaries.

Revision ID: 0002_v01_repair
Revises: 0001_v01
"""

from alembic import op

revision = "0002_v01_repair"
down_revision = "0001_v01"
branch_labels = None
depends_on = None

PROTECTED = ("tenant_state", "raw_envelope", "work_intent", "work_attempt", "audit_event")


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1 FROM tenant_state WHERE btrim(org_id) = ''
            UNION ALL SELECT 1 FROM raw_envelope WHERE btrim(org_id) = ''
            UNION ALL SELECT 1 FROM work_intent WHERE btrim(org_id) = ''
            UNION ALL SELECT 1 FROM work_attempt WHERE btrim(org_id) = ''
            UNION ALL SELECT 1 FROM audit_event WHERE btrim(org_id) = ''
          ) THEN
            RAISE EXCEPTION 'v0.1 repair cannot upgrade blank tenant records; remediate under audited procedure first';
          END IF;
        END;
        $$
        """
    )
    for table in PROTECTED:
        op.execute(f"DROP POLICY {table}_tenant_isolation ON {table}")
        op.execute(
            f"CREATE POLICY {table}_tenant_isolation ON {table} "
            "FOR ALL TO recovery_owner, recovery_app, recovery_worker "
            "USING (org_id = nullif(current_setting('app.current_org_id', true), '')) "
            "WITH CHECK (org_id = nullif(current_setting('app.current_org_id', true), ''))"
        )
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} ADD CONSTRAINT ck_{table}_org_nonempty CHECK (btrim(org_id) <> '')")

    op.execute("ALTER TABLE tenant_state ADD CONSTRAINT ck_tenant_revision_nonnegative CHECK (decision_revision >= 0)")
    op.execute(
        "ALTER TABLE work_intent ADD CONSTRAINT ck_work_state_known "
        "CHECK (state IN ('QUEUED', 'RUNNING', 'RETRYABLE_FAILURE', 'TERMINAL_FAILURE', 'COMPLETED'))"
    )
    op.execute(
        "ALTER TABLE work_intent ADD CONSTRAINT ck_work_lease_consistent CHECK "
        "((state = 'RUNNING' AND lease_owner IS NOT NULL AND lease_token IS NOT NULL AND lease_expires_at IS NOT NULL) "
        "OR (state <> 'RUNNING' AND lease_owner IS NULL AND lease_token IS NULL AND lease_expires_at IS NULL))"
    )

    op.execute(
        """
        CREATE FUNCTION advance_tenant_revision(requested_org text) RETURNS bigint
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
        DECLARE result bigint;
        BEGIN
          IF requested_org IS NULL OR btrim(requested_org) = ''
             OR requested_org <> current_setting('app.current_org_id', true) THEN
            RAISE EXCEPTION 'invalid tenant revision context';
          END IF;
          INSERT INTO tenant_state (org_id, decision_revision) VALUES (requested_org, 0)
          ON CONFLICT (org_id) DO NOTHING;
          UPDATE tenant_state SET decision_revision = decision_revision + 1
          WHERE org_id = requested_org RETURNING decision_revision INTO result;
          RETURN result;
        END;
        $$
        """
    )
    op.execute("ALTER FUNCTION advance_tenant_revision(text) OWNER TO recovery_owner")
    op.execute("REVOKE ALL ON FUNCTION advance_tenant_revision(text) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION advance_tenant_revision(text) TO recovery_app")

    op.execute(
        """
        CREATE FUNCTION guard_tenant_revision() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          IF NEW.org_id <> OLD.org_id OR NEW.decision_revision < OLD.decision_revision THEN
            RAISE EXCEPTION 'tenant revision is append-only';
          END IF;
          RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER tenant_revision_append_only BEFORE UPDATE ON tenant_state "
        "FOR EACH ROW EXECUTE FUNCTION guard_tenant_revision()"
    )
    op.execute(
        """
        CREATE FUNCTION guard_work_intent_transition() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          IF NEW.org_id <> OLD.org_id OR NEW.envelope_id <> OLD.envelope_id OR NEW.max_attempts <> OLD.max_attempts THEN
            RAISE EXCEPTION 'work identity is immutable';
          END IF;
          IF NOT ((OLD.state = 'QUEUED' AND NEW.state = 'RUNNING' AND NEW.attempt_count = OLD.attempt_count + 1)
               OR (OLD.state = 'RUNNING' AND NEW.state IN ('COMPLETED', 'RETRYABLE_FAILURE', 'TERMINAL_FAILURE')
                   AND NEW.attempt_count = OLD.attempt_count)
               OR (OLD.state = 'RETRYABLE_FAILURE' AND NEW.state IN ('QUEUED', 'TERMINAL_FAILURE')
                   AND NEW.attempt_count = OLD.attempt_count)) THEN
            RAISE EXCEPTION 'illegal work state transition';
          END IF;
          RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER work_transition_guard BEFORE UPDATE ON work_intent "
        "FOR EACH ROW EXECUTE FUNCTION guard_work_intent_transition()"
    )
    op.execute(
        """
        CREATE FUNCTION guard_work_attempt() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          IF NEW.org_id <> OLD.org_id OR NEW.work_intent_id <> OLD.work_intent_id
             OR NEW.attempt_number <> OLD.attempt_number OR NEW.lease_owner <> OLD.lease_owner
             OR NEW.lease_token <> OLD.lease_token OR NEW.started_at <> OLD.started_at
             OR OLD.finished_at IS NOT NULL OR NEW.finished_at IS NULL OR NEW.outcome IS NULL THEN
            RAISE EXCEPTION 'work attempt history is append-only';
          END IF;
          RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER work_attempt_guard BEFORE UPDATE ON work_attempt "
        "FOR EACH ROW EXECUTE FUNCTION guard_work_attempt()"
    )

    for table in PROTECTED:
        op.execute(f"REVOKE ALL ON {table} FROM recovery_app, recovery_worker")
    op.execute("GRANT SELECT ON tenant_state, raw_envelope, work_intent, work_attempt, audit_event TO recovery_app")
    op.execute("GRANT INSERT ON raw_envelope, work_intent, audit_event TO recovery_app")
    op.execute("GRANT SELECT ON tenant_state, raw_envelope, work_intent, work_attempt, audit_event TO recovery_worker")
    op.execute("GRANT UPDATE (state, attempt_count, next_run_at, lease_owner, lease_token, lease_expires_at, last_error, completed_at) ON work_intent TO recovery_worker")
    op.execute("GRANT INSERT, UPDATE (finished_at, outcome, detail) ON work_attempt TO recovery_worker")
    op.execute("GRANT INSERT ON audit_event TO recovery_worker")


def downgrade() -> None:
    for table in PROTECTED:
        op.execute(f"DROP POLICY {table}_tenant_isolation ON {table}")
        op.execute(
            f"CREATE POLICY {table}_tenant_isolation ON {table} FOR ALL TO recovery_app "
            "USING (org_id = current_setting('app.current_org_id', true)) "
            "WITH CHECK (org_id = current_setting('app.current_org_id', true))"
        )
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO recovery_app")
    op.execute("DROP TRIGGER work_attempt_guard ON work_attempt")
    op.execute("DROP FUNCTION guard_work_attempt()")
    op.execute("DROP TRIGGER work_transition_guard ON work_intent")
    op.execute("DROP FUNCTION guard_work_intent_transition()")
    op.execute("DROP TRIGGER tenant_revision_append_only ON tenant_state")
    op.execute("DROP FUNCTION guard_tenant_revision()")
    op.execute("DROP FUNCTION advance_tenant_revision(text)")
    op.execute("ALTER TABLE work_intent DROP CONSTRAINT ck_work_lease_consistent")
    op.execute("ALTER TABLE work_intent DROP CONSTRAINT ck_work_state_known")
    op.execute("ALTER TABLE tenant_state DROP CONSTRAINT ck_tenant_revision_nonnegative")
    for table in PROTECTED:
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT ck_{table}_org_nonempty")
