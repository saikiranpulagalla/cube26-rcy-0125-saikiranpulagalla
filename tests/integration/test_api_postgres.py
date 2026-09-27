from __future__ import annotations

import json
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select, text

from recovery_manager.api import create_app
from recovery_manager.db import set_local_tenant
from recovery_manager.ingestion import accept_input
from recovery_manager.models import (
    ClaimPursuit,
    EconomicObligation,
    TenantState,
)


def test_api_auth_authorization_raw_roundtrip_and_spoofing(
    runtime_factory, alpha, bravo, settings
) -> None:
    app = create_app(settings=settings, factory=runtime_factory)
    with TestClient(app) as client:
        assert client.get("/healthz").status_code == 200
        assert client.get("/readyz").status_code == 200
        assert client.post("/v1/imports", content=b"x").status_code == 401
        headers = {
            "X-Development-Credential": "alpha-local-token",
            "Idempotency-Key": "api-1",
            "content-type": "text/csv",
        }
        raw = b"org_id,value\norg_demo_bravo,1\n"
        response = client.post("/v1/imports", content=raw, headers=headers)
        assert response.status_code == 202
        payload = response.json()
        assert payload["validation_status"] == "QUARANTINED"
        assert (
            client.get(f"/v1/imports/{payload['envelope_id']}/raw", headers=headers).content == raw
        )
        bravo_headers = {"X-Development-Credential": "bravo-local-token"}
        assert (
            client.get(f"/v1/imports/{payload['envelope_id']}", headers=bravo_headers).status_code
            == 404
        )
        replay = client.post("/v1/imports", content=raw, headers=headers)
        assert replay.status_code == 200
        conflict = client.post("/v1/imports", content=b"different", headers=headers)
        assert conflict.status_code == 409


def test_review_page_is_tenant_scoped_and_marks_stale(runtime_factory, settings) -> None:
    with runtime_factory() as session, session.begin():
        set_local_tenant(session, "org_demo_alpha")
        accept_input(
            session,
            settings.principals()["alpha-local-token"],
            b"review-fixture",
            "application/octet-stream",
            "review",
            "review-fixture",
            settings,
        )
        obligation = EconomicObligation(
            org_id="org_demo_alpha",
            economic_key="review-alpha",
            recovery_basis="INVALID_FEE",
            currency="USD",
            business_instance={},
            quantity_scope={"coverage": "KNOWN"},
        )
        session.add(obligation)
        session.flush()
        revision = session.execute(
            select(TenantState.decision_revision).where(TenantState.org_id == "org_demo_alpha")
        ).scalar_one()
        assessment_id = uuid4()
        session.execute(
            text(
                "SELECT public.publish_recovery_assessment("
                ":assessment_id, :obligation_id, :revision, 'SYNTHETIC_CLAIM_READY', 200, 'USD', "
                "CAST(:snapshot AS jsonb))"
            ),
            {
                "assessment_id": assessment_id,
                "obligation_id": obligation.id,
                "revision": revision,
                "snapshot": json.dumps({"tenant_revision": revision}),
            },
        )
    app = create_app(settings=settings, factory=runtime_factory)
    headers = {"X-Development-Credential": "alpha-local-token"}
    with TestClient(app) as client:
        own = client.get(f"/review/assessments/{assessment_id}", headers=headers)
        assert own.status_code == 200
        assert "SYNTHETIC_ONLY" in own.text and "CURRENT" in own.text
        assert client.get(f"/review/assessments/{assessment_id}").status_code == 401
        assert (
            client.get(
                f"/review/assessments/{assessment_id}",
                headers={"X-Development-Credential": "bravo-local-token"},
            ).status_code
            == 404
        )
    with runtime_factory() as session, session.begin():
        set_local_tenant(session, "org_demo_alpha")
        session.add(
            ClaimPursuit(
                org_id="org_demo_alpha",
                external_reference=None,
                status="RECOMMENDED",
                currency="USD",
                declared_minor=1,
            )
        )
    with TestClient(app) as client:
        stale = client.get(f"/review/assessments/{assessment_id}", headers=headers)
        assert stale.status_code == 200
        assert "STALE" in stale.text
