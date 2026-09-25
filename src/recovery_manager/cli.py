from __future__ import annotations

from pathlib import Path
from uuid import UUID

import typer
from sqlalchemy.orm import Session, sessionmaker

from recovery_manager.config import Settings, get_settings
from recovery_manager.db import (
    assert_safe_runtime_role,
    make_engine,
    make_session_factory,
    set_local_tenant,
)
from recovery_manager.fixtures import load_known_fixture
from recovery_manager.ingestion import accept_input
from recovery_manager.models import RawEnvelope, WorkIntent
from recovery_manager.worker import PollingWorker

app = typer.Typer(help="Recovery Manager v0.1 headless foundation. No recovery decisions exist.")


def _dependencies() -> tuple[Settings, sessionmaker[Session]]:
    settings = get_settings()
    engine = make_engine(settings)
    assert_safe_runtime_role(engine)
    return settings, make_session_factory(engine)


@app.command()
def ingest(
    file: Path, credential: str, idempotency_key: str, content_type: str = "text/csv"
) -> None:
    settings, factory = _dependencies()
    principal = settings.principals().get(credential)
    if principal is None:
        raise typer.BadParameter("Unknown development credential", param_hint="credential")
    with factory() as session, session.begin():
        set_local_tenant(session, principal.org_id)
        result = accept_input(
            session,
            principal,
            file.read_bytes(),
            content_type,
            file.name,
            idempotency_key,
            settings,
        )
    typer.echo(
        f"envelope_id={result.envelope_id} work_id={result.work_id} status={result.validation_status}"
    )


@app.command()
def status(envelope_id: UUID, credential: str) -> None:
    settings, factory = _dependencies()
    principal = settings.principals().get(credential)
    if principal is None:
        raise typer.BadParameter("Unknown development credential", param_hint="credential")
    with factory() as session, session.begin():
        set_local_tenant(session, principal.org_id)
        envelope = session.get(RawEnvelope, envelope_id)
        if envelope is None or envelope.org_id != principal.org_id:
            raise typer.Exit(code=1)
        work = (
            session.query(WorkIntent)
            .filter_by(org_id=principal.org_id, envelope_id=envelope_id)
            .one()
        )
        typer.echo(
            f"state={work.state} validation={envelope.validation_status} sha256={envelope.sha256}"
        )


@app.command("worker-once")
def worker_once(owner: str = "cli-worker") -> None:
    settings, factory = _dependencies()
    worker = PollingWorker(factory, settings, owner)
    seen: set[str] = set()
    for principal in settings.principals().values():
        if principal.org_id not in seen:
            seen.add(principal.org_id)
            result = worker.run_once(principal.org_id)
            if result is not None:
                typer.echo(f"completed={result} org={principal.org_id}")


@app.command("fixture-load")
def fixture_load(file: Path) -> None:
    settings, factory = _dependencies()
    results = load_known_fixture(factory, settings, file)
    typer.echo(f"loaded_rows={len(results)}")


@app.command()
def readiness() -> None:
    _dependencies()
    typer.echo("ready")
