"""v0.2 deterministic, policy-free canonical source adapters."""

from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from pathlib import Path
from typing import Literal

from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from recovery_manager.config import Settings
from recovery_manager.db import set_local_tenant
from recovery_manager.fixtures import KNOWN_FIXTURE_HASHES, load_known_fixture
from recovery_manager.models import EvidenceRecord, FinancialEvent, SourceRecordVersion

SourceKind = Literal["fee_report", "receiving", "prep", "pack", "returns"]

SOURCE_KIND_BY_FILENAME: dict[str, SourceKind] = {
    "fee_report_sample.csv": "fee_report",
    "receiving_sample.csv": "receiving",
    "prep_sample.csv": "prep",
    "pack_sample.csv": "pack",
    "returns_sample.csv": "returns",
}

EXPECTED_FIXTURE_COUNTS = {
    "fee_report": 61,
    "receiving": 100,
    "prep": 62,
    "pack": 29,
    "returns": 24,
}


class CanonicalizationError(ValueError):
    pass


@dataclass(frozen=True)
class CanonicalSource:
    source_kind: SourceKind
    source_record_id: str
    org_id: str
    row_number: int
    payload: dict[str, str]
    content_sha256: str
    source_observed_at: datetime | None
    observed_precision: str | None
    financial: dict[str, object] | None
    evidence: dict[str, object] | None


def parse_money_minor(value: str) -> int:
    """Parse fixed-point USD safely; no locale, rounding, exponent, or coercion."""
    if not isinstance(value, str) or not value or value.strip() != value:
        raise CanonicalizationError("money must be a non-empty, unpadded decimal string")
    if re.fullmatch(r"-?\d+(?:\.\d{1,2})?", value) is None:
        raise CanonicalizationError("money must use fixed-point decimal notation")
    try:
        money = Decimal(value)
    except InvalidOperation as exc:
        raise CanonicalizationError("money is not decimal") from exc
    if not money.is_finite():
        raise CanonicalizationError("money has unsupported precision")
    exponent = money.as_tuple().exponent
    if not isinstance(exponent, int) or exponent < -2:
        raise CanonicalizationError("money has unsupported precision")
    if money != money.quantize(Decimal("0.01")):
        raise CanonicalizationError("money requires exact cents")
    return int(money * 100)


def parse_nonnegative_quantity(value: str) -> Decimal:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise CanonicalizationError("quantity must be a non-empty, unpadded decimal string")
    if re.fullmatch(r"\d+(?:\.\d+)?", value) is None:
        raise CanonicalizationError("quantity must use non-negative fixed-point decimal notation")
    try:
        quantity = Decimal(value)
    except InvalidOperation as exc:
        raise CanonicalizationError("quantity is not decimal") from exc
    if not quantity.is_finite() or quantity.is_signed():
        raise CanonicalizationError("quantity must be finite and non-negative")
    exponent = quantity.as_tuple().exponent
    if not isinstance(exponent, int) or exponent < -6:
        raise CanonicalizationError("quantity has unsupported precision")
    return quantity


def parse_time(value: str, *, date_only: bool) -> tuple[datetime, str]:
    if not value or value.strip() != value:
        raise CanonicalizationError("time must be non-empty and unpadded")
    try:
        if date_only:
            return datetime.fromisoformat(value).replace(tzinfo=UTC), "DATE"
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CanonicalizationError("time is not ISO-8601") from exc
    if parsed.tzinfo is None:
        raise CanonicalizationError("timestamp requires timezone")
    return parsed.astimezone(UTC), "INSTANT"


def _required(row: dict[str, str], field: str) -> str:
    value = row.get(field)
    if value is None or not value.strip():
        raise CanonicalizationError(f"missing required field: {field}")
    return value.strip()


def _hash_payload(payload: dict[str, str]) -> str:
    return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _references(row: dict[str, str]) -> dict[str, str]:
    return {
        name: value.strip()
        for name in ("unit_id", "sku", "fnsku", "asin", "fba_shipment_id", "order_id", "work_order_id", "po_number")
        if (value := row.get(name)) and value.strip()
    }


def _financial(row: dict[str, str]) -> dict[str, object]:
    report_type = _required(row, "report_type")
    amount_minor = parse_money_minor(_required(row, "amount_usd"))
    direction_map = {
        "fee_report": "DEBIT",
        "reimbursement_report": "CREDIT",
        "inventory_adjustment": "ADJUSTMENT",
    }
    if report_type not in direction_map:
        raise CanonicalizationError(f"unsupported financial report type: {report_type}")
    direction = direction_map[report_type]
    if amount_minor < 0 and direction in {"DEBIT", "CREDIT"}:
        direction = "CREDIT" if direction == "DEBIT" else "DEBIT"
    if amount_minor == 0:
        direction = "ADJUSTMENT"
    posting_time, precision = parse_time(_required(row, "posted_date"), date_only=True)
    return {
        "event_type": _required(row, "charge_type"),
        "direction": direction,
        "amount_minor": abs(amount_minor),
        "currency": "USD",
        "quantity": parse_nonnegative_quantity(_required(row, "quantity")),
        "posting_time": posting_time,
        "posting_time_precision": precision,
        "business_references": _references(row),
        "normalized_fields": {"report_type": report_type, "source_amount_minor_signed": amount_minor},
    }


def _evidence(kind: SourceKind, row: dict[str, str]) -> dict[str, object]:
    observed, precision = parse_time(_required(row, "captured_at"), date_only=False)
    coverage: Decimal | None = None
    if kind == "receiving":
        coverage = parse_nonnegative_quantity(_required(row, "qty_received"))
    return {
        "evidence_kind": kind,
        "observed_time": observed,
        "observed_time_precision": precision,
        "coverage_quantity": coverage,
        "coverage_scope": _references(row),
        "normalized_fields": dict(row),
    }


def parse_fixture(path: Path) -> list[CanonicalSource]:
    kind = SOURCE_KIND_BY_FILENAME.get(path.name)
    if kind is None:
        raise CanonicalizationError("unsupported fixture filename")
    raw = path.read_bytes()
    if sha256(raw).hexdigest() not in KNOWN_FIXTURE_HASHES:
        raise CanonicalizationError("fixture bytes are not allowlisted")
    try:
        decoded = raw.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(decoded, newline=""), strict=True)
        rows = list(reader)
    except (UnicodeDecodeError, csv.Error) as exc:
        raise CanonicalizationError("fixture CSV is malformed") from exc
    if reader.fieldnames is None:
        raise CanonicalizationError("fixture CSV has no headers")
    expected = EXPECTED_FIXTURE_COUNTS[kind]
    if len(rows) != expected:
        raise CanonicalizationError(f"fixture accounting mismatch for {kind}: expected {expected}")
    id_field = "line_id" if kind == "fee_report" else "record_id"
    result: list[CanonicalSource] = []
    for row_number, raw_row in enumerate(rows, start=2):
        if None in raw_row or any(value is None for value in raw_row.values()):
            raise CanonicalizationError("fixture row width mismatch")
        row = {key: value for key, value in raw_row.items() if key is not None and value is not None}
        org_id = _required(row, "org_id")
        record_id = _required(row, id_field)
        observed: datetime | None = None
        precision: str | None = None
        if kind == "fee_report":
            financial = _financial(row)
            observed = financial["posting_time"]  # type: ignore[assignment]
            precision = financial["posting_time_precision"]  # type: ignore[assignment]
            evidence = None
        else:
            evidence = _evidence(kind, row)
            observed = evidence["observed_time"]  # type: ignore[assignment]
            precision = evidence["observed_time_precision"]  # type: ignore[assignment]
            financial = None
        result.append(
            CanonicalSource(
                source_kind=kind,
                source_record_id=record_id,
                org_id=org_id,
                row_number=row_number,
                payload=row,
                content_sha256=_hash_payload(row),
                source_observed_at=observed,
                observed_precision=precision,
                financial=financial,
                evidence=evidence,
            )
        )
    return result


def canonicalize_fixture(factory: sessionmaker[Session], settings: Settings, path: Path) -> int:
    """Load an allowlisted fixture then persist immutable canonical records by tenant."""
    sources = parse_fixture(path)
    accepted = load_known_fixture(factory, settings, path)
    if len(sources) != len(accepted):
        raise RuntimeError("fixture acceptance and canonicalization row accounting diverged")
    inserted = 0
    for source, acceptance in zip(sources, accepted, strict=True):
        with factory() as session, session.begin():
            set_local_tenant(session, source.org_id)
            existing = session.execute(
                select(SourceRecordVersion).where(
                    SourceRecordVersion.org_id == source.org_id,
                    SourceRecordVersion.source_kind == source.source_kind,
                    SourceRecordVersion.source_record_id == source.source_record_id,
                    SourceRecordVersion.content_sha256 == source.content_sha256,
                )
            ).scalar_one_or_none()
            if existing is not None:
                continue
            version = SourceRecordVersion(
                org_id=source.org_id,
                source_kind=source.source_kind,
                source_record_id=source.source_record_id,
                content_sha256=source.content_sha256,
                raw_envelope_id=acceptance.envelope_id,
                row_number=source.row_number,
                declared_org_id=source.org_id,
                payload=source.payload,
                source_observed_at=source.source_observed_at,
            )
            session.add(version)
            session.flush()
            if source.financial is not None:
                session.add(FinancialEvent(org_id=source.org_id, source_record_version_id=version.id, **source.financial))
            if source.evidence is not None:
                session.add(EvidenceRecord(org_id=source.org_id, source_record_version_id=version.id, **source.evidence))
            session.execute(text("SELECT advance_tenant_revision(:org_id)"), {"org_id": source.org_id})
            inserted += 1
    return inserted
