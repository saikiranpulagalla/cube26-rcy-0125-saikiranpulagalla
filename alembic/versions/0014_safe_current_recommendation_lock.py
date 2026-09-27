"""Allow export to lock a current pointer without pointer mutation authority.

Revision ID: 0014_current_pointer_lock
Revises: 0013_guarded_publication
"""

from alembic import op

revision = "0014_current_pointer_lock"
down_revision = "0013_guarded_publication"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION public.lock_current_recovery_assessment(p_obligation_id uuid)
        RETURNS uuid
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
        DECLARE tenant text; result uuid;
        BEGIN
          tenant := nullif(current_setting('app.current_org_id', true), '');
          IF tenant IS NULL THEN RAISE EXCEPTION 'tenant context is required'; END IF;
          SELECT assessment_id INTO result
          FROM public.current_recovery_recommendation
          WHERE org_id = tenant AND obligation_id = p_obligation_id
          FOR UPDATE;
          IF NOT FOUND THEN RAISE EXCEPTION 'current recommendation is missing'; END IF;
          RETURN result;
        END;
        $$
        """
    )
    op.execute("ALTER FUNCTION public.lock_current_recovery_assessment(uuid) OWNER TO recovery_owner")
    op.execute("REVOKE ALL ON FUNCTION public.lock_current_recovery_assessment(uuid) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION public.lock_current_recovery_assessment(uuid) TO recovery_app")


def downgrade() -> None:
    op.execute("REVOKE EXECUTE ON FUNCTION public.lock_current_recovery_assessment(uuid) FROM recovery_app")
    op.execute("DROP FUNCTION public.lock_current_recovery_assessment(uuid)")
