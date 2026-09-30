from __future__ import annotations

from uuid import uuid4

from fastapi.testclient import TestClient
from test_assessment_gate_postgres import _assess_synthetic_candidate

from recovery_manager.api import create_app
from recovery_manager.assessment import assess_synthetic
from recovery_manager.db import set_local_tenant
from recovery_manager.models import AmountDerivation, ClaimPursuit


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
        assert "Synthetic mechanics only; not operational policy validation." in own.text
        assert "CURRENT" in own.text
        assert client.get(f"/review/assessments/{assessment_id}").status_code == 401
        assert (
            client.get(
                f"/review/assessments/{assessment_id}",
                headers={"X-Development-Credential": "bravo-local-token"},
            ).status_code
            == 404
        )
        assert (
            client.get(
                f"/review/assessments/{uuid4()}",
                headers=headers,
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


def test_review_page_uses_pinned_historical_proof_not_later_tenant_state(
    runtime_factory, worker_factory, settings
) -> None:
    org_id = "org_demo_alpha"
    with runtime_factory() as session, session.begin():
        obligation_id = _assess_synthetic_candidate(session, settings, org_id=org_id)
    with worker_factory() as session, session.begin():
        set_local_tenant(session, org_id)
        assessment = assess_synthetic(
            session, org_id, obligation_id, "SYN-FEE-001", "synthetic-invalid-fee",
            synthetic_capability_enabled=True,
        )
    with runtime_factory() as session, session.begin():
        set_local_tenant(session, org_id)
        d1 = session.query(AmountDerivation).filter_by(org_id=org_id, obligation_id=obligation_id).one()
        session.add(AmountDerivation(
            org_id=org_id, obligation_id=obligation_id, derivation_version=2, currency="USD",
            observed_amount_minor=1000, expected_amount_minor=700, justified_entitlement_minor=300,
            rounding_rule=d1.rounding_rule, basis_class=d1.basis_class, source_basis=d1.source_basis,
        ))
        session.add(ClaimPursuit(org_id=org_id, external_reference=None, status="PENDING", currency="USD", declared_minor=1))
    app = create_app(settings=settings, factory=runtime_factory)
    with TestClient(app) as client:
        response = client.get(
            f"/review/assessments/{assessment.id}",
            headers={"X-Development-Credential": "alpha-local-token"},
        )
    assert response.status_code == 200
    assert "Historical assessment proof" in response.text
    assert "Pinned amount derivation" in response.text
    assert "Historical ledger contributors" in response.text
    assert "&#x27;justified_entitlement_minor&#x27;: 200" in response.text
    assert "&#x27;justified_entitlement_minor&#x27;: 300" not in response.text
    snapshot = assessment.dependency_snapshot
    assert snapshot["policy"]["id"] in response.text
    assert snapshot["evidence"][0]["assertion_id"] in response.text
    assert snapshot["reconciliation"]["SETTLEMENT"]["id"] in response.text
    assert snapshot["reconciliation"]["PURSUIT"]["id"] in response.text
    assert snapshot["ledger_proof"]["settlement"]["reconciliation"]["id"] in response.text
    assert "PENDING" not in response.text
    assert "Current status" in response.text and "STALE" in response.text
