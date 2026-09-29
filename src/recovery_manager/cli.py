from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from time import sleep
from uuid import UUID

import typer
from sqlalchemy.orm import Session, sessionmaker

from recovery_manager.benchmark_provisioning import SyntheticRecoverySetup
from recovery_manager.canonical import canonicalize_fixture
from recovery_manager.config import Settings, get_settings
from recovery_manager.db import (
    assert_benchmark_ready,
    assert_runtime_ready,
    make_engine,
    make_session_factory,
    set_local_tenant,
)
from recovery_manager.engine_benchmark import RecoveryEngineBenchmarkAdapter
from recovery_manager.evaluation import EvaluationCase, load_engine_benchmark, metrics, write_report
from recovery_manager.fixtures import load_known_fixture
from recovery_manager.ingestion import accept_input
from recovery_manager.models import RawEnvelope, WorkIntent
from recovery_manager.worker import PollingWorker

app = typer.Typer(help="Recovery Manager deterministic synthetic mechanics.")


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


@app.command("canonicalize-fixture")
def canonicalize_fixture_command(file: Path) -> None:
    """Persist allowlisted fixture rows as deterministic v0.2 canonical records."""
    settings, factory = _dependencies()
    inserted = canonicalize_fixture(factory, settings, file)
    typer.echo(f"canonical_records_inserted={inserted}")


@app.command()
def readiness() -> None:
    _dependencies()
    typer.echo("ready")


@app.command()
def evaluate(
    manifest: Path = Path("data/evaluation/recovery-engine-benchmark.json"),
    output_dir: Path = Path("artifacts/evaluation"),
) -> None:
    """Run the prediction-free benchmark through real PostgreSQL Recovery execution."""
    if not manifest.is_file():
        raise typer.BadParameter("Evaluation manifest does not exist", param_hint="manifest")
    metadata, cases = load_engine_benchmark(manifest)
    settings = get_settings()
    assert_benchmark_ready(settings)
    _, runtime = _dependencies()
    _, worker = _worker_dependencies()
    adapter = RecoveryEngineBenchmarkAdapter(runtime, worker, settings)
    scored: list[EvaluationCase] = []
    executions: list[dict[str, object]] = []
    for case in cases:
        setup = SyntheticRecoverySetup.from_manifest(case.setup)
        execution = adapter.run(setup)
        truth = case.ground_truth
        prediction = execution.prediction
        scored.append(EvaluationCase(
            scenario_id=f"holdout/{case.case_id}", dependency_group=case.case_id,
            dependency_ids=case.dependency_ids, stratum=case.stratum,
            expected_recommendation=str(truth["recommendation"]), expected_amount_minor=truth["amount_minor"],
            predicted_recommendation=prediction.predicted_recommendation, predicted_amount_minor=prediction.predicted_amount_minor,
            expected_opportunity_id=str(truth["opportunity_id"]),
            predicted_opportunity_id=prediction.predicted_opportunity_id,
            expected_obligation_id=str(truth["obligation_id"]),
            predicted_obligation_id=prediction.predicted_obligation_id,
            expected_basis=str(truth["basis"]), predicted_basis=prediction.predicted_basis,
            expected_currency=truth["currency"], predicted_currency=prediction.predicted_currency,
            required_evidence=frozenset(truth["required_evidence_ids"]), predicted_evidence=prediction.predicted_evidence,
        ))
        executions.append({"case_id": case.case_id, "assessment_id": execution.assessment_id,
                           "recommendation": prediction.predicted_recommendation, "amount_minor": prediction.predicted_amount_minor,
                           "currency": prediction.predicted_currency, "basis": prediction.predicted_basis,
                           "stratum": case.stratum, "evidence": sorted(prediction.predicted_evidence)})
    report = {"runner_mode": "real_engine", "benchmark": metadata["benchmark_id"], "schema_version": metadata["schema_version"], "results": metrics(scored), "executions": executions}
    report["engine_benchmarked_revision"] = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], text=True
    ).strip()
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "repair09-results.json"
    markdown_path = output_dir / "repair09-results.md"
    write_report(report, json_path, markdown_path)
    typer.echo(f"manifest={manifest}")
    typer.echo(f"json={json_path}")
    typer.echo(f"markdown={markdown_path}")
    typer.echo(f"cases={report['results']['cases']}")


@app.command()
def demo() -> None:
    """Run the bounded, real-path synthetic demo against an isolated test database.

    This command deliberately delegates fixture setup to the integration harness: it
    uses the worker assessment, guarded publication, export, and freshness paths and
    never inserts a ready assessment or current pointer itself.
    """
    selected = [
        "tests/integration/test_assessment_gate_postgres.py::test_worker_constructor_publishes_trusted_synthetic_two_dollar_control",
        "tests/integration/test_assessment_gate_postgres.py::test_explicitly_reconciled_allocations_drive_residual_outcomes",
        "tests/integration/test_assessment_gate_postgres.py::test_synthetic_readiness_requires_exact_required_evidence_premise",
        "tests/integration/test_assessment_gate_postgres.py::test_unknown_reconciliation_in_either_operand_cannot_be_treated_as_zero",
        "tests/integration/test_assessment_gate_postgres.py::test_export_consumes_real_worker_published_synthetic_assessment",
        "tests/integration/test_assessment_transactions_postgres.py::test_export_rejects_stale_assessment_after_decisive_revision",
    ]
    result = subprocess.run([sys.executable, "-m", "pytest", "-q", *selected], check=False)
    if result.returncode:
        raise typer.Exit(code=result.returncode)
    typer.echo("Case: positive-synthetic — SYNTHETIC_CLAIM_READY USD 2.00; exportable")
    typer.echo("Case: partial/full settlement and already-pursued — residual or non-claim outcome")
    typer.echo("Case: evidence-failure and reconciliation-unknown — REVIEW")
    typer.echo("Case: stale-assessment — historical assessment is non-exportable")
