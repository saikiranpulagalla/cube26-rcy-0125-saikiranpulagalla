"""v0.3 ledger primitives. They calculate residuals but make no recovery decision."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from recovery_manager.models import (
    AmountDerivation,
    ClaimPursuit,
    FinancialEvent,
    PursuitAllocation,
    SettlementAllocation,
    SettlementReversal,
)

ACTIVE_PURSUIT_STATUSES = frozenset({"RECOMMENDED", "EXPORTED", "SUBMITTED", "PENDING"})


@dataclass(frozen=True)
class Residual:
    currency: str
    justified_entitlement_minor: int | None
    allocated_settlement_minor: int
    active_pursuit_minor: int
    remaining_minor: int | None
    unknown_reason: str | None


@dataclass(frozen=True)
class LedgerContributorProof:
    """Immutable, decision-time explanation of the residual ledger operands."""

    settlement: dict[str, object]
    pursuit: dict[str, object]
    consistent: bool


def ledger_contributor_proof(
    session: Session,
    org_id: str,
    obligation_id: UUID,
    currency: str,
    *,
    expected_settlement_minor: int,
    expected_active_pursuit_minor: int,
    reconciliation: Mapping[str, Mapping[str, object]],
) -> LedgerContributorProof:
    """Project the exact allocations used by ``residual_for_obligation``.

    This is deliberately a read-only companion to the residual calculation:
    it pins the allocation identities and decision-time lifecycle fields that
    explain the aggregate operands, without consulting unrelated tenant rows.
    """
    settlement_contributors: list[dict[str, object]] = []
    settlement_amounts: list[int] = []
    for settlement_allocation, credit_event in session.execute(
        select(SettlementAllocation, FinancialEvent)
        .join(FinancialEvent, FinancialEvent.id == SettlementAllocation.credit_event_id)
        .where(
            SettlementAllocation.org_id == org_id,
            SettlementAllocation.obligation_id == obligation_id,
            FinancialEvent.org_id == org_id,
        )
        .order_by(SettlementAllocation.id)
    ).tuples():
        reversals: list[dict[str, object]] = []
        reversed_amounts: list[int] = []
        for reversal in session.execute(
            select(SettlementReversal)
            .where(
                SettlementReversal.org_id == org_id,
                SettlementReversal.allocation_id == settlement_allocation.id,
            )
            .order_by(SettlementReversal.id)
        ).scalars():
            reversed_amounts.append(reversal.reversed_minor)
            reversals.append(
                {
                    "id": str(reversal.id),
                    "reversed_minor": reversal.reversed_minor,
                    "reason": reversal.reason,
                }
            )
        reversed_minor = sum(reversed_amounts)
        net_minor = settlement_allocation.allocated_minor - reversed_minor
        settlement_amounts.append(net_minor)
        settlement_contributors.append(
            {
                "allocation_id": str(settlement_allocation.id),
                "obligation_id": str(settlement_allocation.obligation_id),
                "credit_event_id": str(credit_event.id),
                "credit_source_record_version_id": str(credit_event.source_record_version_id),
                "allocated_minor": settlement_allocation.allocated_minor,
                "reversed_minor": reversed_minor,
                "net_minor": net_minor,
                "currency": credit_event.currency,
                "reversals": reversals,
            }
        )

    pursuit_contributors: list[dict[str, object]] = []
    pursuit_amounts: list[int] = []
    for pursuit_allocation, pursuit in session.execute(
        select(PursuitAllocation, ClaimPursuit)
        .join(ClaimPursuit, ClaimPursuit.id == PursuitAllocation.pursuit_id)
        .where(
            PursuitAllocation.org_id == org_id,
            PursuitAllocation.obligation_id == obligation_id,
            ClaimPursuit.org_id == org_id,
            ClaimPursuit.status.in_(ACTIVE_PURSUIT_STATUSES),
        )
        .order_by(PursuitAllocation.id)
    ).tuples():
        pursuit_amounts.append(pursuit_allocation.allocated_minor)
        pursuit_contributors.append(
            {
                "allocation_id": str(pursuit_allocation.id),
                "pursuit_id": str(pursuit.id),
                "obligation_id": str(pursuit_allocation.obligation_id),
                "allocated_minor": pursuit_allocation.allocated_minor,
                "currency": pursuit.currency,
                "pursuit_state": pursuit.status,
                "pursuit_declared_minor": pursuit.declared_minor,
            }
        )

    settlement_total = sum(settlement_amounts)
    pursuit_total = sum(pursuit_amounts)
    contributor_currencies = [
        item["currency"] for item in settlement_contributors + pursuit_contributors
    ]
    consistent = (
        settlement_total == expected_settlement_minor
        and pursuit_total == expected_active_pursuit_minor
        and all(isinstance(item_currency, str) and item_currency == currency for item_currency in contributor_currencies)
    )
    return LedgerContributorProof(
        settlement={
            "reconciliation": reconciliation.get("SETTLEMENT"),
            "net_minor": settlement_total,
            "currency": currency,
            "contributors": settlement_contributors,
        },
        pursuit={
            "reconciliation": reconciliation.get("PURSUIT"),
            "active_minor": pursuit_total,
            "currency": currency,
            "contributors": pursuit_contributors,
        },
        consistent=consistent,
    )


def residual_for_obligation(session: Session, org_id: str, obligation_id: UUID) -> Residual:
    derivation = session.execute(
        select(AmountDerivation)
        .where(AmountDerivation.org_id == org_id, AmountDerivation.obligation_id == obligation_id)
        .order_by(AmountDerivation.derivation_version.desc())
        .limit(1)
    ).scalar_one_or_none()
    if derivation is None or derivation.justified_entitlement_minor is None:
        currency = derivation.currency if derivation is not None else "UNK"
        return Residual(currency, None, 0, 0, None, "JUSTIFIED_ENTITLEMENT_UNKNOWN")
    allocated = int(
        session.execute(
            select(func.coalesce(func.sum(SettlementAllocation.allocated_minor), 0)).where(
                SettlementAllocation.org_id == org_id,
                SettlementAllocation.obligation_id == obligation_id,
            )
        ).scalar_one()
    )
    reversed_minor = int(
        session.execute(
            select(func.coalesce(func.sum(SettlementReversal.reversed_minor), 0))
            .join(SettlementAllocation, SettlementReversal.allocation_id == SettlementAllocation.id)
            .where(
                SettlementReversal.org_id == org_id,
                SettlementAllocation.org_id == org_id,
                SettlementAllocation.obligation_id == obligation_id,
            )
        ).scalar_one()
    )
    active_pursuit = int(
        session.execute(
            select(func.coalesce(func.sum(PursuitAllocation.allocated_minor), 0))
            .join(ClaimPursuit, PursuitAllocation.pursuit_id == ClaimPursuit.id)
            .where(
                PursuitAllocation.org_id == org_id,
                PursuitAllocation.obligation_id == obligation_id,
                ClaimPursuit.org_id == org_id,
                ClaimPursuit.status.in_(ACTIVE_PURSUIT_STATUSES),
            )
        ).scalar_one()
    )
    net_settlement = allocated - reversed_minor
    if net_settlement < 0:
        return Residual(
            derivation.currency,
            derivation.justified_entitlement_minor,
            net_settlement,
            active_pursuit,
            None,
            "SETTLEMENT_REVERSAL_CONFLICT",
        )
    if net_settlement > derivation.justified_entitlement_minor:
        return Residual(
            derivation.currency,
            derivation.justified_entitlement_minor,
            net_settlement,
            active_pursuit,
            None,
            "OVERSETTLED",
        )
    available_after_settlement = derivation.justified_entitlement_minor - net_settlement
    if active_pursuit > available_after_settlement:
        return Residual(
            derivation.currency,
            derivation.justified_entitlement_minor,
            net_settlement,
            active_pursuit,
            None,
            "PURSUIT_ALLOCATION_CONFLICT",
        )
    remaining = available_after_settlement - active_pursuit
    return Residual(
        derivation.currency,
        derivation.justified_entitlement_minor,
        net_settlement,
        active_pursuit,
        remaining,
        None,
    )
