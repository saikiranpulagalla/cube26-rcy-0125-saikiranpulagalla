from recovery_manager.api import _historical_ledger_proof_text


def test_legacy_snapshot_does_not_fabricate_ledger_contributors() -> None:
    assert _historical_ledger_proof_text({"ledger": {"allocated_settlement_minor": 50}}) == (
        "legacy snapshot — contributor-level ledger provenance unavailable"
    )


def test_new_snapshot_renders_its_pinned_ledger_proof() -> None:
    proof = {"settlement": {"contributors": [{"allocation_id": "S1"}]}}
    assert _historical_ledger_proof_text({"ledger_proof": proof}) == str(proof)
