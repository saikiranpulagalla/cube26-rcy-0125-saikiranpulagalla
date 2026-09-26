from __future__ import annotations

import csv
import io
from hashlib import sha256
from pathlib import Path

from sqlalchemy.orm import Session, sessionmaker

from recovery_manager.config import Principal, Settings
from recovery_manager.db import set_local_tenant
from recovery_manager.ingestion import AcceptanceResult, accept_input

# SHA-256 values are for exact organizer fixture files at the Astra-approved baseline.
KNOWN_FIXTURE_HASHES = frozenset(
    {
        "668259b4857f9d015de4e02c15052a2c84ece4647e7cb7ad0cbd451fbcbc4d9e",
        "a1116ae437d4d674f43f71d0d033a44bace1661ee9eeada93e28c9e165d559ab",
        "5e3a148185a4bcb4ba376012610236775ce6519bae07ae8e25107d7288c822b2",
        "4baa00223e1c9c9f5393729ebce8b4cd99298e1bfb21feadcbfe52037d9689dc",
        "0cca916d25c9db57495420e4c02cebd1ac298de9fa3f9f148fe3e9289e9b3103",
    }
)


def load_known_fixture(
    factory: sessionmaker[Session], settings: Settings, path: Path
) -> list[AcceptanceResult]:
    if not settings.demo_fixtures_enabled:
        raise PermissionError(
            "Fixture loading is disabled; set RECOVERY_DEMO_FIXTURES_ENABLED=true"
        )
    raw_file = path.read_bytes()
    file_hash = sha256(raw_file).hexdigest()
    if file_hash not in KNOWN_FIXTURE_HASHES:
        raise PermissionError("Fixture hash is not allowlisted")
    decoded = raw_file.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(decoded, newline=""))
    if reader.fieldnames is None or "org_id" not in reader.fieldnames:
        raise ValueError("Known fixture does not have an org_id column")
    allowed_orgs = {principal.org_id for principal in settings.principals().values()}
    results: list[AcceptanceResult] = []
    for row_number, row in enumerate(reader, start=2):
        declared_org = (row.get("org_id") or "").strip()
        if declared_org not in allowed_orgs:
            raise PermissionError("Fixture row declares an unknown tenant")
        row_buffer = io.StringIO(newline="")
        writer = csv.DictWriter(row_buffer, fieldnames=reader.fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerow(row)
        row_bytes = row_buffer.getvalue().encode("utf-8")
        principal = Principal(org_id=declared_org, actor_id="fixture_loader", role="fixture_admin")
        with factory() as session, session.begin():
            set_local_tenant(session, declared_org)
            result = accept_input(
                session,
                principal,
                row_bytes,
                "text/csv",
                str(path),
                f"fixture:{file_hash}:{row_number}",
                settings,
                fixture_provenance={
                    "fixture": True,
                    "original_file_sha256": file_hash,
                    "original_row_number": row_number,
                    "declared_org_id": declared_org,
                },
            )
            results.append(result)
    return results
