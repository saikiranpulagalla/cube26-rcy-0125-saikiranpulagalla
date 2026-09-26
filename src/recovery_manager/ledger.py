"""v0.3 ledger primitives. They calculate residuals but make no recovery decision."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from recovery_manager.models import (
    AmountDerivation,
    ClaimPursuit,
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
    remaining = derivation.justified_entitlement_minor - net_settlement - active_pursuit
    return Residual(
        derivation.currency,
        derivation.justified_entitlement_minor,
        net_settlement,
        active_pursuit,
        max(remaining, 0),
        None,
    )
