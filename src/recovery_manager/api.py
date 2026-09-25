from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response, status
from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from recovery_manager.auth import authenticate_development_credential
from recovery_manager.capabilities import V01_CAPABILITIES
from recovery_manager.config import Principal, Settings, get_settings
from recovery_manager.db import (
    assert_safe_runtime_role,
    make_engine,
    make_session_factory,
    set_local_tenant,
)
from recovery_manager.ingestion import IdempotencyConflict, accept_input
from recovery_manager.models import RawEnvelope, TenantState, WorkIntent


def create_app(
    settings: Settings | None = None, factory: sessionmaker[Session] | None = None
) -> FastAPI:
    current_settings = settings or get_settings()
    engine = None if factory is not None else make_engine(current_settings)
    current_factory = factory or make_session_factory(engine)  # type: ignore[arg-type]

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if engine is not None:
            assert_safe_runtime_role(engine)
        yield

    app = FastAPI(title="Recovery Manager v0.1", lifespan=lifespan)
    app.state.settings = current_settings
    app.state.session_factory = current_factory

    def principal_dependency(
        x_development_credential: str | None = Header(default=None),
    ) -> Principal:
        return authenticate_development_credential(x_development_credential, current_settings)

    @app.get("/healthz")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": "v0.1"}

    @app.get("/readyz")
    def readiness() -> dict[str, object]:
        try:
            if engine is not None:
                assert_safe_runtime_role(engine)
            with current_factory() as session:
                session.execute(text("SELECT 1"))
            return {"status": "ready", "capabilities": V01_CAPABILITIES}
        except (OperationalError, RuntimeError) as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
            ) from exc

    @app.post("/v1/imports", status_code=status.HTTP_202_ACCEPTED)
    async def ingest(
        request: Request,
        response: Response,
        principal: Principal = Depends(principal_dependency),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        source_name: str | None = Header(default=None, alias="X-Source-Name"),
    ) -> dict[str, object]:
        if idempotency_key is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Idempotency-Key is required"
            )
        raw = await request.body()
        content_type = request.headers.get("content-type", "application/octet-stream")
        try:
            with current_factory() as session, session.begin():
                set_local_tenant(session, principal.org_id)
                result = accept_input(
                    session,
                    principal,
                    raw,
                    content_type,
                    source_name or "http-input",
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
        except OperationalError as exc:
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

    return app


app = create_app()
