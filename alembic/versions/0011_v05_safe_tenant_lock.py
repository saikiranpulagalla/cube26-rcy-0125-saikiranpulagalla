"""Replace broad runtime revision update permission with a narrow lock primitive.

Revision ID: 0011_v05_safe_tenant_lock
Revises: 0010_v05_tenant_lock_grant
"""

from alembic import op

revision = "0011_v05_safe_tenant_lock"
down_revision = "0010_v05_tenant_lock_grant"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("REVOKE UPDATE (decision_revision) ON tenant_state FROM recovery_app")
    op.execute("""CREATE FUNCTION public.lock_current_tenant_revision() RETURNS bigint LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$ DECLARE tenant text; revision bigint; BEGIN tenant := nullif(current_setting('app.current_org_id', true), ''); IF tenant IS NULL THEN RAISE EXCEPTION 'tenant context is required'; END IF; SELECT decision_revision INTO revision FROM public.tenant_state WHERE org_id = tenant FOR UPDATE; IF NOT FOUND THEN RAISE EXCEPTION 'tenant state is missing'; END IF; RETURN revision; END; $$""")
    op.execute("REVOKE ALL ON FUNCTION public.lock_current_tenant_revision() FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION public.lock_current_tenant_revision() TO recovery_app, recovery_worker")


def downgrade() -> None:
    op.execute("REVOKE EXECUTE ON FUNCTION public.lock_current_tenant_revision() FROM recovery_app, recovery_worker")
    op.execute("DROP FUNCTION public.lock_current_tenant_revision()")
    op.execute("GRANT UPDATE (decision_revision) ON tenant_state TO recovery_app")
