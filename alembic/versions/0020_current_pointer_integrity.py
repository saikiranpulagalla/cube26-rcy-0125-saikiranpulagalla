"""Require a current pointer to reference an assessment for its own obligation.

Revision ID: 0020_current_pointer_integrity
Revises: 0019_worker_publication
"""

from alembic import op

revision = "0020_current_pointer_integrity"
down_revision = "0019_worker_publication"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION public.guard_current_recovery_pointer() RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, public AS $$
        DECLARE assessment_obligation uuid;
        BEGIN
          SELECT obligation_id INTO assessment_obligation
          FROM public.recovery_assessment
          WHERE org_id = NEW.org_id AND id = NEW.assessment_id;
          IF assessment_obligation IS NULL OR assessment_obligation <> NEW.obligation_id THEN
            RAISE EXCEPTION 'current assessment must belong to the same tenant-qualified obligation';
          END IF;
          RETURN NEW;
        END;
        $$
        """
    )
    op.execute("ALTER FUNCTION public.guard_current_recovery_pointer() OWNER TO recovery_owner")
    op.execute(
        "CREATE TRIGGER current_recovery_pointer_guard BEFORE INSERT OR UPDATE "
        "ON current_recovery_recommendation FOR EACH ROW "
        "EXECUTE FUNCTION public.guard_current_recovery_pointer()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER current_recovery_pointer_guard ON current_recovery_recommendation")
    op.execute("DROP FUNCTION public.guard_current_recovery_pointer()")
