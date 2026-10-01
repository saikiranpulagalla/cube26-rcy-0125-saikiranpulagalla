from __future__ import annotations

from collections.abc import Iterator
from uuid import uuid4

import pytest

from recovery_manager.evidence import prove_assertion
from recovery_manager.models import EvidenceAssertion, EvidenceRecord, SourceRecordVersion


class _Result:
    def __init__(self, value: object) -> None:
        self.value = value

    def scalar_one_or_none(self) -> object:
        return self.value


class _Session:
    def __init__(self, values: tuple[object, ...]) -> None:
        self._values: Iterator[object] = iter(values)

    def execute(self, _statement: object) -> _Result:
        return _Result(next(self._values))


def _proof(source_value: object, asserted_value: object) -> object:
    source_id, evidence_id, assertion_id = uuid4(), uuid4(), uuid4()
    source = SourceRecordVersion(
        id=source_id,
        org_id="org_json_values",
        source_kind="unit",
        source_record_id="source",
        content_sha256="a" * 64,
        declared_org_id="org_json_values",
        payload={"fee": {"valid": source_value}},
    )
    evidence = EvidenceRecord(
        id=evidence_id,
        org_id="org_json_values",
        source_record_version_id=source_id,
        evidence_kind="unit",
        observed_time=None,
        observed_time_precision=None,
        coverage_quantity=1,
        coverage_scope={"coverage": "KNOWN"},
        normalized_fields={},
    )
    assertion = EvidenceAssertion(
        id=assertion_id,
        org_id="org_json_values",
        evidence_record_id=evidence_id,
        source_record_version_id=source_id,
        proposition_key="fee-valid",
        subject_key="subject",
        polarity="SUPPORTS",
        fact_path="fee.valid",
        asserted_value=asserted_value,
        scope={"coverage": "KNOWN"},
        decisive=True,
    )
    return prove_assertion(_Session((evidence, source, None)), "org_json_values", assertion)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("source_value", "asserted_value", "supported"),
    (
        (1, True, False),
        (0, False, False),
        (True, True, True),
        (False, False, True),
        (1, 1, True),
        (1.0, 1, True),
        ("true", True, False),
        (None, False, False),
        ({"valid": 1}, {"valid": True}, False),
        ([1], [True], False),
    ),
)
def test_prove_assertion_uses_exact_json_value_types(
    source_value: object, asserted_value: object, supported: bool
) -> None:
    proof = _proof(source_value, asserted_value)
    assert proof.mechanically_supported is supported  # type: ignore[union-attr]
    if not supported:
        assert proof.reason == "FACT_VALUE_MISMATCH"  # type: ignore[union-attr]
