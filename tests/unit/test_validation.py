from __future__ import annotations

import pytest

from recovery_manager.capabilities import V01_CAPABILITIES, CapabilityState
from recovery_manager.validation import validate_input


def test_capabilities_are_all_disabled() -> None:
    assert set(V01_CAPABILITIES.values()) == {CapabilityState.DISABLED}


def test_valid_csv_with_matching_declared_tenant_is_accepted() -> None:
    result = validate_input(b"org_id,value\norg_demo_alpha,1\n", "text/csv", 1000, "org_demo_alpha")
    assert result.status == "ACCEPTED"
    assert result.declared_orgs == ("org_demo_alpha",)


@pytest.mark.parametrize(
    ("raw", "reason"),
    [
        (b"org_id,value\norg_demo_alpha,1,extra\n", "surplus columns"),
        (b"org_id,org_id\norg_demo_alpha,org_demo_alpha\n", "duplicate headers"),
        (b"\xff\xfe", "UTF-8"),
    ],
)
def test_malformed_csv_is_quarantined(raw: bytes, reason: str) -> None:
    result = validate_input(raw, "text/csv", 1000, "org_demo_alpha")
    assert result.status == "QUARANTINED"
    assert reason in (result.quarantine_reason or "")


def test_cross_tenant_or_mixed_csv_is_quarantined() -> None:
    result = validate_input(
        b"org_id,value\norg_demo_alpha,1\norg_demo_bravo,2\n", "text/csv", 1000, "org_demo_alpha"
    )
    assert result.status == "QUARANTINED"
    assert result.declared_orgs == ("org_demo_alpha", "org_demo_bravo")


@pytest.mark.parametrize("content_type", ["application/json", "text/plain"])
def test_unsupported_content_type_is_rejected(content_type: str) -> None:
    with pytest.raises(ValueError, match="Unsupported content type"):
        validate_input(b"x", content_type, 1000, "org_demo_alpha")


def test_empty_and_oversized_input_are_rejected() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        validate_input(b"", "application/octet-stream", 2, "org_demo_alpha")
    with pytest.raises(ValueError, match="maximum size"):
        validate_input(b"abc", "application/octet-stream", 2, "org_demo_alpha")
