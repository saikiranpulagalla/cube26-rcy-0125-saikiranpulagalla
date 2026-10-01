"""Fail-closed compatibility boundary for the supplied Recovery data contract.

This module deliberately does not replace the assessment engine or its ledger.
It validates normalized contract inputs and projects already-established
decision facts into the contract's smaller verdict vocabulary.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Literal, cast

Granularity = Literal["unit", "shipment", "order"]
EvidenceVerdict = Literal["pass", "fail", "uncertain"]
ContractVerdict = Literal[
    "contested", "accepted", "insufficient_evidence", "already_reimbursed", "out_of_window"
]


class RecoveryContractError(ValueError):
    """A contract input is malformed or too ambiguous to evaluate safely."""


CHECK_KEY_REGISTRY: dict[str, frozenset[str]] = {
    "receiving": frozenset(
        {"identity_matches_po", "quantity_matches_po", "carton_undamaged", "unit_undamaged",
         "variant_correct"}
    ),
    "prep": frozenset(
        {"polybag_present", "polybag_sealed", "suffocation_warning_present",
         "suffocation_warning_legible", "fnsku_label_flat", "fnsku_label_placement_valid",
         "manufacturer_barcode_covered", "expiry_date_legible", "handling_marks_present"}
    ),
    "pack": frozenset(
        {"all_items_present", "quantities_correct", "no_extra_items", "order_matches_manifest"}
    ),
    "returns": frozenset(
        {"identity_matches_order", "completeness_verified", "condition_grade",
         "disposition_assigned"}
    ),
}

_CHARGE_FIELDS = frozenset(
    {"charge_id", "charge_type", "charge_subtype", "charged_at", "granularity", "shipment_id",
     "amazon_order_id", "sku", "fnsku", "asin", "quantity", "currency", "amount_per_unit",
     "amount_total", "description"}
)
_REIMBURSEMENT_FIELDS = frozenset(
    {"reimbursement_id", "case_id", "approval_date", "amazon_order_id", "sku", "fnsku", "asin",
     "reason", "condition", "currency", "amount_per_unit", "amount_total",
     "quantity_reimbursed_cash", "quantity_reimbursed_inventory", "original_reimbursement_id"}
)
_MAPPINGS: dict[tuple[str, str], frozenset[str]] = {
    ("inbound_defect", "unbagged_unit"): frozenset({"polybag_present"}),
    ("inbound_defect", "missing_suffocation_warning"): frozenset(
        {"suffocation_warning_present", "suffocation_warning_legible"}
    ),
    ("inbound_defect", "unscannable_barcode"): frozenset(
        {"fnsku_label_flat", "fnsku_label_placement_valid"}
    ),
    ("inbound_defect", "manufacturer_barcode_visible"): frozenset(
        {"manufacturer_barcode_covered"}
    ),
    ("unplanned_prep", "labelling"): frozenset({"fnsku_label_placement_valid"}),
    ("unplanned_prep", "bagging"): frozenset({"polybag_present", "polybag_sealed"}),
    ("warehouse_lost", "lost"): frozenset({"quantity_matches_po"}),
    ("mis_ship", "mis_ship"): frozenset({"all_items_present", "quantities_correct"}),
}


def _required(mapping: Mapping[str, Any], fields: frozenset[str], name: str) -> None:
    unknown = set(mapping) - fields
    missing = {field for field in fields if field not in mapping and field not in {"case_id", "condition",
               "original_reimbursement_id", "shipment_id", "amazon_order_id", "fnsku", "asin",
               "amount_per_unit"}}
    if unknown or missing:
        raise RecoveryContractError(
            f"{name} fields invalid: unknown={sorted(unknown)}, missing={sorted(missing)}"
        )


def _text(value: object, field: str, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str) or not value.strip():
        raise RecoveryContractError(f"{field} must be a non-empty string")
    return value


def _currency(value: object) -> str:
    if not isinstance(value, str) or len(value) != 3 or not value.isascii() or not value.isupper():
        raise RecoveryContractError("currency must match [A-Z]{3}")
    return value


def _money(value: object, field: str, *, signed: bool = False) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, str | int | Decimal):
        raise RecoveryContractError(f"{field} must be a decimal string or integer")
    try:
        result = Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise RecoveryContractError(f"{field} is not a decimal") from exc
    if not result.is_finite() or (not signed and result < 0):
        requirement = "signed decimal" if signed else "non-negative decimal"
        raise RecoveryContractError(f"{field} must be {requirement}")
    return result


def _date(value: object, field: str) -> date:
    if not isinstance(value, str):
        raise RecoveryContractError(f"{field} must be an ISO date")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise RecoveryContractError(f"{field} must be an ISO date") from exc


@dataclass(frozen=True)
class Charge:
    charge_id: str
    charge_type: str
    charge_subtype: str
    charged_at: date
    granularity: Granularity
    shipment_id: str | None
    amazon_order_id: str | None
    sku: str
    fnsku: str | None
    asin: str | None
    quantity: int
    currency: str
    amount_per_unit: Decimal | None
    amount_total: Decimal
    description: str

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> Charge:
        _required(raw, _CHARGE_FIELDS, "charge")
        granularity = raw["granularity"]
        if granularity not in {"unit", "shipment", "order"}:
            raise RecoveryContractError("charge granularity is invalid")
        quantity = raw["quantity"]
        if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity <= 0:
            raise RecoveryContractError("charge quantity must be a positive integer")
        per_unit = raw["amount_per_unit"]
        return cls(
            charge_id=cast(str, _text(raw["charge_id"], "charge_id")),
            charge_type=cast(str, _text(raw["charge_type"], "charge_type")),
            charge_subtype=cast(str, _text(raw["charge_subtype"], "charge_subtype")),
            charged_at=_date(raw["charged_at"], "charged_at"),
            granularity=cast(Granularity, granularity),
            shipment_id=_text(raw["shipment_id"], "shipment_id", True),
            amazon_order_id=_text(raw["amazon_order_id"], "amazon_order_id", True),
            sku=cast(str, _text(raw["sku"], "sku")),
            fnsku=_text(raw["fnsku"], "fnsku", True),
            asin=_text(raw["asin"], "asin", True),
            quantity=quantity,
            currency=_currency(raw["currency"]),
            amount_per_unit=None if per_unit is None else _money(per_unit, "amount_per_unit"),
            amount_total=_money(raw["amount_total"], "amount_total"),
            description=cast(str, _text(raw["description"], "description")),
        )


@dataclass(frozen=True)
class Reimbursement:
    reimbursement_id: str
    case_id: str | None
    approval_date: date
    amazon_order_id: str | None
    sku: str
    fnsku: str | None
    asin: str | None
    reason: str
    condition: str | None
    currency: str
    amount_per_unit: Decimal
    amount_total: Decimal
    quantity_reimbursed_cash: int
    quantity_reimbursed_inventory: int
    original_reimbursement_id: str | None

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> Reimbursement:
        _required(raw, _REIMBURSEMENT_FIELDS, "reimbursement")
        quantities = ("quantity_reimbursed_cash", "quantity_reimbursed_inventory")
        if any(not isinstance(raw[name], int) or isinstance(raw[name], bool) or raw[name] < 0 for name in quantities):
            raise RecoveryContractError("reimbursement quantities must be non-negative integers")
        return cls(
            reimbursement_id=cast(str, _text(raw["reimbursement_id"], "reimbursement_id")),
            case_id=_text(raw.get("case_id"), "case_id", True),
            approval_date=_date(raw["approval_date"], "approval_date"),
            amazon_order_id=_text(raw.get("amazon_order_id"), "amazon_order_id", True),
            sku=cast(str, _text(raw["sku"], "sku")),
            fnsku=_text(raw.get("fnsku"), "fnsku", True),
            asin=_text(raw.get("asin"), "asin", True),
            reason=cast(str, _text(raw["reason"], "reason")),
            condition=_text(raw.get("condition"), "condition", True),
            currency=_currency(raw["currency"]),
            amount_per_unit=_money(raw["amount_per_unit"], "amount_per_unit"),
            amount_total=_money(raw["amount_total"], "amount_total", signed=True),
            quantity_reimbursed_cash=raw["quantity_reimbursed_cash"],
            quantity_reimbursed_inventory=raw["quantity_reimbursed_inventory"],
            original_reimbursement_id=_text(
                raw.get("original_reimbursement_id"), "original_reimbursement_id", True
            ),
        )


@dataclass(frozen=True)
class ContractEvidence:
    manager: str
    check_key: str
    verdict: EvidenceVerdict
    observed_at: date
    granularity: Granularity
    shipment_id: str | None = None
    amazon_order_id: str | None = None
    attributed_charge_id: str | None = None

    def __post_init__(self) -> None:
        if self.manager not in CHECK_KEY_REGISTRY or self.check_key not in CHECK_KEY_REGISTRY[self.manager]:
            raise RecoveryContractError("evidence check_key is not in the supplied registry")
        if self.verdict not in {"pass", "fail", "uncertain"}:
            raise RecoveryContractError("evidence verdict is invalid")
        if self.granularity not in {"unit", "shipment", "order"}:
            raise RecoveryContractError("evidence granularity is invalid")


@dataclass(frozen=True)
class RecoveryRule:
    authority_available: bool
    claim_window_days: int | None

    def __post_init__(self) -> None:
        if self.claim_window_days is not None and self.claim_window_days < 0:
            raise RecoveryContractError("claim window must be non-negative")


@dataclass(frozen=True)
class InternalDecisionFacts:
    """Facts exported by the existing engine; they are not recomputed here."""

    economic_state_known: bool
    current: bool
    remaining_actionable: Decimal
    active_pursuit: Decimal


@dataclass(frozen=True)
class ContractDecision:
    verdict: ContractVerdict
    reason: str
    remaining_amount: Decimal | None
    currency: str | None


def _relevant(charge: Charge, evidence: ContractEvidence) -> bool:
    if evidence.observed_at > charge.charged_at:
        return False
    if charge.granularity == "shipment":
        return (
            evidence.granularity == "shipment"
            and charge.shipment_id is not None
            and evidence.shipment_id == charge.shipment_id
        )
    if charge.granularity == "order":
        return (
            evidence.granularity == "order"
            and charge.amazon_order_id is not None
            and evidence.amazon_order_id == charge.amazon_order_id
        )
    return evidence.granularity == "unit" and evidence.attributed_charge_id == charge.charge_id


def _same_subject(charge: Charge, reimbursement: Reimbursement) -> bool:
    pairs = (
        (charge.amazon_order_id, reimbursement.amazon_order_id),
        (charge.sku, reimbursement.sku),
        (charge.fnsku, reimbursement.fnsku),
        (charge.asin, reimbursement.asin),
    )
    shared = [(left, right) for left, right in pairs if left is not None and right is not None]
    return bool(shared) and all(left == right for left, right in shared)


def net_reimbursement(charge: Charge, credits: tuple[Reimbursement, ...]) -> Decimal:
    """Compute a lineage-aware, currency-exact credit total for one charge."""
    matching = [credit for credit in credits if credit.currency == charge.currency and _same_subject(charge, credit)]
    roots = {credit.reimbursement_id: credit for credit in matching if credit.original_reimbursement_id is None}
    total = Decimal("0")
    for credit in matching:
        if credit.original_reimbursement_id is None:
            total += credit.amount_total
        else:
            if credit.original_reimbursement_id not in roots or credit.amount_total >= 0:
                raise RecoveryContractError("reimbursement adjustment lineage is ambiguous")
            total += credit.amount_total
    return max(total, Decimal("0"))


def _checks(charge: Charge) -> frozenset[str] | None:
    charge_type = charge.charge_type.strip().lower().replace("-", "_").replace(" ", "_")
    subtype = charge.charge_subtype.strip().lower().replace("-", "_").replace(" ", "_")
    direct = _MAPPINGS.get((charge_type, subtype))
    if direct is not None:
        return direct
    if charge_type == "warehouse_lost":
        return _MAPPINGS[("warehouse_lost", "lost")]
    if charge_type == "mis_ship":
        return _MAPPINGS[("mis_ship", "mis_ship")]
    return None


def project_contract_verdict(
    charge: Charge,
    credits: tuple[Reimbursement, ...],
    evidence: tuple[ContractEvidence, ...],
    rule: RecoveryRule,
    facts: InternalDecisionFacts,
    evaluated_on: date,
) -> ContractDecision:
    """Project valid contract inputs using existing decision facts without guessing policy."""
    reimbursed = net_reimbursement(charge, credits)
    if reimbursed >= charge.amount_total:
        return ContractDecision("already_reimbursed", "NET_REIMBURSEMENT_COVERS_CHARGE", Decimal("0"), charge.currency)
    if not facts.economic_state_known or not facts.current:
        return ContractDecision("insufficient_evidence", "INTERNAL_STATE_NOT_ACTIONABLE", None, None)
    required = _checks(charge)
    if required is None:
        return ContractDecision("insufficient_evidence", "NO_AUTHORITATIVE_CHARGE_MAPPING", None, None)
    relevant = tuple(item for item in evidence if _relevant(charge, item) and item.check_key in required)
    if any(item.verdict == "fail" for item in relevant):
        return ContractDecision("accepted", "RELEVANT_CHECK_FAILED", None, None)
    if {item.check_key for item in relevant if item.verdict == "pass"} != required:
        return ContractDecision("insufficient_evidence", "EVIDENCE_INCOMPLETE_OR_UNCERTAIN", None, None)
    if not rule.authority_available or rule.claim_window_days is None:
        return ContractDecision("insufficient_evidence", "RULE_WINDOW_UNAVAILABLE", None, None)
    if (evaluated_on - charge.charged_at).days > rule.claim_window_days:
        return ContractDecision("out_of_window", "FILING_WINDOW_CLOSED", None, None)
    remaining = min(charge.amount_total - reimbursed, facts.remaining_actionable)
    if remaining <= 0:
        return ContractDecision("already_reimbursed", "NO_ACTIONABLE_RESIDUAL", Decimal("0"), charge.currency)
    return ContractDecision("contested", "SUPPORTED_CURRENT_RESIDUAL", remaining, charge.currency)
