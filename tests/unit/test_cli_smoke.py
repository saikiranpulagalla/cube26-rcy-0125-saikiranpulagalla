from __future__ import annotations

import json
import os

from typer.testing import CliRunner

from recovery_manager.cli import app
from recovery_manager.config import get_settings


def test_all_headless_commands_render_help() -> None:
    runner = CliRunner()
    for command in (
        "",
        "ingest",
        "status",
        "worker-once",
        "worker-poll",
        "fixture-load",
        "canonicalize-fixture",
        "readiness",
        "evaluate",
        "demo",
    ):
        result = runner.invoke(app, [command, "--help"] if command else ["--help"])
        assert result.exit_code == 0, result.output


def test_evaluate_writes_reproducible_reports(tmp_path, monkeypatch) -> None:
    # This command-level test does not receive pytest's Settings fixture.  Pin
    # every benchmark role to the explicitly configured isolated test endpoint
    # rather than allowing Settings defaults to target localhost:5432.
    monkeypatch.setenv("RECOVERY_DATABASE_URL", os.environ["TEST_RUNTIME_DATABASE_URL"])
    monkeypatch.setenv("RECOVERY_MIGRATION_DATABASE_URL", os.environ["TEST_OWNER_DATABASE_URL"])
    monkeypatch.setenv("RECOVERY_WORKER_DATABASE_URL", os.environ["RECOVERY_WORKER_DATABASE_URL"])
    monkeypatch.setenv("RECOVERY_DEVELOPMENT_MODE", "true")
    monkeypatch.setenv("RECOVERY_BENCHMARK_DATABASE", "true")
    monkeypatch.setenv(
        "RECOVERY_DEV_CREDENTIALS",
        json.dumps({"benchmark-token": {"org_id": "benchmark", "actor_id": "runner", "role": "operator"}}),
    )
    get_settings.cache_clear()
    try:
        runner = CliRunner()
        result = runner.invoke(app, ["evaluate", "--output-dir", str(tmp_path)])
        assert result.exit_code == 0, result.output
        result_json = tmp_path / "repair09-results.json"
        assert result_json.exists()
        assert (tmp_path / "repair09-results.md").exists()
        assert json.loads(result_json.read_text(encoding="utf-8"))["engine_benchmarked_revision"]
    finally:
        get_settings.cache_clear()


def test_evaluate_requires_explicit_benchmark_database_before_writing(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("RECOVERY_DEVELOPMENT_MODE", "true")
    monkeypatch.setenv(
        "RECOVERY_DEV_CREDENTIALS",
        json.dumps({"benchmark-token": {"org_id": "benchmark", "actor_id": "runner", "role": "operator"}}),
    )
    monkeypatch.setenv("RECOVERY_BENCHMARK_DATABASE", "false")
    get_settings.cache_clear()
    try:
        result = CliRunner().invoke(app, ["evaluate", "--output-dir", str(tmp_path)])
        assert result.exit_code != 0
        assert not (tmp_path / "repair09-results.json").exists()
        assert not (tmp_path / "repair09-results.md").exists()
    finally:
        get_settings.cache_clear()
