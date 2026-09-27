"""Restrict machine assessment publication to one tenant-bound database boundary.

Revision ID: 0013_guarded_publication
Revises: 0012_v05_assessment_outcomes
"""

from alembic import op

revision = "0013_guarded_publication"
down_revision = "0012_v05_assessment_outcomes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("REVOKE INSERT ON recovery_assessment FROM recovery_app")
    op.execute("REVOKE INSERT, UPDATE, DELETE ON current_recovery_recommendation FROM recovery_app")
    op.execute(
        """
        CREATE FUNCTION public.publish_recovery_assessment(
          p_assessment_id uuid,
          p_obligation_id uuid,
          p_revision bigint,
          p_conclusion text,
          p_recoverable_minor bigint,
          p_currency text,
          p_snapshot jsonb
        ) RETURNS uuid
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
        DECLARE tenant text; current_revision bigint;
        BEGIN
          tenant := nullif(current_setting('app.current_org_id', true), '');
          IF tenant IS NULL THEN RAISE EXCEPTION 'tenant context is required'; END IF;
          SELECT decision_revision INTO current_revision
          FROM public.tenant_state WHERE org_id = tenant FOR UPDATE;
          IF NOT FOUND THEN RAISE EXCEPTION 'tenant state is missing'; END IF;
          IF p_revision <> current_revision THEN RAISE EXCEPTION 'assessment snapshot is stale'; END IF;
          IF p_snapshot IS NULL OR p_snapshot->>'tenant_revision' IS DISTINCT FROM p_revision::text THEN
            RAISE EXCEPTION 'assessment snapshot revision mismatch';
          END IF;
          PERFORM 1 FROM public.economic_obligation
          WHERE org_id = tenant AND id = p_obligation_id;
          IF NOT FOUND THEN RAISE EXCEPTION 'tenant-qualified obligation is required'; END IF;
          IF p_conclusion NOT IN ('REVIEW', 'NO_CLAIM', 'RESOLVED', 'ALREADY_PURSUED', 'SYNTHETIC_CLAIM_READY') THEN
            RAISE EXCEPTION 'invalid assessment conclusion';
          END IF;
          IF (p_conclusion = 'SYNTHETIC_CLAIM_READY' AND (p_recoverable_minor IS NULL OR p_recoverable_minor <= 0 OR p_currency IS NULL))
             OR (p_conclusion <> 'SYNTHETIC_CLAIM_READY' AND p_recoverable_minor IS NOT NULL) THEN
            RAISE EXCEPTION 'invalid assessment amount for conclusion';
          END IF;
          INSERT INTO public.recovery_assessment
            (id, org_id, obligation_id, tenant_revision, conclusion, recoverable_minor, currency, dependency_snapshot)
          VALUES
            (p_assessment_id, tenant, p_obligation_id, p_revision, p_conclusion, p_recoverable_minor, p_currency, p_snapshot);
          INSERT INTO public.current_recovery_recommendation (org_id, obligation_id, assessment_id)
          VALUES (tenant, p_obligation_id, p_assessment_id)
          ON CONFLICT (org_id, obligation_id) DO UPDATE SET assessment_id = EXCLUDED.assessment_id;
          RETURN p_assessment_id;
        END;
        $$
        """
    )
    op.execute("ALTER FUNCTION public.publish_recovery_assessment(uuid, uuid, bigint, text, bigint, text, jsonb) OWNER TO recovery_owner")
    op.execute("REVOKE ALL ON FUNCTION public.publish_recovery_assessment(uuid, uuid, bigint, text, bigint, text, jsonb) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION public.publish_recovery_assessment(uuid, uuid, bigint, text, bigint, text, jsonb) TO recovery_app")


def downgrade() -> None:
    op.execute("REVOKE EXECUTE ON FUNCTION public.publish_recovery_assessment(uuid, uuid, bigint, text, bigint, text, jsonb) FROM recovery_app")
    op.execute("DROP FUNCTION public.publish_recovery_assessment(uuid, uuid, bigint, text, bigint, text, jsonb)")
    op.execute("GRANT INSERT ON recovery_assessment TO recovery_app")
    op.execute("GRANT INSERT, UPDATE, DELETE ON current_recovery_recommendation TO recovery_app")
