"""Enforce aggregate conservation for obligations sharing one financial event.

Revision ID: 0021_opportunity_conservation
Revises: 0020_current_pointer_integrity
"""

from alembic import op

revision = "0021_opportunity_conservation"
down_revision = "0020_current_pointer_integrity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION public.lock_economic_opportunity(p_obligation_id uuid) RETURNS uuid
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, public AS $$
        DECLARE tenant text; opportunity_event uuid;
        BEGIN
          tenant := nullif(btrim(current_setting('app.current_org_id', true)), '');
          IF tenant IS NULL THEN RAISE EXCEPTION 'tenant context is required'; END IF;
          SELECT financial_event_id INTO opportunity_event
          FROM public.economic_obligation
          WHERE org_id = tenant AND id = p_obligation_id;
          IF NOT FOUND THEN RAISE EXCEPTION 'unknown tenant-qualified obligation'; END IF;
          IF opportunity_event IS NULL THEN RETURN NULL; END IF;
          PERFORM 1 FROM public.financial_event
          WHERE org_id = tenant AND id = opportunity_event FOR UPDATE;
          IF NOT FOUND THEN RAISE EXCEPTION 'economic opportunity financial event is missing'; END IF;
          RETURN opportunity_event;
        END;
        $$
        """
    )
    op.execute("ALTER FUNCTION public.lock_economic_opportunity(uuid) OWNER TO recovery_owner")
    op.execute("REVOKE ALL ON FUNCTION public.lock_economic_opportunity(uuid) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION public.lock_economic_opportunity(uuid) TO recovery_app")
    op.execute("GRANT EXECUTE ON FUNCTION public.lock_economic_opportunity(uuid) TO recovery_worker")
    op.execute(
        "CREATE INDEX ix_obligation_opportunity_event "
        "ON economic_obligation (org_id, financial_event_id) WHERE financial_event_id IS NOT NULL"
    )
    op.execute("DROP TRIGGER pursuit_allocation_guard ON pursuit_allocation")
    op.execute("DROP FUNCTION guard_pursuit_allocation()")
    op.execute(
        """
        CREATE FUNCTION guard_pursuit_allocation() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, public AS $$
        DECLARE declared bigint; allocated bigint; pursuit_currency text; obligation_currency text;
        DECLARE opportunity_event uuid; entitlement bigint; net_settlement bigint; active_pursuit bigint;
        BEGIN
          PERFORM public.lock_current_tenant_revision();
          opportunity_event := public.lock_economic_opportunity(NEW.obligation_id);
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
          IF opportunity_event IS NULL THEN RETURN NEW; END IF;
          SELECT CASE WHEN COUNT(*) = 0 OR BOOL_OR(latest.justified_entitlement_minor IS NULL)
                      THEN NULL ELSE MAX(latest.justified_entitlement_minor) END
          INTO entitlement
          FROM public.economic_obligation obligation
          LEFT JOIN LATERAL (
            SELECT justified_entitlement_minor FROM public.amount_derivation
            WHERE org_id = NEW.org_id AND obligation_id = obligation.id
            ORDER BY derivation_version DESC LIMIT 1
          ) latest ON TRUE
          WHERE obligation.org_id = NEW.org_id AND obligation.financial_event_id = opportunity_event;
          IF entitlement IS NULL THEN RAISE EXCEPTION 'economic opportunity entitlement is unknown'; END IF;
          SELECT COALESCE(SUM(allocation.allocated_minor - COALESCE((
            SELECT SUM(reversal.reversed_minor) FROM public.settlement_reversal reversal
            WHERE reversal.org_id = NEW.org_id AND reversal.allocation_id = allocation.id
          ), 0)), 0)
          INTO net_settlement
          FROM public.settlement_allocation allocation
          JOIN public.economic_obligation obligation ON obligation.org_id = allocation.org_id
            AND obligation.id = allocation.obligation_id
          WHERE allocation.org_id = NEW.org_id AND obligation.financial_event_id = opportunity_event;
          SELECT COALESCE(SUM(allocation.allocated_minor), 0) INTO active_pursuit
          FROM public.pursuit_allocation allocation
          JOIN public.claim_pursuit pursuit ON pursuit.org_id = allocation.org_id
            AND pursuit.id = allocation.pursuit_id
          JOIN public.economic_obligation obligation ON obligation.org_id = allocation.org_id
            AND obligation.id = allocation.obligation_id
          WHERE allocation.org_id = NEW.org_id AND obligation.financial_event_id = opportunity_event
            AND pursuit.status IN ('RECOMMENDED', 'EXPORTED', 'SUBMITTED', 'PENDING');
          IF net_settlement + active_pursuit + NEW.allocated_minor > entitlement THEN
            RAISE EXCEPTION 'opportunity pursuit allocation exceeds justified residual';
          END IF;
          RETURN NEW;
        END;
        $$
        """
    )
    op.execute("ALTER FUNCTION guard_pursuit_allocation() OWNER TO recovery_owner")
    op.execute(
        "CREATE TRIGGER pursuit_allocation_guard BEFORE INSERT ON pursuit_allocation "
        "FOR EACH ROW EXECUTE FUNCTION guard_pursuit_allocation()"
    )
    op.execute("DROP TRIGGER settlement_allocation_guard ON settlement_allocation")
    op.execute("DROP FUNCTION guard_settlement_allocation()")
    op.execute(
        """
        CREATE FUNCTION guard_settlement_allocation() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, public AS $$
        DECLARE available bigint; allocated bigint; event_direction text; event_currency text; obligation_currency text;
        DECLARE opportunity_event uuid; entitlement bigint; net_settlement bigint; active_pursuit bigint;
        BEGIN
          PERFORM public.lock_current_tenant_revision();
          opportunity_event := public.lock_economic_opportunity(NEW.obligation_id);
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
          IF opportunity_event IS NULL THEN RETURN NEW; END IF;
          SELECT CASE WHEN COUNT(*) = 0 OR BOOL_OR(latest.justified_entitlement_minor IS NULL)
                      THEN NULL ELSE MAX(latest.justified_entitlement_minor) END
          INTO entitlement
          FROM public.economic_obligation obligation
          LEFT JOIN LATERAL (
            SELECT justified_entitlement_minor FROM public.amount_derivation
            WHERE org_id = NEW.org_id AND obligation_id = obligation.id
            ORDER BY derivation_version DESC LIMIT 1
          ) latest ON TRUE
          WHERE obligation.org_id = NEW.org_id AND obligation.financial_event_id = opportunity_event;
          IF entitlement IS NULL THEN RAISE EXCEPTION 'economic opportunity entitlement is unknown'; END IF;
          SELECT COALESCE(SUM(allocation.allocated_minor - COALESCE((
            SELECT SUM(reversal.reversed_minor) FROM public.settlement_reversal reversal
            WHERE reversal.org_id = NEW.org_id AND reversal.allocation_id = allocation.id
          ), 0)), 0)
          INTO net_settlement
          FROM public.settlement_allocation allocation
          JOIN public.economic_obligation obligation ON obligation.org_id = allocation.org_id
            AND obligation.id = allocation.obligation_id
          WHERE allocation.org_id = NEW.org_id AND obligation.financial_event_id = opportunity_event;
          SELECT COALESCE(SUM(allocation.allocated_minor), 0) INTO active_pursuit
          FROM public.pursuit_allocation allocation
          JOIN public.claim_pursuit pursuit ON pursuit.org_id = allocation.org_id
            AND pursuit.id = allocation.pursuit_id
          JOIN public.economic_obligation obligation ON obligation.org_id = allocation.org_id
            AND obligation.id = allocation.obligation_id
          WHERE allocation.org_id = NEW.org_id AND obligation.financial_event_id = opportunity_event
            AND pursuit.status IN ('RECOMMENDED', 'EXPORTED', 'SUBMITTED', 'PENDING');
          IF net_settlement + NEW.allocated_minor + active_pursuit > entitlement THEN
            RAISE EXCEPTION 'opportunity settlement allocation exceeds justified residual';
          END IF;
          RETURN NEW;
        END;
        $$
        """
    )
    op.execute("ALTER FUNCTION guard_settlement_allocation() OWNER TO recovery_owner")
    op.execute(
        "CREATE TRIGGER settlement_allocation_guard BEFORE INSERT ON settlement_allocation "
        "FOR EACH ROW EXECUTE FUNCTION guard_settlement_allocation()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER settlement_allocation_guard ON settlement_allocation")
    op.execute("DROP FUNCTION guard_settlement_allocation()")
    op.execute("DROP TRIGGER pursuit_allocation_guard ON pursuit_allocation")
    op.execute("DROP FUNCTION guard_pursuit_allocation()")
    op.execute("DROP FUNCTION public.lock_economic_opportunity(uuid)")
    op.execute("DROP INDEX ix_obligation_opportunity_event")
