from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import select, update
from sqlalchemy.exc import DBAPIError

from recovery_manager.db import set_local_tenant
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


def test_residual_is_obligation_specific_and_unknown_is_not_zero(runtime_factory) -> None:
    with runtime_factory() as session, session.begin():
        set_local_tenant(session, "org_demo_alpha")
        known = _obligation(session, "org_demo_alpha", "known")
        _derivation(session, "org_demo_alpha", known, 200)
        unknown = _obligation(session, "org_demo_alpha", "unknown")
        assert residual_for_obligation(session, "org_demo_alpha", known.id).remaining_minor == 200
        result = residual_for_obligation(session, "org_demo_alpha", unknown.id)
        assert result.remaining_minor is None
        assert result.unknown_reason == "JUSTIFIED_ENTITLEMENT_UNKNOWN"


def test_settlement_reversal_and_active_pursuit_reduce_residual(runtime_factory) -> None:
    with runtime_factory() as session, session.begin():
        set_local_tenant(session, "org_demo_alpha")
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


def test_allocation_limits_currency_and_pursuit_transitions_are_database_enforced(runtime_factory) -> None:
    with runtime_factory() as session, session.begin():
        set_local_tenant(session, "org_demo_alpha")
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


def test_concurrent_credit_allocations_cannot_overallocate(runtime_factory) -> None:
    with runtime_factory() as session, session.begin():
        set_local_tenant(session, "org_demo_alpha")
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
