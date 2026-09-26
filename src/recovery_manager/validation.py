from __future__ import annotations

import csv
import io
from dataclasses import dataclass

SUPPORTED_CONTENT_TYPES = frozenset({"text/csv", "application/octet-stream"})


@dataclass(frozen=True)
class StructuralValidation:
    input_format: str
    status: str
    quarantine_reason: str | None
    declared_orgs: tuple[str, ...]


def validate_input(
    raw: bytes, content_type: str, max_bytes: int, authenticated_org_id: str
) -> StructuralValidation:
    if not raw:
        raise ValueError("Input body must not be empty")
    if len(raw) > max_bytes:
        raise ValueError(f"Input exceeds maximum size of {max_bytes} bytes")
    bare_content_type = content_type.split(";", 1)[0].strip().lower()
    if bare_content_type not in SUPPORTED_CONTENT_TYPES:
        raise ValueError(f"Unsupported content type: {bare_content_type}")
    if bare_content_type == "application/octet-stream":
        return StructuralValidation("BINARY", "ACCEPTED", None, ())
    try:
        decoded = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return StructuralValidation("CSV", "QUARANTINED", "CSV must be UTF-8", ())
    try:
        reader = csv.reader(io.StringIO(decoded, newline=""), strict=True)
        headers = next(reader, None)
        if headers is None or any(not field or not field.strip() for field in headers):
            return StructuralValidation("CSV", "QUARANTINED", "CSV requires non-empty headers", ())
        if len(headers) != len(set(headers)):
            return StructuralValidation("CSV", "QUARANTINED", "CSV has duplicate headers", ())
        org_index = headers.index("org_id") if "org_id" in headers else None
        declared_orgs: set[str] = set()
        for row in reader:
            if len(row) != len(headers):
                reason = "CSV has surplus columns" if len(row) > len(headers) else "CSV row length does not match headers"
                return StructuralValidation(
                    "CSV", "QUARANTINED", reason, tuple(sorted(declared_orgs))
                )
            if org_index is not None:
                declared = row[org_index].strip()
                if not declared:
                    return StructuralValidation("CSV", "QUARANTINED", "CSV has an empty org_id", ())
                declared_orgs.add(declared)
    except csv.Error:
        return StructuralValidation("CSV", "QUARANTINED", "CSV is structurally malformed", ())
    if declared_orgs and declared_orgs != {authenticated_org_id}:
        return StructuralValidation(
            "CSV",
            "QUARANTINED",
            "Source-declared organization does not match authenticated tenant",
            tuple(sorted(declared_orgs)),
        )
    return StructuralValidation("CSV", "ACCEPTED", None, tuple(sorted(declared_orgs)))
