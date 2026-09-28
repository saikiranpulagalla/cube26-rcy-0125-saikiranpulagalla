from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import DBAPIError

from recovery_manager.config import Principal
from recovery_manager.db import set_local_tenant
from recovery_manager.ingestion import accept_input
from recovery_manager.ledger import residual_for_obligation
from recovery_manager.models import (
    AmountDerivation,
    ClaimPursuit,
    EconomicObligation,
    FinancialEvent,
    PursuitAllocation,
    SettlementAllocation,
    SettlementReversal,
    SourceRecordVersion,
)


def _obligation(session, org_id: str, key: str, *, currency: str = "USD") -> EconomicObligation:
    obligation = EconomicObligation(
        org_id=org_id,
        economic_key=key,
        recovery_basis="UNKNOWN",
        currency=currency,
        business_instance={"fixture": "synthetic-v03"},
        quantity_scope={"coverage": "UNKNOWN"},
    )
    session.add(obligation)
    session.flush()
    return obligation


def _establish_tenant_state(session, settings, org_id: str) -> None:
    accept_input(
        session,
        Principal(org_id=org_id, actor_id="ledger-test", role="operator"),
        b"ledger fixture",
        "application/octet-stream",
        "ledger",
        f"ledger-{org_id}",
        settings,
    )


def _credit(session, org_id: str, key: str, amount_minor: int, *, currency: str = "USD") -> FinancialEvent:
    source = SourceRecordVersion(
        org_id=org_id,
        source_kind="test_credit",
        source_record_id=key,
        content_sha256=(key.encode().hex() * 64)[:64],
        declared_org_id=org_id,
        payload={"synthetic": True},
    )
    session.add(source)
    session.flush()
    credit = FinancialEvent(
        org_id=org_id,
        source_record_version_id=source.id,
        event_type="SYNTHETIC_CREDIT",
        direction="CREDIT",
        amount_minor=amount_minor,
        currency=currency,
        quantity=None,
        posting_time=None,
        posting_time_precision=None,
        incident_time=None,
        incident_time_precision=None,
        business_references={},
        normalized_fields={"synthetic": True},
    )
    session.add(credit)
    session.flush()
    return credit


def _debit(session, org_id: str, key: str, amount_minor: int, *, currency: str = "USD") -> FinancialEvent:
    source = SourceRecordVersion(
        org_id=org_id,
        source_kind="test_debit",
        source_record_id=key,
        content_sha256=(key.encode().hex() * 64)[:64],
        declared_org_id=org_id,
        payload={"synthetic": True},
    )
    session.add(source)
    session.flush()
    debit = FinancialEvent(
        org_id=org_id,
        source_record_version_id=source.id,
        event_type="SYNTHETIC_DEBIT",
        direction="DEBIT",
        amount_minor=amount_minor,
        currency=currency,
        quantity=1,
        posting_time=None,
        posting_time_precision=None,
        incident_time=None,
        incident_time_precision=None,
        business_references={"charge_id": key},
        normalized_fields={"synthetic": True},
    )
    session.add(debit)
    session.flush()
    return debit


def _derivation(session, org_id: str, obligation: EconomicObligation, entitlement: int | None) -> None:
    session.add(
        AmountDerivation(
            org_id=org_id,
            obligation_id=obligation.id,
            derivation_version=1,
            currency=obligation.currency,
            observed_amount_minor=1000,
            expected_amount_minor=800,
            justified_entitlement_minor=entitlement,
            rounding_rule="integer minor units",
            basis_class="SYNTHETIC_ONLY",
            source_basis={"fixture": "v03"},
        )
    )
    session.flush()


def test_same_debit_different_bases_can_duplicate_pursuit_before_opportunity_cap(
    runtime_factory, settings
) -> None:
    """Regression reproduction for A-006: one debit currently creates two pursuit pools."""
    org_id = "org_opportunity_reproduction"
    with runtime_factory() as session, session.begin():
        set_local_tenant(session, org_id)
        _establish_tenant_state(session, settings, org_id)
        debit = _debit(session, org_id, "one-charge-two-bases", 1000)
        obligations = []
        for key, basis in (("same-charge-invalid-fee", "INVALID_FEE"), ("same-charge-duplicate", "DUPLICATE_BILLING")):
            obligation = EconomicObligation(
                org_id=org_id,
                economic_key=key,
                financial_event_id=debit.id,
                recovery_basis=basis,
                currency="USD",
                business_instance={"charge_id": "one-charge-two-bases"},
                quantity_scope={"coverage": "KNOWN", "quantity": "1"},
            )
            session.add(obligation)
            session.flush()
            _derivation(session, org_id, obligation, 200)
            obligations.append(obligation)
        first, second = obligations
        pursuit = ClaimPursuit(
            org_id=org_id,
            external_reference=None,
            status="RECOMMENDED",
            currency="USD",
            declared_minor=200,
        )
        session.add(pursuit)
        session.flush()
        session.add(
            PursuitAllocation(
                org_id=org_id,
                pursuit_id=pursuit.id,
                obligation_id=first.id,
                allocated_minor=200,
            )
        )
        session.flush()
        duplicate_pursuit = ClaimPursuit(
            org_id=org_id,
            external_reference=None,
            status="RECOMMENDED",
            currency="USD",
            declared_minor=200,
        )
        session.add(duplicate_pursuit)
        session.flush()
        with pytest.raises(DBAPIError, match="opportunity pursuit allocation exceeds justified residual"):
            with session.begin_nested():
                session.add(
                    PursuitAllocation(
                        org_id=org_id,
                        pursuit_id=duplicate_pursuit.id,
                        obligation_id=second.id,
                        allocated_minor=200,
                    )
                )
                session.flush()
        total = session.execute(select(func.sum(PursuitAllocation.allocated_minor))).scalar_one()
        assert total == 200


def _pursuit_allocation(session, org_id: str, obligation: EconomicObligation, amount: int) -> None:
    pursuit = ClaimPursuit(org_id=org_id, external_reference=None, status="RECOMMENDED", currency="USD", declared_minor=amount)
    session.add(pursuit)
    session.flush()
    session.add(PursuitAllocation(org_id=org_id, pursuit_id=pursuit.id, obligation_id=obligation.id, allocated_minor=amount))
    session.flush()


def test_opportunity_partial_caps_settlement_and_independent_events(runtime_factory, settings) -> None:
    org_id = "org_opportunity_matrix"
    with runtime_factory() as session, session.begin():
        set_local_tenant(session, org_id)
        _establish_tenant_state(session, settings, org_id)
        debit_x, debit_y = _debit(session, org_id, "event-x", 1000), _debit(session, org_id, "event-y", 1000)
        x = EconomicObligation(org_id=org_id, economic_key="x", financial_event_id=debit_x.id, recovery_basis="INVALID_FEE", currency="USD", business_instance={}, quantity_scope={"coverage": "KNOWN"})
        y = EconomicObligation(org_id=org_id, economic_key="y", financial_event_id=debit_y.id, recovery_basis="INVALID_FEE", currency="USD", business_instance={}, quantity_scope={"coverage": "KNOWN"})
        session.add_all((x, y))
        session.flush()
        _derivation(session, org_id, x, 200)
        _derivation(session, org_id, y, 300)
        _pursuit_allocation(session, org_id, x, 100)
        _pursuit_allocation(session, org_id, x, 100)
        with pytest.raises(DBAPIError):
            with session.begin_nested():
                _pursuit_allocation(session, org_id, x, 1)
        _pursuit_allocation(session, org_id, y, 300)
        credit = _credit(session, org_id, "event-x-credit", 100)
        with pytest.raises(DBAPIError):
            with session.begin_nested():
                session.add(
                    SettlementAllocation(
                        org_id=org_id,
                        credit_event_id=credit.id,
                        obligation_id=x.id,
                        allocated_minor=100,
                        rationale="would exceed X",
                    )
                )
                session.flush()


def test_opportunity_exact_minor_unit_partial_pursuits_sum_to_ceiling(runtime_factory, settings) -> None:
    org_id = "org_opportunity_minor_units"
    with runtime_factory() as session, session.begin():
        set_local_tenant(session, org_id)
        _establish_tenant_state(session, settings, org_id)
        debit = _debit(session, org_id, "minor-unit-opportunity", 1000)
        obligation = EconomicObligation(
            org_id=org_id,
            economic_key="minor-unit-opportunity",
            financial_event_id=debit.id,
            recovery_basis="INVALID_FEE",
            currency="USD",
            business_instance={},
            quantity_scope={"coverage": "KNOWN"},
        )
        session.add(obligation)
        session.flush()
        _derivation(session, org_id, obligation, 200)
        for allocation in (75, 75, 50):
            _pursuit_allocation(session, org_id, obligation, allocation)
        assert session.execute(select(func.sum(PursuitAllocation.allocated_minor))).scalar_one() == 200
        with pytest.raises(DBAPIError, match="opportunity pursuit allocation exceeds justified residual"):
            with session.begin_nested():
                _pursuit_allocation(session, org_id, obligation, 1)


def test_opportunity_settlement_reversal_and_pursuit_share_one_capacity(runtime_factory, settings) -> None:
    """Known allocations share the exact-event ceiling; a reversal restores capacity."""
    org_id = "org_opportunity_settlement"
    with runtime_factory() as session, session.begin():
        set_local_tenant(session, org_id)
        _establish_tenant_state(session, settings, org_id)
        debit = _debit(session, org_id, "settlement-opportunity", 1000)
        obligation = EconomicObligation(
            org_id=org_id,
            economic_key="settlement-opportunity",
            financial_event_id=debit.id,
            recovery_basis="INVALID_FEE",
            currency="USD",
            business_instance={},
            quantity_scope={"coverage": "KNOWN"},
        )
        session.add(obligation)
        session.flush()
        _derivation(session, org_id, obligation, 200)
        credit = _credit(session, org_id, "settlement-opportunity-credit", 100)
        settlement = SettlementAllocation(
            org_id=org_id,
            credit_event_id=credit.id,
            obligation_id=obligation.id,
            allocated_minor=100,
            rationale="known settlement",
        )
        session.add(settlement)
        session.flush()
        _pursuit_allocation(session, org_id, obligation, 100)
        with pytest.raises(DBAPIError, match="opportunity pursuit allocation exceeds justified residual"):
            with session.begin_nested():
                _pursuit_allocation(session, org_id, obligation, 1)
        session.add(
            SettlementReversal(
                org_id=org_id,
                allocation_id=settlement.id,
                reversed_minor=100,
                reason="known reversal",
            )
        )
        session.flush()
        _pursuit_allocation(session, org_id, obligation, 100)
        with pytest.raises(DBAPIError, match="opportunity pursuit allocation exceeds justified residual"):
            with session.begin_nested():
                _pursuit_allocation(session, org_id, obligation, 1)


def test_terminal_pursuit_releases_only_its_active_opportunity_capacity(runtime_factory, settings) -> None:
    org_id = "org_opportunity_lifecycle"
    with runtime_factory() as session, session.begin():
        set_local_tenant(session, org_id)
        _establish_tenant_state(session, settings, org_id)
        debit = _debit(session, org_id, "lifecycle-opportunity", 1000)
        obligation = EconomicObligation(
            org_id=org_id,
            economic_key="lifecycle-opportunity",
            financial_event_id=debit.id,
            recovery_basis="INVALID_FEE",
            currency="USD",
            business_instance={},
            quantity_scope={"coverage": "KNOWN"},
        )
        session.add(obligation)
        session.flush()
        _derivation(session, org_id, obligation, 200)
        pursuit = ClaimPursuit(
            org_id=org_id,
            external_reference=None,
            status="RECOMMENDED",
            currency="USD",
            declared_minor=200,
        )
        session.add(pursuit)
        session.flush()
        session.add(
            PursuitAllocation(
                org_id=org_id,
                pursuit_id=pursuit.id,
                obligation_id=obligation.id,
                allocated_minor=200,
            )
        )
        session.flush()
        session.execute(update(ClaimPursuit).where(ClaimPursuit.id == pursuit.id).values(status="WITHDRAWN"))
        _pursuit_allocation(session, org_id, obligation, 200)


def test_entitlement_increase_allows_only_new_opportunity_capacity(runtime_factory, settings) -> None:
    org_id = "org_opportunity_entitlement_increase"
    with runtime_factory() as session, session.begin():
        set_local_tenant(session, org_id)
        _establish_tenant_state(session, settings, org_id)
        debit = _debit(session, org_id, "entitlement-increase", 1000)
        obligation = EconomicObligation(
            org_id=org_id,
            economic_key="entitlement-increase",
            financial_event_id=debit.id,
            recovery_basis="INVALID_FEE",
            currency="USD",
            business_instance={},
            quantity_scope={"coverage": "KNOWN"},
        )
        session.add(obligation)
        session.flush()
        _derivation(session, org_id, obligation, 200)
        _pursuit_allocation(session, org_id, obligation, 200)
        session.add(
            AmountDerivation(
                org_id=org_id,
                obligation_id=obligation.id,
                derivation_version=2,
                currency="USD",
                observed_amount_minor=1000,
                expected_amount_minor=700,
                justified_entitlement_minor=300,
                rounding_rule="integer minor units",
                basis_class="SYNTHETIC_ONLY",
                source_basis={"fixture": "v03-increase"},
            )
        )
        session.flush()
        _pursuit_allocation(session, org_id, obligation, 100)
        with pytest.raises(DBAPIError, match="opportunity pursuit allocation exceeds justified residual"):
            with session.begin_nested():
                _pursuit_allocation(session, org_id, obligation, 1)


def test_entitlement_decrease_below_active_reservation_is_explicit_conflict(runtime_factory, settings) -> None:
    org_id = "org_opportunity_entitlement_change"
    with runtime_factory() as session, session.begin():
        set_local_tenant(session, org_id)
        _establish_tenant_state(session, settings, org_id)
        debit = _debit(session, org_id, "entitlement-change", 1000)
        obligation = EconomicObligation(org_id=org_id, economic_key="entitlement-change", financial_event_id=debit.id, recovery_basis="INVALID_FEE", currency="USD", business_instance={}, quantity_scope={"coverage": "KNOWN"})
        session.add(obligation)
        session.flush()
        _derivation(session, org_id, obligation, 200)
        _pursuit_allocation(session, org_id, obligation, 200)
        session.add(AmountDerivation(org_id=org_id, obligation_id=obligation.id, derivation_version=2, currency="USD", observed_amount_minor=1000, expected_amount_minor=900, justified_entitlement_minor=100, rounding_rule="integer minor units", basis_class="SYNTHETIC_ONLY", source_basis={}))
        session.flush()
        result = residual_for_obligation(session, org_id, obligation.id)
        assert result.remaining_minor is None and result.unknown_reason == "PURSUIT_ALLOCATION_CONFLICT"


def test_concurrent_same_opportunity_full_pursuits_cannot_double_reserve(runtime_factory, settings) -> None:
    org_id = "org_opportunity_concurrent"
    with runtime_factory() as session, session.begin():
        set_local_tenant(session, org_id)
        _establish_tenant_state(session, settings, org_id)
        debit = _debit(session, org_id, "concurrent-opportunity", 1000)
        obligation = EconomicObligation(org_id=org_id, economic_key="concurrent-opportunity", financial_event_id=debit.id, recovery_basis="INVALID_FEE", currency="USD", business_instance={}, quantity_scope={"coverage": "KNOWN"})
        session.add(obligation)
        session.flush()
        _derivation(session, org_id, obligation, 200)
        obligation_id = obligation.id
        pursuits = []
        for _ in range(2):
            pursuit = ClaimPursuit(org_id=org_id, external_reference=None, status="RECOMMENDED", currency="USD", declared_minor=200)
            session.add(pursuit)
            session.flush()
            pursuits.append(pursuit.id)
    barrier = Barrier(2)

    def reserve(pursuit_id) -> bool:  # type: ignore[no-untyped-def]
        try:
            with runtime_factory() as session, session.begin():
                set_local_tenant(session, org_id)
                session.execute(text("SET LOCAL lock_timeout = '3s'"))
                barrier.wait(timeout=5)
                session.add(
                    PursuitAllocation(
                        org_id=org_id,
                        pursuit_id=pursuit_id,
                        obligation_id=obligation_id,
                        allocated_minor=200,
                    )
                )
                session.flush()
            return True
        except DBAPIError:
            return False

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(reserve, pursuits)) == [False, True]
    with runtime_factory() as session, session.begin():
        set_local_tenant(session, org_id)
        assert session.execute(select(func.sum(PursuitAllocation.allocated_minor))).scalar_one() == 200


@pytest.mark.parametrize(
    ("attempts", "expected", "reserved_total"),
    [((100, 100), [True, True], 200), ((150, 150), [False, True], 150)],
)
def test_concurrent_partial_pursuits_share_the_exact_event_cap(
    runtime_factory,
    settings,
    attempts: tuple[int, int],
    expected: list[bool],
    reserved_total: int,
) -> None:
    org_id = f"org_opportunity_partial_{attempts[0]}"
    with runtime_factory() as session, session.begin():
        set_local_tenant(session, org_id)
        _establish_tenant_state(session, settings, org_id)
        debit = _debit(session, org_id, "concurrent-partial", 1000)
        obligation = EconomicObligation(
            org_id=org_id,
            economic_key="concurrent-partial",
            financial_event_id=debit.id,
            recovery_basis="INVALID_FEE",
            currency="USD",
            business_instance={},
            quantity_scope={"coverage": "KNOWN"},
        )
        session.add(obligation)
        session.flush()
        _derivation(session, org_id, obligation, 200)
        obligation_id = obligation.id
        pursuits = []
        for amount in attempts:
            pursuit = ClaimPursuit(
                org_id=org_id,
                external_reference=None,
                status="RECOMMENDED",
                currency="USD",
                declared_minor=amount,
            )
            session.add(pursuit)
            session.flush()
            pursuits.append(pursuit.id)
    barrier = Barrier(2)

    def reserve(pursuit_id, amount: int) -> bool:  # type: ignore[no-untyped-def]
        try:
            with runtime_factory() as session, session.begin():
                set_local_tenant(session, org_id)
                session.execute(text("SET LOCAL lock_timeout = '3s'"))
                barrier.wait(timeout=5)
                session.add(
                    PursuitAllocation(
                        org_id=org_id,
                        pursuit_id=pursuit_id,
                        obligation_id=obligation_id,
                        allocated_minor=amount,
                    )
                )
                session.flush()
            return True
        except DBAPIError:
            return False

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(reserve, pursuits, attempts))
    assert sorted(results) == expected
    with runtime_factory() as session, session.begin():
        set_local_tenant(session, org_id)
        total = session.execute(select(func.sum(PursuitAllocation.allocated_minor))).scalar_one()
        assert total == reserved_total


def test_same_event_identifier_is_independent_across_tenants(runtime_factory, settings) -> None:
    for org_id in ("org_opportunity_alpha", "org_opportunity_bravo"):
        with runtime_factory() as session, session.begin():
            set_local_tenant(session, org_id)
            _establish_tenant_state(session, settings, org_id)
            debit = _debit(session, org_id, "shared-external-event", 1000)
            obligation = EconomicObligation(
                org_id=org_id,
                economic_key="shared-external-event",
                financial_event_id=debit.id,
                recovery_basis="INVALID_FEE",
                currency="USD",
                business_instance={},
                quantity_scope={"coverage": "KNOWN"},
            )
            session.add(obligation)
            session.flush()
            _derivation(session, org_id, obligation, 200)
            _pursuit_allocation(session, org_id, obligation, 200)


def test_residual_is_obligation_specific_and_unknown_is_not_zero(runtime_factory, settings) -> None:
    with runtime_factory() as session, session.begin():
        set_local_tenant(session, "org_demo_alpha")
        _establish_tenant_state(session, settings, "org_demo_alpha")
        known = _obligation(session, "org_demo_alpha", "known")
        _derivation(session, "org_demo_alpha", known, 200)
        unknown = _obligation(session, "org_demo_alpha", "unknown")
        assert residual_for_obligation(session, "org_demo_alpha", known.id).remaining_minor == 200
        result = residual_for_obligation(session, "org_demo_alpha", unknown.id)
        assert result.remaining_minor is None
        assert result.unknown_reason == "JUSTIFIED_ENTITLEMENT_UNKNOWN"


def test_settlement_reversal_and_active_pursuit_reduce_residual(runtime_factory, settings) -> None:
    with runtime_factory() as session, session.begin():
        set_local_tenant(session, "org_demo_alpha")
        _establish_tenant_state(session, settings, "org_demo_alpha")
        obligation = _obligation(session, "org_demo_alpha", "settled")
        _derivation(session, "org_demo_alpha", obligation, 5000)
        credit = _credit(session, "org_demo_alpha", "credit-a", 5000)
        allocation = SettlementAllocation(
            org_id="org_demo_alpha",
            credit_event_id=credit.id,
            obligation_id=obligation.id,
            allocated_minor=3000,
            rationale="synthetic allocation",
        )
        session.add(allocation)
        session.flush()
        pursuit = ClaimPursuit(
            org_id="org_demo_alpha",
            external_reference=None,
            status="RECOMMENDED",
            currency="USD",
            declared_minor=1000,
        )
        session.add(pursuit)
        session.flush()
        session.add(
            PursuitAllocation(
                org_id="org_demo_alpha",
                pursuit_id=pursuit.id,
                obligation_id=obligation.id,
                allocated_minor=1000,
            )
        )
        session.flush()
        before = residual_for_obligation(session, "org_demo_alpha", obligation.id)
        assert (before.allocated_settlement_minor, before.active_pursuit_minor, before.remaining_minor) == (3000, 1000, 1000)
        session.add(
            SettlementReversal(
                org_id="org_demo_alpha",
                allocation_id=allocation.id,
                reversed_minor=3000,
                reason="synthetic reversal",
            )
        )
        session.flush()
        after = residual_for_obligation(session, "org_demo_alpha", obligation.id)
        assert (after.allocated_settlement_minor, after.remaining_minor) == (0, 4000)


def test_over_settlement_and_over_pursuit_are_conflicts_not_zero_residual(runtime_factory, settings) -> None:
    with runtime_factory() as session, session.begin():
        set_local_tenant(session, "org_demo_alpha")
        _establish_tenant_state(session, settings, "org_demo_alpha")
        obligation = _obligation(session, "org_demo_alpha", "no-silent-clamp")
        _derivation(session, "org_demo_alpha", obligation, 200)
        credit = _credit(session, "org_demo_alpha", "credit-over", 300)
        session.add(
            SettlementAllocation(
                org_id="org_demo_alpha",
                credit_event_id=credit.id,
                obligation_id=obligation.id,
                allocated_minor=300,
                rationale="adversarial over-settlement",
            )
        )
        session.flush()
        result = residual_for_obligation(session, "org_demo_alpha", obligation.id)
        assert result.remaining_minor is None
        assert result.unknown_reason == "OVERSETTLED"


def test_allocation_limits_currency_and_pursuit_transitions_are_database_enforced(
    runtime_factory, settings
) -> None:
    with runtime_factory() as session, session.begin():
        set_local_tenant(session, "org_demo_alpha")
        _establish_tenant_state(session, settings, "org_demo_alpha")
        obligation = _obligation(session, "org_demo_alpha", "constraints")
        credit = _credit(session, "org_demo_alpha", "credit-b", 3000)
        session.add(
            SettlementAllocation(
                org_id="org_demo_alpha", credit_event_id=credit.id, obligation_id=obligation.id,
                allocated_minor=3000, rationale="all credit",
            )
        )
        session.flush()
        pursuit = ClaimPursuit(
            org_id="org_demo_alpha", external_reference=None, status="RECOMMENDED", currency="USD", declared_minor=100,
        )
        session.add(pursuit)
        session.flush()
        pursuit_id = pursuit.id
    with runtime_factory() as session, pytest.raises(DBAPIError), session.begin():
        set_local_tenant(session, "org_demo_alpha")
        session.add(
            SettlementAllocation(
                org_id="org_demo_alpha", credit_event_id=credit.id, obligation_id=obligation.id,
                allocated_minor=1, rationale="must fail",
            )
        )
        session.flush()
    with runtime_factory() as session, pytest.raises(DBAPIError), session.begin():
        set_local_tenant(session, "org_demo_alpha")
        session.execute(update(ClaimPursuit).where(ClaimPursuit.id == pursuit_id).values(status="SUBMITTED"))


def test_concurrent_credit_allocations_cannot_overallocate(runtime_factory, settings) -> None:
    with runtime_factory() as session, session.begin():
        set_local_tenant(session, "org_demo_alpha")
        _establish_tenant_state(session, settings, "org_demo_alpha")
        first = _obligation(session, "org_demo_alpha", "concurrent-first")
        second = _obligation(session, "org_demo_alpha", "concurrent-second")
        credit = _credit(session, "org_demo_alpha", "credit-concurrent", 3000)
        ids = (first.id, second.id, credit.id)
    barrier = Barrier(2)

    def insert(obligation_id) -> bool:  # type: ignore[no-untyped-def]
        try:
            with runtime_factory() as session, session.begin():
                set_local_tenant(session, "org_demo_alpha")
                session.add(
                    SettlementAllocation(
                        org_id="org_demo_alpha", credit_event_id=ids[2], obligation_id=obligation_id,
                        allocated_minor=2000, rationale="concurrent test",
                    )
                )
                barrier.wait(timeout=5)
                session.flush()
            return True
        except DBAPIError:
            return False

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(insert, ids[:2]))
    assert sorted(results) == [False, True]
    with runtime_factory() as session, session.begin():
        set_local_tenant(session, "org_demo_alpha")
        allocations = session.execute(select(SettlementAllocation.allocated_minor)).scalars().all()
        assert sum(allocations) == 2000
