from __future__ import annotations

from pathlib import Path
from time import sleep
from uuid import UUID

import typer
from sqlalchemy.orm import Session, sessionmaker

from recovery_manager.config import Settings, get_settings
from recovery_manager.db import (
    assert_runtime_ready,
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
    assert_runtime_ready(engine, settings, required_role="recovery_app")
    return settings, make_session_factory(engine)


def _worker_dependencies() -> tuple[Settings, sessionmaker[Session]]:
    settings = get_settings()
    engine = make_engine(settings, worker=True)
    assert_runtime_ready(engine, settings, required_role="recovery_worker")
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
    settings, factory = _worker_dependencies()
    worker = PollingWorker(factory, settings, owner)
    seen: set[str] = set()
    for principal in settings.principals().values():
        if principal.org_id not in seen:
            seen.add(principal.org_id)
            result = worker.run_once(principal.org_id)
            if result is not None:
                typer.echo(f"completed={result} org={principal.org_id}")


@app.command("worker-poll")
def worker_poll(
    owner: str = "cli-worker",
    idle_seconds: float = typer.Option(1.0, min=0.1, max=60.0),
    max_iterations: int | None = typer.Option(None, min=1),
) -> None:
    """Poll tenants with bounded idle sleep; Ctrl-C stops without changing work state."""
    settings, factory = _worker_dependencies()
    worker = PollingWorker(factory, settings, owner)
    principals = {principal.org_id for principal in settings.principals().values()}
    iterations = 0
    try:
        while max_iterations is None or iterations < max_iterations:
            processed = False
            for org_id in principals:
                processed = worker.run_once(org_id) is not None or processed
            iterations += 1
            if not processed:
                sleep(idle_seconds)
    except KeyboardInterrupt:
        typer.echo("worker-poll stopped")


@app.command("fixture-load")
def fixture_load(file: Path) -> None:
    settings, factory = _dependencies()
    results = load_known_fixture(factory, settings, file)
    typer.echo(f"loaded_rows={len(results)}")


@app.command()
def readiness() -> None:
    _dependencies()
    typer.echo("ready")
