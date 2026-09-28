from __future__ import annotations

from fastapi.testclient import TestClient
from test_assessment_gate_postgres import _assess_synthetic_candidate

from recovery_manager.api import create_app
from recovery_manager.assessment import assess_synthetic
from recovery_manager.db import set_local_tenant
from recovery_manager.models import ClaimPursuit


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


def test_review_page_is_tenant_scoped_and_marks_stale(runtime_factory, worker_factory, settings) -> None:
    with runtime_factory() as session, session.begin():
        obligation_id = _assess_synthetic_candidate(
            session, settings, org_id="org_demo_alpha"
        )
    with worker_factory() as session, session.begin():
        set_local_tenant(session, "org_demo_alpha")
        assessment = assess_synthetic(
            session,
            "org_demo_alpha",
            obligation_id,
            "SYN-FEE-001",
            "synthetic-invalid-fee",
            synthetic_capability_enabled=True,
        )
        assessment_id = assessment.id
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
