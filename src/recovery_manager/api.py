from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from html import escape
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from recovery_manager.assessment import current_assessment
from recovery_manager.auth import authenticate_development_credential
from recovery_manager.capabilities import V01_CAPABILITIES
from recovery_manager.config import Principal, Settings, get_settings
from recovery_manager.db import (
    assert_runtime_ready,
    make_engine,
    make_session_factory,
    set_local_tenant,
)
from recovery_manager.ingestion import IdempotencyConflict, accept_input
from recovery_manager.models import (
    ClaimPursuit,
    EconomicObligation,
    PursuitAllocation,
    RawEnvelope,
    RecoveryAssessment,
    SettlementAllocation,
    SyntheticPacketReservation,
    TenantState,
    WorkIntent,
)


def create_app(
    settings: Settings | None = None, factory: sessionmaker[Session] | None = None
) -> FastAPI:
    current_settings = settings or get_settings()
    engine = None if factory is not None else make_engine(current_settings)
    current_factory = factory or make_session_factory(engine)  # type: ignore[arg-type]
    bound_engine = engine or current_factory.kw.get("bind")
    if not isinstance(bound_engine, Engine):
        raise RuntimeError("Application session factory must be bound to an Engine")

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        assert_runtime_ready(bound_engine, current_settings, required_role="recovery_app")
        yield

    app = FastAPI(title="Recovery Manager v0.1", lifespan=lifespan)
    app.state.settings = current_settings
    app.state.session_factory = current_factory

    def principal_dependency(
        request: Request,
    ) -> Principal:
        credentials = request.headers.getlist("x-development-credential")
        if len(credentials) != 1:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credential")
        return authenticate_development_credential(credentials[0], current_settings)

    @app.get("/healthz")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": "v0.1"}

    @app.get("/readyz")
    def readiness() -> dict[str, object]:
        try:
            assert_runtime_ready(bound_engine, current_settings, required_role="recovery_app")
            return {"status": "ready", "capabilities": V01_CAPABILITIES}
        except (SQLAlchemyError, RuntimeError, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Service is not ready"
            ) from exc

    @app.post("/v1/imports", status_code=status.HTTP_202_ACCEPTED)
    async def ingest(
        request: Request,
        response: Response,
        principal: Principal = Depends(principal_dependency),
    ) -> dict[str, object]:
        idempotency_values = request.headers.getlist("idempotency-key")
        if len(idempotency_values) != 1:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Idempotency-Key is required")
        idempotency_key = idempotency_values[0]
        source_values = request.headers.getlist("x-source-name")
        if len(source_values) > 1:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid source header")
        source_name = source_values[0] if source_values else "http-input"
        if idempotency_key is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Idempotency-Key is required"
            )
        declared_length = request.headers.get("content-length")
        if declared_length is not None:
            try:
                if int(declared_length) > current_settings.max_input_bytes:
                    raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Input too large")
            except ValueError as exc:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid Content-Length") from exc
        parts: list[bytes] = []
        total = 0
        async for chunk in request.stream():
            total += len(chunk)
            if total > current_settings.max_input_bytes:
                raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Input too large")
            parts.append(chunk)
        raw = b"".join(parts)
        content_type = request.headers.get("content-type", "application/octet-stream")
        try:
            with current_factory() as session, session.begin():
                set_local_tenant(session, principal.org_id)
                result = accept_input(
                    session,
                    principal,
                    raw,
                    content_type,
                    source_name,
                    idempotency_key,
                    current_settings,
                )
            if result.replayed:
                response.status_code = status.HTTP_200_OK
            return {
                "envelope_id": str(result.envelope_id),
                "work_id": str(result.work_id),
                "replayed": result.replayed,
                "validation_status": result.validation_status,
                "quarantine_reason": result.quarantine_reason,
            }
        except IdempotencyConflict as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        except SQLAlchemyError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Database unavailable"
            ) from exc

    @app.get("/v1/imports/{envelope_id}")
    def import_status(
        envelope_id: UUID, principal: Principal = Depends(principal_dependency)
    ) -> dict[str, object]:
        with current_factory() as session, session.begin():
            set_local_tenant(session, principal.org_id)
            envelope = session.execute(
                select(RawEnvelope).where(
                    RawEnvelope.org_id == principal.org_id, RawEnvelope.id == envelope_id
                )
            ).scalar_one_or_none()
            if envelope is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail="Import not found"
                )
            work = session.execute(
                select(WorkIntent).where(
                    WorkIntent.org_id == principal.org_id, WorkIntent.envelope_id == envelope.id
                )
            ).scalar_one()
            revision = session.execute(
                select(TenantState.decision_revision).where(TenantState.org_id == principal.org_id)
            ).scalar_one()
            return {
                "envelope_id": str(envelope.id),
                "work_id": str(work.id),
                "state": work.state,
                "validation_status": envelope.validation_status,
                "quarantine_reason": envelope.quarantine_reason,
                "tenant_revision": revision,
            }

    @app.get("/v1/imports/{envelope_id}/raw")
    def raw_envelope(
        envelope_id: UUID, principal: Principal = Depends(principal_dependency)
    ) -> Response:
        with current_factory() as session, session.begin():
            set_local_tenant(session, principal.org_id)
            envelope = session.execute(
                select(RawEnvelope).where(
                    RawEnvelope.org_id == principal.org_id, RawEnvelope.id == envelope_id
                )
            ).scalar_one_or_none()
            if envelope is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail="Import not found"
                )
            return Response(content=envelope.raw_bytes, media_type=envelope.content_type)

    @app.get("/review/assessments/{assessment_id}", response_class=HTMLResponse)
    def review_assessment(
        assessment_id: UUID, principal: Principal = Depends(principal_dependency)
    ) -> HTMLResponse:
        """Minimal read-only judge view; no human action changes a machine result."""
        with current_factory() as session, session.begin():
            set_local_tenant(session, principal.org_id)
            assessment = session.execute(
                select(RecoveryAssessment).where(
                    RecoveryAssessment.org_id == principal.org_id,
                    RecoveryAssessment.id == assessment_id,
                )
            ).scalar_one_or_none()
            if assessment is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Assessment not found")
            obligation = session.execute(
                select(EconomicObligation).where(
                    EconomicObligation.org_id == principal.org_id,
                    EconomicObligation.id == assessment.obligation_id,
                )
            ).scalar_one()
            current = current_assessment(session, principal.org_id, obligation.id)
            state = current.state if current is not None and current.assessment.id == assessment.id else "HISTORICAL"
            packet = session.execute(
                select(SyntheticPacketReservation).where(
                    SyntheticPacketReservation.org_id == principal.org_id,
                    SyntheticPacketReservation.assessment_id == assessment.id,
                )
            ).scalar_one_or_none()
            pursuits = session.execute(
                select(ClaimPursuit.status)
                .join(PursuitAllocation, PursuitAllocation.pursuit_id == ClaimPursuit.id)
                .where(
                    ClaimPursuit.org_id == principal.org_id,
                    PursuitAllocation.org_id == principal.org_id,
                    PursuitAllocation.obligation_id == obligation.id,
                )
            ).scalars().all()
            settlements = session.execute(
                select(SettlementAllocation.allocated_minor).where(
                    SettlementAllocation.org_id == principal.org_id,
                    SettlementAllocation.obligation_id == obligation.id,
                )
            ).scalars().all()
        amount = (
            "—"
            if assessment.recoverable_minor is None
            else f"{assessment.currency} {assessment.recoverable_minor // 100}.{assessment.recoverable_minor % 100:02d}"
        )
        snapshot = assessment.dependency_snapshot
        derivation = snapshot.get("amount_derivation")
        policy = snapshot.get("policy")
        evidence = snapshot.get("evidence")
        reconciliation = snapshot.get("reconciliation")
        authority = snapshot.get("trusted_fixture_profile")
        derivation_text = "historical dependency unavailable" if not derivation else str(derivation)
        policy_text = "historical dependency unavailable" if not policy else str(policy)
        evidence_text = "historical dependency unavailable" if not evidence else str(evidence)
        reconciliation_text = "historical dependency unavailable" if not reconciliation else str(reconciliation)
        capability = (
            "Synthetic mechanics only; not operational policy validation."
            if authority
            else "Historical authority unavailable."
        )
        current_text = state if state != "HISTORICAL" else (
            f"HISTORICAL; current assessment is {current.assessment.id}" if current is not None else "HISTORICAL"
        )
        body = f"""<!doctype html><title>Recovery review</title><main>
<h1>Recovery assessment</h1><section><h2>Historical assessment proof</h2>
<p><strong>{escape(assessment.conclusion)}</strong> · {escape(amount)} · assessed at {escape(str(snapshot.get('assessment_as_of', 'unavailable')))}</p>
<dl><dt>Capability</dt><dd>{escape(capability)}</dd>
<dt>Historical obligation</dt><dd>{escape(str(snapshot.get('obligation', 'historical dependency unavailable')))}</dd>
<dt>Pinned amount derivation</dt><dd>{escape(derivation_text)}</dd>
<dt>Pinned policy</dt><dd>{escape(policy_text)}</dd>
<dt>Pinned evidence</dt><dd>{escape(evidence_text)}</dd>
<dt>Pinned reconciliation</dt><dd>{escape(reconciliation_text)}</dd></dl></section>
<section><h2>Current status</h2><dl><dt>Actionability</dt><dd>{escape(current_text)}</dd>
<dt>Relevant pursuit states</dt><dd>{escape(', '.join(pursuits) or 'none')}</dd>
<dt>Relevant settlements</dt><dd>{escape(', '.join(str(item) for item in settlements) or 'none')}</dd>
<dt>Packet</dt><dd>{'exported historical packet' if packet else 'not exported'}</dd>
</dl></section><p>Machine assessment is immutable. This view provides no force-claim action.</p></main>"""
        return HTMLResponse(body)

    return app


app = create_app()
