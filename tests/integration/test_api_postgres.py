from __future__ import annotations

from fastapi.testclient import TestClient

from recovery_manager.api import create_app


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
