from datetime import date
from decimal import Decimal

import pytest

from recovery_manager.recovery_contract import (
    Charge,
    ContractEvidence,
    InternalDecisionFacts,
    RecoveryContractError,
    RecoveryRule,
    Reimbursement,
    net_reimbursement,
    project_contract_verdict,
)


def charge(**changes: object) -> Charge:
    raw: dict[str, object] = {
        "charge_id": "charge-1", "charge_type": "inbound_defect", "charge_subtype": "unbagged_unit",
        "charged_at": "2026-01-10", "granularity": "unit", "shipment_id": None, "amazon_order_id": "O-1",
        "sku": "SKU-1", "fnsku": "F-1", "asin": "A-1", "quantity": 1, "currency": "USD",
        "amount_per_unit": "2.00", "amount_total": "2.00", "description": "unbagged unit",
    }
    raw.update(changes)
    return Charge.from_mapping(raw)


def credit(**changes: object) -> Reimbursement:
    raw: dict[str, object] = {
        "reimbursement_id": "credit-1", "case_id": None, "approval_date": "2026-01-11",
        "amazon_order_id": "O-1", "sku": "SKU-1", "fnsku": "F-1", "asin": "A-1", "reason": "credit",
        "condition": None, "currency": "USD", "amount_per_unit": "2.00", "amount_total": "2.00",
        "quantity_reimbursed_cash": 1, "quantity_reimbursed_inventory": 0,
        "original_reimbursement_id": None,
    }
    raw.update(changes)
    return Reimbursement.from_mapping(raw)


def evidence(verdict: str = "pass", **changes: object) -> ContractEvidence:
    values: dict[str, object] = {
        "manager": "prep", "check_key": "polybag_present", "verdict": verdict,
        "observed_at": date(2026, 1, 9), "granularity": "unit", "attributed_charge_id": "charge-1",
    }
    values.update(changes)
    return ContractEvidence(**values)  # type: ignore[arg-type]


def decision(
    current: Charge | None = None,
    credits: tuple[Reimbursement, ...] = (),
    records: tuple[ContractEvidence, ...] = (),
    rule: RecoveryRule = RecoveryRule(True, 30),
    facts: InternalDecisionFacts = InternalDecisionFacts(True, True, Decimal("2.00"), Decimal("0")),
) -> str:
    return project_contract_verdict(
        current or charge(), credits, records, rule, facts, date(2026, 1, 20)
    ).verdict


def test_supported_and_explicit_failure_contract_verdicts() -> None:
    assert decision(records=(evidence(),)) == "contested"
    assert decision(records=(evidence("fail"),)) == "accepted"


def test_missing_uncertain_and_wrong_granularity_fail_closed() -> None:
    assert decision() == "insufficient_evidence"
    assert decision(records=(evidence("uncertain"),)) == "insufficient_evidence"
    shipment = charge(granularity="shipment", shipment_id="S-1")
    assert decision(shipment, records=(evidence(),)) == "insufficient_evidence"


def test_shipment_exact_identity_and_out_of_window() -> None:
    shipment = charge(granularity="shipment", shipment_id="S-1")
    record = evidence(granularity="shipment", shipment_id="S-1", attributed_charge_id=None)
    assert decision(shipment, records=(record,)) == "contested"
    assert decision(shipment, records=(record,), rule=RecoveryRule(True, 1)) == "out_of_window"


@pytest.mark.parametrize(
    ("charge_type", "subtype", "manager", "key"),
    [
        ("Inbound defect", "missing suffocation warning", "prep", "suffocation_warning_present"),
        ("Inbound defect", "unscannable barcode", "prep", "fnsku_label_flat"),
        ("Inbound defect", "manufacturer barcode visible", "prep", "manufacturer_barcode_covered"),
        ("Unplanned prep", "labelling", "prep", "fnsku_label_placement_valid"),
        ("Unplanned prep", "bagging", "prep", "polybag_present"),
        ("Warehouse lost", "lost", "receiving", "quantity_matches_po"),
        ("Mis-ship", "anything", "pack", "all_items_present"),
    ],
)
def test_explicit_contract_charge_mappings(
    charge_type: str, subtype: str, manager: str, key: str
) -> None:
    mapped = charge(charge_type=charge_type, charge_subtype=subtype)
    record = evidence(manager=manager, check_key=key)
    if (charge_type, subtype) == ("Inbound defect", "missing suffocation warning"):
        record = (
            record,
            evidence(manager="prep", check_key="suffocation_warning_legible"),
        )
        assert decision(mapped, records=record) == "contested"
    elif (charge_type, subtype) == ("Inbound defect", "unscannable barcode"):
        record = (
            record,
            evidence(manager="prep", check_key="fnsku_label_placement_valid"),
        )
        assert decision(mapped, records=record) == "contested"
    elif (charge_type, subtype) == ("Unplanned prep", "bagging"):
        record = (record, evidence(manager="prep", check_key="polybag_sealed"))
        assert decision(mapped, records=record) == "contested"
    elif (charge_type, subtype) == ("Mis-ship", "anything"):
        record = (record, evidence(manager="pack", check_key="quantities_correct"))
        assert decision(mapped, records=record) == "contested"
    else:
        assert decision(mapped, records=(record,)) == "contested"


def test_net_reimbursement_handles_null_case_reversal_and_currency() -> None:
    base = credit()
    reversal = credit(
        reimbursement_id="credit-2", amount_total="-2.00", amount_per_unit="0",
        original_reimbursement_id="credit-1",
    )
    assert base.case_id is None
    assert net_reimbursement(charge(), (base, reversal)) == Decimal("0")
    assert decision(credits=(base,)) == "already_reimbursed"
    assert decision(credits=(credit(currency="EUR"),), records=(evidence(),)) == "contested"


def test_active_pursuit_residual_is_consumed_without_a_second_ledger() -> None:
    result = project_contract_verdict(
        charge(), (), (evidence(),), RecoveryRule(True, 30),
        InternalDecisionFacts(True, True, Decimal("1.00"), Decimal("1.00")), date(2026, 1, 20),
    )
    assert result.verdict == "contested"
    assert result.remaining_amount == Decimal("1.00")


def test_invalid_inputs_and_ambiguous_adjustment_reject() -> None:
    with pytest.raises(RecoveryContractError):
        Charge.from_mapping({"charge_id": "only"})
    with pytest.raises(RecoveryContractError):
        net_reimbursement(charge(), (credit(original_reimbursement_id="missing"),))


def test_missing_rule_authority_and_noncurrent_facts_fail_closed() -> None:
    assert decision(records=(evidence(),), rule=RecoveryRule(False, None)) == "insufficient_evidence"
    assert decision(
        records=(evidence(),), facts=InternalDecisionFacts(True, False, Decimal("2"), Decimal("0"))
    ) == "insufficient_evidence"


def test_registry_rejects_unknown_check_key() -> None:
    with pytest.raises(RecoveryContractError):
        evidence(check_key="invented_check")
